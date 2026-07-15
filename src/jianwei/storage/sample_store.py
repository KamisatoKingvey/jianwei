from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from jianwei.config import MySqlSettings
from jianwei.storage.mysql_store import _connect, ensure_utc


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

# 波形字段：每行存一个 int 列表（该秒的原始波形增量），
# MySQL 里以 JSON 文本落库，读回时解码回列表。与标量 SAMPLE_FIELDS 分开处理。
WAVEFORM_FIELDS = (
    "respiration_waveform",
    "heart_waveform",
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
  respiration_waveform TEXT NULL,
  heart_waveform TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_radar_samples_device_time (device_id, sampled_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 兼容已部署的旧表：CREATE TABLE IF NOT EXISTS 不会给已存在的表补列，
# 这里逐列检查 information_schema 后按需 ALTER，幂等且不依赖 MySQL 版本。
WAVEFORM_COLUMN_DDL = {
    "respiration_waveform": "ALTER TABLE radar_samples ADD COLUMN respiration_waveform TEXT NULL",
    "heart_waveform": "ALTER TABLE radar_samples ADD COLUMN heart_waveform TEXT NULL",
}


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
        columns = ["device_id", "sampled_at", "clock_synced", *SAMPLE_FIELDS, *WAVEFORM_FIELDS]
        placeholders = ", ".join(["%s"] * len(columns))
        sql = f"INSERT INTO radar_samples ({', '.join(columns)}) VALUES ({placeholders})"
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.executemany(sql, [self._insert_values(row, columns) for row in rows])
            connection.commit()

    @staticmethod
    def _insert_values(row: dict[str, Any], columns: list[str]) -> tuple:
        values: list[Any] = []
        for column in columns:
            if column == "clock_synced":
                values.append(int(bool(row.get(column, True))))
            elif column in WAVEFORM_FIELDS:
                values.append(_encode_waveform(row.get(column)))
            else:
                values.append(row.get(column))
        return tuple(values)

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
                rows = [_utc_row(dict(row)) for row in cursor.fetchall()]
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
        return _utc_row(dict(row)) if row else None

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
            self._ensure_waveform_columns(cursor)
        connection.commit()
        self._schema_ready = True

    @staticmethod
    def _ensure_waveform_columns(cursor: Any) -> None:
        for column, ddl in WAVEFORM_COLUMN_DDL.items():
            # SHOW COLUMNS ... LIKE 有行即列已存在（新建表已含这些列），无行才补 ALTER
            cursor.execute("SHOW COLUMNS FROM radar_samples LIKE %s", (column,))
            if cursor.fetchone() is None:
                cursor.execute(ddl)


def _utc_row(row: dict[str, Any]) -> dict[str, Any]:
    row["sampled_at"] = ensure_utc(row.get("sampled_at"))
    row["clock_synced"] = bool(row.get("clock_synced", True))
    for field in WAVEFORM_FIELDS:
        row[field] = _decode_waveform(row.get(field))
    return row


def _encode(row: dict[str, Any]) -> dict[str, Any]:
    encoded = dict(row)
    if isinstance(encoded.get("sampled_at"), datetime):
        encoded["sampled_at"] = encoded["sampled_at"].isoformat()
    return encoded


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("sampled_at"), str):
        row["sampled_at"] = datetime.fromisoformat(row["sampled_at"])
    for field in WAVEFORM_FIELDS:
        if field in row:
            row[field] = _decode_waveform(row.get(field))
    return row


def _encode_waveform(value: Any) -> str | None:
    """把波形列表编码成落库的 JSON 文本；空/缺省存 NULL 省空间。"""
    if not value:
        return None
    return json.dumps([int(sample) for sample in value], separators=(",", ":"))


def _decode_waveform(value: Any) -> list[int]:
    """把落库的波形还原成 int 列表；兼容 JSON 文本、已解析列表、NULL。"""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [int(sample) for sample in value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [int(sample) for sample in parsed] if isinstance(parsed, list) else []
