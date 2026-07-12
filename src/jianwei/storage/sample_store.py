from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from jianwei.config import MySqlSettings
from jianwei.storage.mysql_store import _connect


SAMPLE_FIELDS = (
    "presence",
    "activity",
    "breath_rate",
    "heart_rate",
    "sleep_stage",
    "sleep_score",
    "in_bed",
    "movement",
    "distance",
    "co2",
    "temperature",
    "humidity",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS radar_samples (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  device_id VARCHAR(128) NOT NULL,
  sampled_at DATETIME NOT NULL,
  clock_synced TINYINT NOT NULL DEFAULT 1,
  presence TINYINT NULL,
  activity TINYINT NULL,
  breath_rate INT NULL,
  heart_rate INT NULL,
  sleep_stage TINYINT NULL,
  sleep_score INT NULL,
  in_bed TINYINT NULL,
  movement INT NULL,
  distance INT NULL,
  co2 INT NULL,
  temperature DOUBLE NULL,
  humidity DOUBLE NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_radar_samples_device_time (device_id, sampled_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class JsonlSampleStore:
    """ESP32 平铺采样的本地 JSONL 存储，字段与 radar_samples 表一致。"""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append_many(self, rows: list[dict[str, Any]]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(_encode(row), ensure_ascii=False, separators=(",", ":")) + "\n")

    def iter_device(
        self,
        device_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from sorted(self._scan(device_id, start, end), key=lambda row: row["sampled_at"])

    def latest(self, device_id: str) -> dict[str, Any] | None:
        rows = list(self._scan(device_id))
        if not rows:
            return None
        return max(rows, key=lambda row: row["sampled_at"])

    def count(self, device_id: str | None = None) -> int:
        return sum(1 for _ in self._scan(device_id))

    def _scan(
        self,
        device_id: str | None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = _decode(json.loads(line))
                if device_id is not None and row["device_id"] != device_id:
                    continue
                if start is not None and row["sampled_at"] < start:
                    continue
                if end is not None and row["sampled_at"] > end:
                    continue
                yield row


class MySqlSampleStore:
    def __init__(self, settings: MySqlSettings, connect: Callable[[MySqlSettings], Any] | None = None):
        self.settings = settings
        self._connect = connect or _connect
        self._schema_ready = False

    def append_many(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = ["device_id", "sampled_at", "clock_synced", *SAMPLE_FIELDS]
        placeholders = ", ".join(["%s"] * len(columns))
        sql = f"INSERT INTO radar_samples ({', '.join(columns)}) VALUES ({placeholders})"
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.executemany(
                    sql,
                    [
                        tuple(row.get(column) if column != "clock_synced" else int(bool(row.get(column, True))) for column in columns)
                        for row in rows
                    ],
                )
            connection.commit()

    def iter_device(
        self,
        device_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Iterator[dict[str, Any]]:
        sql = "SELECT * FROM radar_samples WHERE device_id = %s"
        params: list[Any] = [device_id]
        if start is not None:
            sql += " AND sampled_at >= %s"
            params.append(start)
        if end is not None:
            sql += " AND sampled_at <= %s"
            params.append(end)
        sql += " ORDER BY sampled_at, id"
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = [dict(row) for row in cursor.fetchall()]
        return iter(rows)

    def latest(self, device_id: str) -> dict[str, Any] | None:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM radar_samples WHERE device_id = %s ORDER BY sampled_at DESC, id DESC LIMIT 1",
                    (device_id,),
                )
                row = cursor.fetchone()
        return dict(row) if row else None

    def count(self, device_id: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS total FROM radar_samples"
        params: tuple = ()
        if device_id is not None:
            sql += " WHERE device_id = %s"
            params = (device_id,)
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()
        return int(row["total"]) if row else 0

    def _ensure_schema(self, connection: Any) -> None:
        if self._schema_ready:
            return
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
        connection.commit()
        self._schema_ready = True


def _encode(row: dict[str, Any]) -> dict[str, Any]:
    encoded = dict(row)
    if isinstance(encoded.get("sampled_at"), datetime):
        encoded["sampled_at"] = encoded["sampled_at"].isoformat()
    return encoded


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("sampled_at"), str):
        row["sampled_at"] = datetime.fromisoformat(row["sampled_at"])
    return row
