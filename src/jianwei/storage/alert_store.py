from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from jianwei.config import MySqlSettings
from jianwei.storage.mysql_store import _connect


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  device_id VARCHAR(128) NOT NULL,
  alert_type VARCHAR(64) NOT NULL,
  level VARCHAR(32) NOT NULL,
  message VARCHAR(512) NOT NULL,
  created_at DATETIME NOT NULL,
  KEY idx_alerts_device_time (device_id, created_at, id),
  KEY idx_alerts_device_type (device_id, alert_type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class JsonlAlertStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, alert: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_encode(alert), ensure_ascii=False, separators=(",", ":")) + "\n")

    def recent(self, device_ids: list[str], limit: int = 20) -> list[dict[str, Any]]:
        rows = [row for row in self._scan() if row["device_id"] in device_ids]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows[:limit]

    def last_time(self, device_id: str, alert_type: str) -> datetime | None:
        times = [
            row["created_at"]
            for row in self._scan()
            if row["device_id"] == device_id and row["alert_type"] == alert_type
        ]
        return max(times) if times else None

    def _scan(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield _decode(json.loads(line))


class MySqlAlertStore:
    def __init__(self, settings: MySqlSettings, connect: Callable[[MySqlSettings], Any] | None = None):
        self.settings = settings
        self._connect = connect or _connect
        self._schema_ready = False

    def append(self, alert: dict[str, Any]) -> None:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO alerts (device_id, alert_type, level, message, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        alert["device_id"],
                        alert["alert_type"],
                        alert["level"],
                        alert["message"],
                        alert["created_at"],
                    ),
                )
            connection.commit()

    def recent(self, device_ids: list[str], limit: int = 20) -> list[dict[str, Any]]:
        if not device_ids:
            return []
        placeholders = ", ".join(["%s"] * len(device_ids))
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT device_id, alert_type, level, message, created_at
                    FROM alerts
                    WHERE device_id IN ({placeholders})
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (*device_ids, limit),
                )
                return [dict(row) for row in cursor.fetchall()]

    def last_time(self, device_id: str, alert_type: str) -> datetime | None:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT MAX(created_at) AS last_time
                    FROM alerts
                    WHERE device_id = %s AND alert_type = %s
                    """,
                    (device_id, alert_type),
                )
                row = cursor.fetchone()
        return row["last_time"] if row else None

    def _ensure_schema(self, connection: Any) -> None:
        if self._schema_ready:
            return
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
        connection.commit()
        self._schema_ready = True


def _encode(alert: dict[str, Any]) -> dict[str, Any]:
    encoded = dict(alert)
    if isinstance(encoded.get("created_at"), datetime):
        encoded["created_at"] = encoded["created_at"].isoformat()
    return encoded


def _decode(alert: dict[str, Any]) -> dict[str, Any]:
    if isinstance(alert.get("created_at"), str):
        alert["created_at"] = datetime.fromisoformat(alert["created_at"])
    return alert
