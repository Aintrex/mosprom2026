import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit

from .agent.mcp import dispatch, error

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
                payload, ensure_ascii=False).encode("utf-8")
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
                self.respond(403, {"error": "Local requests only"})
                return
            path = urlsplit(self.path).path
            if path == "/api/health":
                self.respond(200, {"status": "ok", "mode": "scaffold"})
            elif path in STATIC_FILES:
                filename, content_type = STATIC_FILES[path]
                with open(os.path.join(WEB_DIR, filename), "rb") as stream:
                    self.respond(200, stream.read(), content_type)
            else:
                self.respond(404, {"error": "Not found"})

        def do_POST(self):
            if not self.is_local_request():
                self.respond(403, {"error": "Local requests only"})
                return
            if self.path not in ("/api/message", "/mcp"):
                self.respond(404, {"error": "Not found"})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.respond(415, {"error": "Expected application/json"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY_BYTES:
                    self.respond(413, {"error": "Invalid body size"})
                    return
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeError):
                payload = error(None, -32700, "Parse error") if self.path == "/mcp" else {"error": "Invalid JSON"}
                self.respond(400, payload)
                return
            if self.path == "/mcp":
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

        def log_message(self, format, *args):
            # Do not log message bodies, credentials or local file paths.
            return

    return ChatServer(("127.0.0.1", port), Handler)
