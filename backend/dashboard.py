from __future__ import annotations

from typing import Any


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def get_status_priority(status: str) -> int:
    if status == "offline":
        return 0
    if status == "degraded":
        return 1
    return 2


def build_dashboard_data(
    services: list[dict[str, Any]],
    history: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []

    for service in services:
        entries = history.get(service["id"], [])
        latest = entries[-1] if entries else None

        if latest is None:
            snapshots.append(
                {
                    "id": service["id"],
                    "name": service["name"],
                    "host": service["host"],
                    "threshold": service.get("threshold", 100),
                    "imageUrl": service.get("imageUrl", ""),
                    "online": False,
                    "status": "offline",
                    "avgLatencyMs": None,
                    "minLatencyMs": None,
                    "maxLatencyMs": None,
                    "packetLossPct": None,
                    "jitterMs": None,
                    "stabilityPct": None,
                    "sent": None,
                    "received": None,
                    "samplesMs": [],
                    "lastUpdate": None,
                }
            )
            continue

        snapshots.append(
            {
                "id": service["id"],
                "name": service["name"],
                "host": service["host"],
                "threshold": service.get("threshold", 100),
                "imageUrl": service.get("imageUrl", ""),
                "online": latest["online"],
                "status": latest["status"],
                "avgLatencyMs": latest["avg_latency_ms"],
                "minLatencyMs": latest["min_latency_ms"],
                "maxLatencyMs": latest["max_latency_ms"],
                "packetLossPct": latest["packet_loss_pct"],
                "jitterMs": latest["jitter_ms"],
                "stabilityPct": latest["stability_pct"],
                "sent": latest["sent"],
                "received": latest["received"],
                "samplesMs": latest["samples_ms"],
                "lastUpdate": latest["timestamp"],
            }
        )

    snapshots.sort(
        key=lambda service: (get_status_priority(service["status"]), service["name"].lower())
    )

    normalized_history = {
        service["id"]: [
            {
                "timestamp": entry["timestamp"],
                "latencyMs": entry["avg_latency_ms"],
                "online": entry["online"],
                "status": entry["status"],
                "packetLossPct": entry["packet_loss_pct"],
                "jitterMs": entry["jitter_ms"],
                "stabilityPct": entry["stability_pct"],
                "threshold": entry["service"].get("threshold", 100),
            }
            for entry in history.get(service["id"], [])
        ]
        for service in services
    }

    last_updates = [service["lastUpdate"] for service in snapshots if service["lastUpdate"]]
    latency_values = [service["avgLatencyMs"] for service in snapshots if isinstance(service["avgLatencyMs"], (int, float))]
    stability_values = [service["stabilityPct"] for service in snapshots if isinstance(service["stabilityPct"], (int, float))]

    return {
        "meta": {
            "source": "python",
            "lastUpdate": max(last_updates) if last_updates else None,
            "nextUpdateSeconds": 15,
        },
        "summary": {
            "total": len(snapshots),
            "online": sum(1 for service in snapshots if service["status"] == "online"),
            "degraded": sum(1 for service in snapshots if service["status"] == "degraded"),
            "offline": sum(1 for service in snapshots if service["status"] == "offline"),
            "avgLatencyMs": average(latency_values),
            "avgStabilityPct": average(stability_values),
        },
        "services": snapshots,
        "history": normalized_history,
    }
