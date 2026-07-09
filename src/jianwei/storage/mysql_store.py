from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from jianwei.config import MySqlSettings
from jianwei.radar.r60abd1 import RadarEvent, event_to_dict


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS radar_events (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  device_id VARCHAR(128) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  session_id VARCHAR(128) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  event JSON NOT NULL,
  event_timestamp DATETIME NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_radar_events_session (device_id, session_id, event_timestamp, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class MySqlEventStore:
    def __init__(self, settings: MySqlSettings, connect: Callable[[MySqlSettings], Any] | None = None):
        self.settings = settings
        self._connect = connect or _connect
        self._schema_ready = False

    def append(self, device_id: str, user_id: str, session_id: str, event: RadarEvent) -> None:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO radar_events
                      (device_id, user_id, session_id, event_type, event, event_timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        device_id,
                        user_id,
                        session_id,
                        event.type,
                        json.dumps(event_to_dict(event), ensure_ascii=False, separators=(",", ":")),
                        event.timestamp,
                    ),
                )
            connection.commit()

    def iter_session(self, device_id: str, session_id: str) -> Iterator[dict[str, Any]]:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT device_id, user_id, session_id, event
                    FROM radar_events
                    WHERE device_id = %s AND session_id = %s
                    ORDER BY event_timestamp IS NULL, event_timestamp, id
                    """,
                    (device_id, session_id),
                )
                rows = [_decode_row(dict(row)) for row in cursor.fetchall()]
        return iter(rows)

    def is_healthy(self) -> bool:
        try:
            with self._connect(self.settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            return True
        except Exception:
            return False

    def _ensure_schema(self, connection: Any) -> None:
        if self._schema_ready:
            return
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
        connection.commit()
        self._schema_ready = True


def _connect(settings: MySqlSettings) -> Any:
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ModuleNotFoundError as exc:
        raise RuntimeError("MySQL support requires PyMySQL") from exc

    return pymysql.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        database=settings["database"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("event"), str):
        row["event"] = json.loads(row["event"])
    return row
