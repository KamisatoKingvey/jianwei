from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jianwei.config import MySqlSettings
from jianwei.storage.mysql_store import _connect


DEVICES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS devices (
  device_id VARCHAR(128) NOT NULL PRIMARY KEY,
  secret VARCHAR(128) NULL,
  bind_code VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_devices_bind_code (bind_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

USER_DEVICES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_devices (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  openid VARCHAR(128) NOT NULL,
  device_id VARCHAR(128) NOT NULL,
  role VARCHAR(32) NOT NULL DEFAULT 'owner',
  bound_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_user_devices (openid, device_id),
  KEY idx_user_devices_device (device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def generate_bind_code() -> str:
    return secrets.token_hex(3).upper()


class JsonDeviceStore:
    """本地 JSON 文件的设备注册表 + 用户绑定关系。"""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def upsert_device(self, device_id: str, secret: str | None = None) -> dict[str, Any]:
        data = self._load()
        device = data["devices"].get(device_id)
        if device is None:
            device = {
                "device_id": device_id,
                "secret": secret,
                "bind_code": self._unique_bind_code(data),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            data["devices"][device_id] = device
            self._save(data)
        elif secret is not None and device.get("secret") != secret:
            device["secret"] = secret
            self._save(data)
        return dict(device)

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        device = self._load()["devices"].get(device_id)
        return dict(device) if device else None

    def find_by_bind_code(self, bind_code: str) -> dict[str, Any] | None:
        for device in self._load()["devices"].values():
            if device["bind_code"] == bind_code.upper():
                return dict(device)
        return None

    def bind_user(self, openid: str, device_id: str, role: str = "owner") -> None:
        data = self._load()
        for binding in data["bindings"]:
            if binding["openid"] == openid and binding["device_id"] == device_id:
                return
        data["bindings"].append(
            {
                "openid": openid,
                "device_id": device_id,
                "role": role,
                "bound_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save(data)

    def devices_for_user(self, openid: str) -> list[str]:
        return [b["device_id"] for b in self._load()["bindings"] if b["openid"] == openid]

    def openids_for_device(self, device_id: str) -> list[str]:
        return [b["openid"] for b in self._load()["bindings"] if b["device_id"] == device_id]

    def _unique_bind_code(self, data: dict[str, Any]) -> str:
        existing = {device["bind_code"] for device in data["devices"].values()}
        while True:
            code = generate_bind_code()
            if code not in existing:
                return code

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"devices": {}, "bindings": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class MySqlDeviceStore:
    def __init__(self, settings: MySqlSettings, connect: Callable[[MySqlSettings], Any] | None = None):
        self.settings = settings
        self._connect = connect or _connect
        self._schema_ready = False

    def upsert_device(self, device_id: str, secret: str | None = None) -> dict[str, Any]:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM devices WHERE device_id = %s", (device_id,))
                row = cursor.fetchone()
                if row is None:
                    bind_code = generate_bind_code()
                    cursor.execute(
                        "INSERT INTO devices (device_id, secret, bind_code) VALUES (%s, %s, %s)",
                        (device_id, secret, bind_code),
                    )
                    connection.commit()
                    cursor.execute("SELECT * FROM devices WHERE device_id = %s", (device_id,))
                    row = cursor.fetchone()
                elif secret is not None and row.get("secret") != secret:
                    cursor.execute(
                        "UPDATE devices SET secret = %s WHERE device_id = %s",
                        (secret, device_id),
                    )
                    connection.commit()
                    row = dict(row)
                    row["secret"] = secret
        return dict(row)

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM devices WHERE device_id = %s", (device_id,))
        return dict(row) if row else None

    def find_by_bind_code(self, bind_code: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM devices WHERE bind_code = %s", (bind_code.upper(),))
        return dict(row) if row else None

    def bind_user(self, openid: str, device_id: str, role: str = "owner") -> None:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT IGNORE INTO user_devices (openid, device_id, role) VALUES (%s, %s, %s)",
                    (openid, device_id, role),
                )
            connection.commit()

    def devices_for_user(self, openid: str) -> list[str]:
        rows = self._fetchall(
            "SELECT device_id FROM user_devices WHERE openid = %s ORDER BY bound_at, id",
            (openid,),
        )
        return [row["device_id"] for row in rows]

    def openids_for_device(self, device_id: str) -> list[str]:
        rows = self._fetchall(
            "SELECT openid FROM user_devices WHERE device_id = %s ORDER BY bound_at, id",
            (device_id,),
        )
        return [row["openid"] for row in rows]

    def _fetchone(self, sql: str, params: tuple) -> Any:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()

    def _fetchall(self, sql: str, params: tuple) -> list[Any]:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())

    def _ensure_schema(self, connection: Any) -> None:
        if self._schema_ready:
            return
        with connection.cursor() as cursor:
            cursor.execute(DEVICES_SCHEMA_SQL)
            cursor.execute(USER_DEVICES_SCHEMA_SQL)
        connection.commit()
        self._schema_ready = True
