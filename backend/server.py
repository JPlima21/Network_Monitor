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

# Essas instancias sao compartilhadas por todas as requisicoes do processo.
storage = SqliteStorage()
monitor = MonitorService(storage=storage)


def read_dashboard() -> dict[str, object]:
    """Le o cadastro e o historico e devolve o payload consolidado do painel."""
    services = storage.load_services()
    history = storage.load_history()
    return build_dashboard_data(services, history)


class RequestHandler(BaseHTTPRequestHandler):
    """Handler HTTP simples que serve API JSON e arquivos estaticos."""

    server_version = "PulseBoardPython/1.0"

    def do_GET(self) -> None:
        # GET concentra as consultas usadas pelo frontend para montar a tela.
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
        # POST cria um novo servico e ja dispara uma leitura inicial.
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
        # O PUT pode atualizar um servico existente ou persistir a ordem dos cards.
        parsed = urlparse(self.path)
        if parsed.path == "/api/services/reorder":
            payload = self._read_json_body()
            service_ids = payload.get("serviceIds")
            if not isinstance(service_ids, list) or not all(isinstance(item, str) for item in service_ids):
                self._send_json(
                    {"error": "serviceIds must be a list of strings"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            try:
                services = storage.reorder_services(service_ids)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return

            self._send_json({"message": "Service order updated", "services": services})
            return

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
        # Remove cadastro e historico associado do servico.
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
        # Desliga o log padrao do BaseHTTPRequestHandler para manter o terminal limpo.
        return

    def _read_json_body(self) -> dict[str, object]:
        # Se o cliente enviar corpo invalido, a API responde com payload vazio
        # e a validacao da rota decide o erro apropriado.
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b"{}"

        try:
            return json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        # Centraliza serializacao e headers JSON da API.
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, request_path: str) -> None:
        # Tudo que nao e rota da API cai aqui para servir HTML/CSS/JS.
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        file_path = (PUBLIC_DIR / relative).resolve()

        try:
            # Impede acesso a arquivos fora de /public via path traversal.
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
    """Inicializa o monitor e sobe o servidor HTTP principal."""
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
