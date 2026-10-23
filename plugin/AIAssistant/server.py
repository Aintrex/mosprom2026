# -*- coding: utf-8 -*-
import json
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from .agent.service import ConflictError, NotFoundError

MAX_BODY_BYTES = 2 * 1024 * 1024
STATIC_WHITELIST = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8")
}
ALLOWED_HOSTS_RE = re.compile(r"^(127\.0\.0\.1|localhost)(:\d+)?$", re.IGNORECASE)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_handler(web_dir, agent_service, mcp_handler, slicer_adapter, config_store=None):
    class RequestHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _check_security(self):
            host = self.headers.get("Host", "")
            if not ALLOWED_HOSTS_RE.match(host):
                self._send_json({"ok": False, "error": "Forbidden Host"}, status=403)
                return False

            origin = self.headers.get("Origin")
            if origin:
                parsed = urllib.parse.urlparse(origin)
                if parsed.scheme not in ("http", "https") or not ALLOWED_HOSTS_RE.match(parsed.netloc or ""):
                    self._send_json({"ok": False, "error": "Forbidden Origin"}, status=403)
                    return False
            return True

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self):
            length_str = self.headers.get("Content-Length", "0")
            try:
                length = int(length_str)
            except ValueError:
                self._send_json({"ok": False, "error": "Invalid Content-Length"}, status=400)
                return None

            if length > MAX_BODY_BYTES:
                self._send_json({"ok": False, "error": "Payload Too Large"}, status=413)
                return None

            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                self._send_json({"ok": False, "error": "Invalid JSON"}, status=400)
                return None

            if not isinstance(data, dict):
                self._send_json({"ok": False, "error": "JSON body must be an object"}, status=400)
                return None
            return data

        def do_GET(self):
            if not self._check_security():
                return

            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path in STATIC_WHITELIST:
                filename, content_type = STATIC_WHITELIST[path]
                full_path = os.path.join(web_dir, filename)
                if not os.path.isfile(full_path):
                    self._send_json({"ok": False, "error": "Static file not found"}, status=404)
                    return
                with open(full_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

            if path == "/api/status":
                status = agent_service.get_status()
                try:
                    status["slicer"] = slicer_adapter.get_printer_state()
                except Exception as e:
                    status["slicer"] = {"error": str(e)}
                self._send_json(status, status=200)
                return

            m_sess = re.match(r"^/api/sessions/([A-Za-z0-9_\-]+)$", path)
            if m_sess:
                session_id = m_sess.group(1)
                try:
                    self._send_json(agent_service.get_session(session_id), status=200)
                except NotFoundError as e:
                    self._send_json({"ok": False, "error": str(e)}, status=404)
                return

            m_ev = re.match(r"^/api/sessions/([A-Za-z0-9_\-]+)/events$", path)
            if m_ev:
                session_id = m_ev.group(1)
                qs = urllib.parse.parse_qs(parsed.query)
                after_raw = (qs.get("after") or ["0"])[0]
                try:
                    after_seq = max(0, int(after_raw))
                except ValueError:
                    after_seq = 0
                try:
                    ev_data = agent_service.get_events(session_id, after_seq=after_seq)
                    try:
                        ev_data["slicer"] = slicer_adapter.get_printer_state()
                    except Exception:
                        pass
                    self._send_json(ev_data, status=200)
                except NotFoundError as e:
                    self._send_json({"ok": False, "error": str(e)}, status=404)
                return

            self._send_json({"ok": False, "error": "Not Found"}, status=404)

        def do_POST(self):
            if not self._check_security():
                return

            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            data = self._read_json_body()
            if data is None:
                return

            if path == "/mcp":
                rpc_resp = mcp_handler.handle_request(data)
                if rpc_resp is None:
                    self.send_response(204)
                    self.end_headers()
                else:
                    self._send_json(rpc_resp, status=200)
                return

            if path == "/api/settings" and config_store is not None:
                config_store.update_from_dict(data)
                self._send_json(agent_service.get_status(), status=200)
                return

            if path == "/api/sessions":
                sess = agent_service.create_session()
                self._send_json(sess, status=201)
                return

            m_msg = re.match(r"^/api/sessions/([A-Za-z0-9_\-]+)/messages$", path)
            if m_msg:
                session_id = m_msg.group(1)
                try:
                    res = agent_service.submit_message(
                        session_id=session_id,
                        text=data.get("text", ""),
                        client_message_id=data.get("client_message_id"),
                        images=data.get("images")
                    )
                    self._send_json(res, status=202)
                except ValueError as e:
                    self._send_json({"ok": False, "error": str(e)}, status=400)
                except NotFoundError as e:
                    self._send_json({"ok": False, "error": str(e)}, status=404)
                except ConflictError as e:
                    self._send_json({"ok": False, "error": str(e)}, status=409)
                return

            self._send_json({"ok": False, "error": "Not Found"}, status=404)

    return RequestHandler