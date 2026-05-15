from __future__ import annotations

import http.client
import math
import re
import socket
import ssl
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .storage import SqliteStorage


PING_PATTERN = re.compile(r"(?:tempo|time)\s*[=<]?\s*(\d+(?:[.,]\d+)?)\s*ms", re.IGNORECASE)


def parse_ping_times(output: str) -> list[float]:
    return [float(value.replace(",", ".")) for value in PING_PATTERN.findall(output)]


def calculate_jitter(latencies: list[float]) -> float | None:
    if len(latencies) < 2:
        return None

    deltas = [abs(current - previous) for previous, current in zip(latencies, latencies[1:])]
    return round(sum(deltas) / len(deltas), 2)


def _single_probe_result(online: bool, latency_ms: float | None) -> dict[str, Any]:
    rounded_latency = round(latency_ms, 2) if latency_ms is not None else None
    return {
        "online": online,
        "sent": 1,
        "received": 1 if online else 0,
        "packet_loss_pct": 0.0 if online else 100.0,
        "avg_latency_ms": rounded_latency,
        "min_latency_ms": rounded_latency,
        "max_latency_ms": rounded_latency,
        "jitter_ms": None,
        "samples_ms": [rounded_latency] if rounded_latency is not None else [],
    }


def ping_host(host: str, count: int, timeout_ms: int) -> dict[str, Any]:
    if sys.platform == "win32":
        command = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
    else:
        timeout_seconds = max(1, math.ceil(timeout_ms / 1000))
        command = ["ping", "-c", str(count), "-W", str(timeout_seconds), host]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except OSError:
        return {
            "online": False,
            "sent": count,
            "received": 0,
            "packet_loss_pct": 100.0,
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
            "jitter_ms": None,
            "samples_ms": [],
        }

    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    latencies = parse_ping_times(output)
    received = len(latencies)
    sent = count
    packet_loss_pct = round(((sent - received) / sent) * 100, 2)
    online = received > 0

    if latencies:
        avg_latency = round(statistics.fmean(latencies), 2)
        min_latency = round(min(latencies), 2)
        max_latency = round(max(latencies), 2)
        jitter = calculate_jitter(latencies)
    else:
        avg_latency = None
        min_latency = None
        max_latency = None
        jitter = None

    return {
        "online": online,
        "sent": sent,
        "received": received,
        "packet_loss_pct": packet_loss_pct,
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "jitter_ms": jitter,
        "samples_ms": latencies,
    }


def tcp_probe(host: str, port: int, timeout_ms: int) -> dict[str, Any]:
    timeout_seconds = max(timeout_ms / 1000, 0.1)
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            latency_ms = (time.perf_counter() - start) * 1000
            return _single_probe_result(True, latency_ms)
    except OSError:
        return _single_probe_result(False, None)


def http_probe(
    host: str,
    scheme: str,
    timeout_ms: int,
    path: str = "/",
    port: int | None = None,
) -> dict[str, Any]:
    timeout_seconds = max(timeout_ms / 1000, 0.1)
    connection_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    context = ssl._create_unverified_context() if scheme == "https" else None
    kwargs: dict[str, Any] = {"timeout": timeout_seconds}
    if context is not None:
        kwargs["context"] = context

    connection = connection_cls(host, port=port, **kwargs)
    start = time.perf_counter()

    try:
        connection.request("GET", path or "/", headers={"User-Agent": "PulseBoard/1.0"})
        response = connection.getresponse()
        response.read(1)
        latency_ms = (time.perf_counter() - start) * 1000
        return _single_probe_result(True, latency_ms)
    except http.client.HTTPException:
        return _single_probe_result(False, None)
    except OSError:
        return _single_probe_result(False, None)
    finally:
        connection.close()


def measure_service(service: dict[str, Any], ping_count: int, timeout_ms: int) -> dict[str, Any]:
    check_type = str(service.get("checkType") or "ping").lower()
    host = str(service["host"])

    if check_type == "tcp":
        return tcp_probe(host, int(service.get("port") or 443), timeout_ms)
    if check_type in {"http", "https"}:
        return http_probe(
            host=host,
            scheme=check_type,
            timeout_ms=timeout_ms,
            path=str(service.get("requestPath") or "/"),
            port=service.get("port"),
        )
    return ping_host(host, ping_count, timeout_ms)


def resolve_status(online: bool, avg_latency_ms: float | None, threshold: int | None) -> str:
    if not online:
        return "offline"
    if threshold is not None and avg_latency_ms is not None and avg_latency_ms >= threshold:
        return "degraded"
    return "online"


class MonitorService:
    """Background service that updates host measurements periodically."""

    def __init__(
        self,
        storage: SqliteStorage,
        interval_seconds: int = 15,
        ping_count: int = 4,
        timeout_ms: int = 1000,
        stability_window: int = 20,
        max_entries: int = 1000,
    ) -> None:
        self.storage = storage
        self.interval_seconds = interval_seconds
        self.ping_count = ping_count
        self.timeout_ms = timeout_ms
        self.stability_window = stability_window
        self.max_entries = max_entries
        self.current_status: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._cycle_lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_cycle()
            self._stop_event.wait(self.interval_seconds)

    def _reset_history_if_due(self) -> None:
        now = datetime.now(timezone.utc)
        last_reset = self.storage.get_last_reset()

        if last_reset is None:
            self.storage.set_last_reset(now)
            return

        if (now - last_reset).total_seconds() < 24 * 3600:
            return

        if self.storage.has_history():
            self.storage.clear_history()
        self.storage.set_last_reset(now)

    def run_cycle(self, service_ids: set[str] | None = None) -> None:
        requested_ids = set(service_ids) if service_ids else None

        with self._cycle_lock:
            self._reset_history_if_due()
            services = self.storage.load_services()
            existing_ids = {str(service["id"]) for service in services}

            for stale_id in list(self.current_status):
                if stale_id not in existing_ids:
                    self.current_status.pop(stale_id, None)

            for service in services:
                service_id = str(service["id"])
                if requested_ids is not None and service_id not in requested_ids:
                    continue
                if not all(key in service for key in ("id", "name", "host")):
                    continue

                result = measure_service(service, self.ping_count, self.timeout_ms)
                entry = self._build_entry(service, result)
                persisted_entry = self.storage.append_history_entry(
                    entry,
                    max_entries=self.max_entries,
                    stability_window=self.stability_window,
                )
                if persisted_entry is None:
                    continue

                self.current_status[service_id] = persisted_entry

    def _build_entry(self, service: dict[str, Any], probe_result: dict[str, Any]) -> dict[str, Any]:
        timestamp = datetime.now(timezone.utc).isoformat()
        threshold = service.get("threshold")

        return {
            "timestamp": timestamp,
            "service": {
                "id": service["id"],
                "name": service["name"],
                "host": service["host"],
                "threshold": threshold,
                "checkType": service.get("checkType", "ping"),
                "port": service.get("port"),
                "requestPath": service.get("requestPath", "/"),
            },
            "online": probe_result["online"],
            "status": resolve_status(probe_result["online"], probe_result["avg_latency_ms"], threshold),
            "sent": probe_result["sent"],
            "received": probe_result["received"],
            "packet_loss_pct": probe_result["packet_loss_pct"],
            "avg_latency_ms": probe_result["avg_latency_ms"],
            "min_latency_ms": probe_result["min_latency_ms"],
            "max_latency_ms": probe_result["max_latency_ms"],
            "jitter_ms": probe_result["jitter_ms"],
            "samples_ms": probe_result["samples_ms"],
            "stability_pct": 0.0,
        }
