from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .dashboard import build_dashboard_data
from .monitor import MonitorService
from .storage import ALLOWED_CHECK_TYPES, PUBLIC_DIR, SqliteStorage


HOST = "127.0.0.1"
PORT = 3000

storage = SqliteStorage()
monitor = MonitorService(storage=storage)


def read_dashboard() -> dict[str, object]:
    services = storage.load_services()
    history = storage.load_history()
    return build_dashboard_data(services, history)


def _coerce_optional_int(value: object, field_name: str) -> int | None:
    if value in (None, ""):
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid integer") from exc

    if not (1 <= parsed <= 65535):
        raise ValueError(f"{field_name} must be between 1 and 65535")
    return parsed


def _normalize_service_payload(payload: dict[str, object], partial: bool) -> dict[str, object]:
    normalized: dict[str, object] = {}

    if not partial or "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Name is required")
        normalized["name"] = name

    if not partial or "host" in payload:
        host = str(payload.get("host") or "").strip()
        if not host:
            raise ValueError("Host is required")
        normalized["host"] = host

    if not partial or "threshold" in payload:
        threshold_raw = payload.get("threshold")
        try:
            normalized["threshold"] = int(threshold_raw) if threshold_raw not in (None, "") else 100
        except (TypeError, ValueError) as exc:
            raise ValueError("Threshold must be a valid integer") from exc

    if not partial or "imageUrl" in payload:
        normalized["imageUrl"] = str(payload.get("imageUrl") or "").strip()

    if not partial or "checkType" in payload:
        check_type = str(payload.get("checkType") or "ping").strip().lower()
        if check_type not in ALLOWED_CHECK_TYPES:
            raise ValueError(f"Check type must be one of: {', '.join(sorted(ALLOWED_CHECK_TYPES))}")
        normalized["checkType"] = check_type

    if not partial or "port" in payload:
        normalized["port"] = _coerce_optional_int(payload.get("port"), "Port")

    if not partial or "requestPath" in payload:
        normalized["requestPath"] = str(payload.get("requestPath") or "").strip()

    return normalized


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
        try:
            normalized = _normalize_service_payload(payload, partial=False)
            service = storage.add_service(
                name=str(normalized["name"]),
                host=str(normalized["host"]),
                threshold=int(normalized["threshold"]),
                image_url=str(normalized["imageUrl"]),
                check_type=str(normalized["checkType"]),
                port=normalized.get("port"),
                request_path=str(normalized.get("requestPath") or "/"),
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        monitor.run_cycle({service["id"]})
        self._send_json({"message": "Service added", "service": service}, status=HTTPStatus.CREATED)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/services/"):
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        service_id = parsed.path.rsplit("/", 1)[-1]
        payload = self._read_json_body()

        try:
            normalized = _normalize_service_payload(payload, partial=True)
            updated = storage.update_service(service_id, normalized)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

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
