from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .dashboard import build_dashboard_data
from .monitor import MonitorService
from .storage import PUBLIC_DIR, SqliteStorage


HOST = "127.0.0.1"
PORT = 3000

storage = SqliteStorage()
monitor = MonitorService(storage=storage)


def read_dashboard() -> dict[str, object]:
    services = storage.load_services()
    history = storage.load_history()
    return build_dashboard_data(services, history)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "PulseBoardPython/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/dashboard":
            self._send_json(read_dashboard())
            return

        if parsed.path == "/api/status":
            self._send_json(read_dashboard()["services"])
            return

        if parsed.path == "/api/history":
            self._send_json(read_dashboard()["history"])
            return

        if parsed.path == "/api/services":
            self._send_json(storage.load_services())
            return

        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/services":
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        payload = self._read_json_body()
        name = str(payload.get("name") or "").strip()
        host = str(payload.get("host") or "").strip()
        if not name or not host:
            self._send_json({"error": "Name and host are required"}, status=HTTPStatus.BAD_REQUEST)
            return

        threshold_raw = payload.get("threshold")
        threshold = int(threshold_raw) if threshold_raw not in (None, "") else 100
        image_url = str(payload.get("imageUrl") or "").strip()

        service = storage.add_service(
            name=name,
            host=host,
            threshold=threshold,
            image_url=image_url,
        )
        monitor.run_cycle({service["id"]})
        self._send_json({"message": "Service added", "service": service}, status=HTTPStatus.CREATED)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/services/"):
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        service_id = parsed.path.rsplit("/", 1)[-1]
        payload = self._read_json_body()
        updated = storage.update_service(service_id, payload)

        if updated is None:
            self._send_json({"error": "Service not found"}, status=HTTPStatus.NOT_FOUND)
            return

        monitor.run_cycle({service_id})
        self._send_json({"message": "Service updated", "service": updated})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/services/"):
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        service_id = parsed.path.rsplit("/", 1)[-1]
        removed = storage.delete_service(service_id)

        if not removed:
            self._send_json({"error": "Service not found"}, status=HTTPStatus.NOT_FOUND)
            return

        monitor.current_status.pop(service_id, None)
        self._send_json({"message": "Service removed"})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b"{}"

        try:
            return json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        file_path = (PUBLIC_DIR / relative).resolve()

        try:
            file_path.relative_to(PUBLIC_DIR.resolve())
        except ValueError:
            self._send_json({"error": "Forbidden"}, status=HTTPStatus.FORBIDDEN)
            return

        if not file_path.exists() or not file_path.is_file():
            self._send_json({"error": "File not found"}, status=HTTPStatus.NOT_FOUND)
            return

        content = file_path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(file_path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def run(host: str = HOST, port: int = PORT) -> None:
    monitor.start()
    server = ThreadingHTTPServer((host, port), RequestHandler)
    print(f"Server running at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        server.server_close()
