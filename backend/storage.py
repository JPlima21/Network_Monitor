from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PUBLIC_DIR = BASE_DIR / "public"
DB_FILE = DATA_DIR / "storage.db"
SERVICES_FILE = DATA_DIR / "services.json"
PYTHON_HISTORY_FILE = DATA_DIR / "python_history.json"
LEGACY_HISTORY_FILE = DATA_DIR / "history.json"
LAST_RESET_FILE = DATA_DIR / "last_reset.txt"

DEFAULT_SERVICES = [
    {
        "id": "dns-google",
        "name": "DNS Google",
        "host": "8.8.8.8",
        "threshold": 80,
        "imageUrl": "",
    },
    {
        "id": "cloudflare",
        "name": "Cloudflare DNS",
        "host": "1.1.1.1",
        "threshold": 80,
        "imageUrl": "",
    },
]


def _slugify_name(name: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in name.strip())
    compact = "-".join(part for part in normalized.split("-") if part)
    return compact or "service"


class SqliteStorage:
    """SQLite-backed storage with a process-wide lock."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._ensure_tables()
        self._migrate_from_json()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def _execute(
        self,
        query: str,
        params: tuple[Any, ...] = (),
        commit: bool = False,
    ) -> sqlite3.Cursor:
        import time

        with self._lock:
            for attempt in range(5):
                try:
                    cursor = self._connection.execute(query, params)
                    if commit:
                        self._connection.commit()
                    return cursor
                except sqlite3.OperationalError as exc:
                    if "database is locked" in str(exc) and attempt < 4:
                        time.sleep(0.1 * (attempt + 1))
                        continue
                    raise

    def _ensure_tables(self) -> None:
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                image_url TEXT NOT NULL DEFAULT ''
            )
            """,
            commit=True,
        )
        self._ensure_service_columns()
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                service_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                online INTEGER NOT NULL,
                status TEXT NOT NULL,
                sent INTEGER NOT NULL,
                received INTEGER NOT NULL,
                packet_loss_pct REAL NOT NULL,
                avg_latency_ms REAL,
                min_latency_ms REAL,
                max_latency_ms REAL,
                jitter_ms REAL,
                samples_ms TEXT NOT NULL,
                stability_pct REAL NOT NULL,
                service_threshold INTEGER,
                FOREIGN KEY(service_id) REFERENCES services(id) ON DELETE CASCADE
            )
            """,
            commit=True,
        )
        self._ensure_history_columns()

    def _ensure_service_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._execute("PRAGMA table_info(services)").fetchall()
        }

        if "image_url" not in columns:
            self._execute(
                "ALTER TABLE services ADD COLUMN image_url TEXT NOT NULL DEFAULT ''",
                commit=True,
            )

    def _ensure_history_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._execute("PRAGMA table_info(history)").fetchall()
        }

        if "service_threshold" not in columns:
            self._execute(
                "ALTER TABLE history ADD COLUMN service_threshold INTEGER",
                commit=True,
            )

    def _read_json(self, file_path: Path, default: Any) -> Any:
        if not file_path.exists():
            return default

        try:
            with file_path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError):
            return default

    def _migrate_from_json(self) -> None:
        services = self.load_services()
        if not services:
            services = self._read_json(SERVICES_FILE, list(DEFAULT_SERVICES))
            services = services if isinstance(services, list) and services else list(DEFAULT_SERVICES)
            self.save_services(services)

        if self.has_history():
            return

        history = self._read_json(PYTHON_HISTORY_FILE, None)
        if history is None:
            history = self._read_json(LEGACY_HISTORY_FILE, {})

        if isinstance(history, dict) and history:
            self.save_history(history)

    def load_services(self) -> list[dict[str, Any]]:
        cursor = self._execute(
            "SELECT id, name, host, threshold, image_url FROM services ORDER BY name ASC"
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "host": row["host"],
                "threshold": row["threshold"],
                "imageUrl": row["image_url"] or "",
            }
            for row in cursor.fetchall()
        ]

    def save_services(self, services: list[dict[str, Any]]) -> None:
        with self._lock:
            self._connection.executemany(
                """
                INSERT INTO services (id, name, host, threshold, image_url)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    host = excluded.host,
                    threshold = excluded.threshold,
                    image_url = excluded.image_url
                """,
                [
                    (
                        str(service["id"]),
                        str(service["name"]),
                        str(service["host"]),
                        int(service.get("threshold", 100)),
                        str(service.get("imageUrl", "") or ""),
                    )
                    for service in services
                ],
            )
            self._connection.commit()

    def add_service(
        self,
        name: str,
        host: str,
        threshold: int = 100,
        image_url: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            existing_ids = {
                row["id"]
                for row in self._connection.execute("SELECT id FROM services").fetchall()
            }
            base = _slugify_name(name)
            service_id = base
            index = 2

            while service_id in existing_ids:
                service_id = f"{base}-{index}"
                index += 1

            service = {
                "id": service_id,
                "name": name,
                "host": host,
                "threshold": threshold,
                "imageUrl": image_url,
            }
            self._connection.execute(
                """
                INSERT INTO services (id, name, host, threshold, image_url)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    service["id"],
                    service["name"],
                    service["host"],
                    service["threshold"],
                    service["imageUrl"],
                ),
            )
            self._connection.commit()
            return service

    def update_service(self, service_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id, name, host, threshold, image_url FROM services WHERE id = ?",
                (service_id,),
            ).fetchone()
            if row is None:
                return None

            updated = {
                "id": row["id"],
                "name": row["name"],
                "host": row["host"],
                "threshold": row["threshold"],
                "imageUrl": row["image_url"] or "",
            }

            if "name" in payload:
                name = str(payload.get("name") or "").strip()
                if name:
                    updated["name"] = name
            if "host" in payload:
                host = str(payload.get("host") or "").strip()
                if host:
                    updated["host"] = host
            if "threshold" in payload:
                threshold = payload.get("threshold")
                updated["threshold"] = int(threshold) if threshold not in (None, "") else 100
            if "imageUrl" in payload:
                updated["imageUrl"] = str(payload.get("imageUrl") or "").strip()

            self._connection.execute(
                """
                UPDATE services
                SET name = ?, host = ?, threshold = ?, image_url = ?
                WHERE id = ?
                """,
                (
                    updated["name"],
                    updated["host"],
                    updated["threshold"],
                    updated["imageUrl"],
                    service_id,
                ),
            )
            self._connection.commit()
            return updated

    def load_history(self) -> dict[str, list[dict[str, Any]]]:
        cursor = self._execute(
            "SELECT * FROM history ORDER BY service_id, timestamp ASC"
        )
        history: dict[str, list[dict[str, Any]]] = {}

        for row in cursor.fetchall():
            service_id = row["service_id"]
            history.setdefault(service_id, []).append(
                {
                    "timestamp": row["timestamp"],
                    "service": {
                        "id": service_id,
                        "name": None,
                        "host": None,
                        "threshold": row["service_threshold"],
                    },
                    "online": bool(row["online"]),
                    "status": row["status"],
                    "sent": row["sent"],
                    "received": row["received"],
                    "packet_loss_pct": row["packet_loss_pct"],
                    "avg_latency_ms": row["avg_latency_ms"],
                    "min_latency_ms": row["min_latency_ms"],
                    "max_latency_ms": row["max_latency_ms"],
                    "jitter_ms": row["jitter_ms"],
                    "samples_ms": json.loads(row["samples_ms"]),
                    "stability_pct": row["stability_pct"],
                }
            )

        services = self.load_services()
        service_by_id = {service["id"]: service for service in services}
        for service_entries in history.values():
            for entry in service_entries:
                service_metadata = service_by_id.get(entry["service"]["id"])
                if service_metadata:
                    entry["service"]["name"] = service_metadata["name"]
                    entry["service"]["host"] = service_metadata["host"]
                    if entry["service"]["threshold"] is None:
                        entry["service"]["threshold"] = service_metadata["threshold"]

        return history

    def save_history(self, history: dict[str, list[dict[str, Any]]]) -> None:
        with self._lock:
            cursor = self._connection.execute("SELECT id FROM services")
            existing_service_ids = {row["id"] for row in cursor.fetchall()}

            self._connection.execute("DELETE FROM history")
            insert_values: list[tuple[Any, ...]] = []

            for service_id, entries in history.items():
                if service_id not in existing_service_ids:
                    continue

                for entry in entries:
                    service_threshold = entry.get("service", {}).get("threshold")
                    insert_values.append(
                        (
                            service_id,
                            entry["timestamp"],
                            int(entry["online"]),
                            entry["status"],
                            int(entry["sent"]),
                            int(entry["received"]),
                            float(entry["packet_loss_pct"]),
                            None if entry["avg_latency_ms"] is None else float(entry["avg_latency_ms"]),
                            None if entry["min_latency_ms"] is None else float(entry["min_latency_ms"]),
                            None if entry["max_latency_ms"] is None else float(entry["max_latency_ms"]),
                            None if entry["jitter_ms"] is None else float(entry["jitter_ms"]),
                            json.dumps(entry.get("samples_ms", []), ensure_ascii=False),
                            float(entry["stability_pct"]),
                            None if service_threshold is None else int(service_threshold),
                        )
                    )

            if insert_values:
                self._connection.executemany(
                    """
                    INSERT INTO history (
                        service_id,
                        timestamp,
                        online,
                        status,
                        sent,
                        received,
                        packet_loss_pct,
                        avg_latency_ms,
                        min_latency_ms,
                        max_latency_ms,
                        jitter_ms,
                        samples_ms,
                        stability_pct,
                        service_threshold
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    insert_values,
                )
            self._connection.commit()

    def append_history_entry(
        self,
        entry: dict[str, Any],
        max_entries: int,
        stability_window: int,
    ) -> dict[str, Any] | None:
        service_id = str(entry["service"]["id"])
        service_threshold = entry["service"].get("threshold")
        window = max(1, stability_window)

        with self._lock:
            service_exists = self._connection.execute(
                "SELECT 1 FROM services WHERE id = ?",
                (service_id,),
            ).fetchone()
            if service_exists is None:
                return None

            recent_rows = self._connection.execute(
                """
                SELECT online
                FROM history
                WHERE service_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (service_id, max(0, window - 1)),
            ).fetchall()
            online_flags = [bool(row["online"]) for row in reversed(recent_rows)]
            online_flags.append(bool(entry["online"]))
            stability_pct = round((sum(online_flags) / len(online_flags)) * 100, 2)

            persisted_entry = {
                **entry,
                "service": {
                    **entry["service"],
                    "threshold": service_threshold,
                },
                "stability_pct": stability_pct,
            }

            self._connection.execute(
                """
                INSERT INTO history (
                    service_id,
                    timestamp,
                    online,
                    status,
                    sent,
                    received,
                    packet_loss_pct,
                    avg_latency_ms,
                    min_latency_ms,
                    max_latency_ms,
                    jitter_ms,
                    samples_ms,
                    stability_pct,
                    service_threshold
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service_id,
                    persisted_entry["timestamp"],
                    int(persisted_entry["online"]),
                    persisted_entry["status"],
                    int(persisted_entry["sent"]),
                    int(persisted_entry["received"]),
                    float(persisted_entry["packet_loss_pct"]),
                    None if persisted_entry["avg_latency_ms"] is None else float(persisted_entry["avg_latency_ms"]),
                    None if persisted_entry["min_latency_ms"] is None else float(persisted_entry["min_latency_ms"]),
                    None if persisted_entry["max_latency_ms"] is None else float(persisted_entry["max_latency_ms"]),
                    None if persisted_entry["jitter_ms"] is None else float(persisted_entry["jitter_ms"]),
                    json.dumps(persisted_entry.get("samples_ms", []), ensure_ascii=False),
                    float(persisted_entry["stability_pct"]),
                    None if service_threshold is None else int(service_threshold),
                ),
            )

            if max_entries > 0:
                self._connection.execute(
                    """
                    DELETE FROM history
                    WHERE rowid IN (
                        SELECT rowid
                        FROM history
                        WHERE service_id = ?
                        ORDER BY timestamp DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (service_id, max_entries),
                )

            self._connection.commit()
            return persisted_entry

    def clear_history(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM history")
            self._connection.commit()

    def has_history(self) -> bool:
        cursor = self._execute("SELECT 1 FROM history LIMIT 1")
        return cursor.fetchone() is not None

    def delete_service(self, service_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute("DELETE FROM services WHERE id = ?", (service_id,))
            self._connection.commit()
            return cursor.rowcount > 0

    def get_last_reset(self) -> datetime | None:
        try:
            if not LAST_RESET_FILE.exists():
                return None

            text = LAST_RESET_FILE.read_text(encoding="utf-8").strip()
            if not text:
                return None

            return datetime.fromisoformat(text)
        except Exception:
            return None

    def set_last_reset(self, ts: datetime) -> None:
        try:
            LAST_RESET_FILE.write_text(ts.astimezone(timezone.utc).isoformat(), encoding="utf-8")
        except Exception:
            pass
