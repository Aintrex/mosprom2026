import json
import os
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit, parse_qs, quote

from .agent.mcp import dispatch, error
from .agent.service import APIError
from .uploads import UploadError
from .downloads import DownloadError

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_BODY_BYTES = 64 * 1024


class ChatServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def create_server(service, port=0):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def respond(self, status, payload, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(
                payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def is_local_request(self):
            port = self.server.server_port
            hosts = ("127.0.0.1:%d" % port, "localhost:%d" % port)
            origin = self.headers.get("Origin")
            return (self.headers.get("Host") in hosts and
                    (origin is None or origin in tuple("http://" + h for h in hosts)))

        def do_GET(self):
            if not self.is_local_request():
                self.forbidden()
                return
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith("/api/files/"):
                if parsed.query or parsed.fragment or len(path.split("/")) != 4:
                    self.api_error(APIError(404, "not_found", "Маршрут скачивания не найден"))
                    return
                self.download_file(path.split("/")[3])
            elif path == "/api/health":
                self.respond(200, {"status": "ok", "mode": "scaffold"})
            elif path in ("/api/status", "/api/events", "/api/sessions") or path.startswith("/api/sessions/"):
                try:
                    if service.closed:
                        raise APIError(503, "service_stopped", "Плагин завершает работу")
                    if path == "/api/status" and not parsed.query:
                        result = service.get_status()
                    elif path == "/api/sessions" and not parsed.query:
                        result = service.list_sessions()
                    elif path == "/api/events":
                        query = parse_qs(parsed.query, keep_blank_values=True)
                        if (set(query) - {"session_id", "after"} or len(query.get("session_id", [])) != 1 or
                                len(query.get("after", ["0"])) != 1):
                            raise APIError(400, "invalid_arguments", "Нужны session_id и необязательный after")
                        try:
                            after = int(query.get("after", ["0"])[0])
                        except ValueError:
                            raise APIError(400, "invalid_arguments", "after должен быть целым >=0")
                        result = service.get_events(query["session_id"][0], after)
                    elif len(path.split("/")) == 4 and not parsed.query:
                        result = service.get_session(path.split("/")[3])
                    else:
                        raise APIError(404, "not_found", "Маршрут не найден")
                    self.respond(200, result)
                except APIError as exc:
                    self.api_error(exc)
                except Exception as exc:
                    logging.getLogger(__name__).error("REST read: %s", type(exc).__name__)
                    self.api_error(APIError(500, "internal_error", "Не удалось прочитать состояние"))
            elif path in STATIC_FILES:
                filename, content_type = STATIC_FILES[path]
                with open(os.path.join(WEB_DIR, filename), "rb") as stream:
                    self.respond(200, stream.read(), content_type)
            else:
                self.not_found()

        def do_DELETE(self):
            if not self.is_local_request():
                self.forbidden()
                return
            parsed = urlsplit(self.path)
            parts = parsed.path.split("/")
            if parsed.query or parsed.fragment or len(parts) != 4 or parts[:3] != ["", "api", "sessions"]:
                self.not_found()
                return
            try:
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
                    raise APIError(400, "invalid_arguments", "Удаление диалога не принимает тело")
                self.respond(200, service.delete_session(parts[3]))
            except APIError as exc:
                self.api_error(exc)
            except Exception as exc:
                logging.getLogger(__name__).error("REST delete: %s", type(exc).__name__)
                self.api_error(APIError(500, "internal_error", "Удаление не подтверждено; перечитайте список"))

        def do_POST(self):
            if not self.is_local_request():
                self.forbidden()
                return
            parsed = urlsplit(self.path)
            path, parts = parsed.path, parsed.path.split("/")
            if path == "/api/files" and not parsed.query:
                self.upload_file()
                return
            rest = path == "/api/sessions" or (len(parts) == 5 and parts[:3] == ["", "api", "sessions"] and parts[4] == "messages")
            if parsed.query or path not in ("/api/message", "/mcp") and not rest:
                self.not_found()
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.api_error(APIError(415, "unsupported_media_type", "Expected application/json")) if rest else self.respond(415, {"error": "Expected application/json"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY_BYTES:
                    self.api_error(APIError(413, "payload_too_large", "Invalid body size")) if rest else self.respond(413, {"error": "Invalid body size"})
                    return
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeError, OSError):
                if rest:
                    self.api_error(APIError(400, "invalid_request", "Некорректный JSON"))
                    return
                payload = error(None, -32700, "Parse error") if path == "/mcp" else {"error": "Invalid JSON"}
                self.respond(400, payload)
                return
            if service.closed:
                self.api_error(APIError(503, "service_stopped", "Плагин завершает работу"))
                return
            if rest:
                try:
                    if path == "/api/sessions":
                        if not isinstance(data, dict) or data:
                            raise APIError(400, "invalid_arguments", "Создание диалога принимает {}")
                        self.respond(201, service.create_session())
                    else:
                        self.respond(202, service.submit_message(parts[3], data))
                except APIError as exc:
                    self.api_error(exc)
                except Exception as exc:
                    logging.getLogger(__name__).error("REST submit: %s", type(exc).__name__)
                    self.api_error(APIError(500, "internal_error", "Не удалось обработать запрос"))
                return
            if path == "/mcp":
                result = dispatch(data, service.tools)
                if result is None:
                    self.respond(204, b"")
                else:
                    self.respond(200, result)
                return
            text = data.get("text") if isinstance(data, dict) else None
            if not isinstance(text, str) or not text.strip() or len(text) > 12000:
                self.respond(400, {"error": "Expected 1-12000 characters of text"})
                return
            self.respond(200, service.message(text.strip()))

        def api_error(self, exc):
            self.respond(exc.status, {"error": {"code": exc.code, "message": exc.message}})

        def download_file(self, file_id):
            stream, started = None, False
            self.close_connection = True
            try:
                if service.closed:
                    raise APIError(503, "service_stopped", "Сервис закрывается")
                stream, artifact = service.open_download(file_id)
                self.send_response(200)
                self.send_header("Content-Type", "model/3mf" if artifact["kind"] == "project" else "application/octet-stream")
                self.send_header("Content-Length", str(artifact["size_bytes"]))
                name = quote(artifact["file_name"], safe="")
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + name)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
                started = True
                self.end_headers()
                remaining = artifact["size_bytes"]
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        raise OSError("Published file became truncated")
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (APIError, DownloadError) as exc:
                if not started:
                    self.api_error(exc)
            except (OSError, ValueError) as exc:
                logging.getLogger(__name__).info("Download interrupted: %s", type(exc).__name__)
                if not started:
                    self.api_error(APIError(503, "download_unavailable", "Не удалось начать скачивание"))
            except Exception as exc:
                logging.getLogger(__name__).error("Download: %s", type(exc).__name__)
                if not started:
                    self.api_error(APIError(503, "download_unavailable", "Скачивание не подтверждено"))
            finally:
                if stream is not None:
                    stream.close()

        def upload_file(self):
            self.close_connection = True
            try:
                if service.closed:
                    raise APIError(503, "service_stopped", "Плагин завершает работу")
                lengths = self.headers.get_all("Content-Length", [])
                if (self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1 or
                        not lengths[0] or any(c not in "0123456789" for c in lengths[0]) or len(lengths[0]) > 12):
                    raise APIError(400, "invalid_upload", "Нужен один Content-Length, chunked не поддерживается")
                if len(self.headers.get_all("Content-Type", [])) != 1:
                    raise APIError(415, "unsupported_media_type", "Нужен один multipart/form-data Content-Type")
                result = service.file_store.upload(self.rfile, int(lengths[0]), self.headers["Content-Type"])
                self.respond(201, result)
            except (APIError, UploadError) as exc:
                self.api_error(exc)
            except Exception as exc:
                logging.getLogger(__name__).error("Upload handler: %s", type(exc).__name__)
                self.api_error(APIError(500, "upload_failed", "Не удалось обработать вложение"))

        def new_api(self):
            path = urlsplit(self.path).path
            return path.startswith("/api/") and path not in ("/api/health", "/api/message")

        def forbidden(self):
            if self.new_api():
                self.api_error(APIError(403, "forbidden_origin", "Разрешён только origin текущего локального сервера"))
            else:
                self.respond(403, {"error": "Local requests only"})

        def not_found(self):
            if self.new_api():
                self.api_error(APIError(404, "not_found", "Маршрут не найден"))
            else:
                self.respond(404, {"error": "Not found"})

        def log_message(self, format, *args):
            # Do not log message bodies, credentials or local file paths.
            return

    return ChatServer(("127.0.0.1", port), Handler)
