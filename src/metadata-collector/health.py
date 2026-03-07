"""Minimal HTTP health endpoint for K8s liveness/readiness probes."""

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/healthz", "/readyz"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


class HealthServer:
    def __init__(self, port: int = 8080):
        self._port = port
        self._server = HTTPServer(("", port), _Handler)

    def start(self):
        t = threading.Thread(target=self._server.serve_forever, daemon=True)
        t.start()
