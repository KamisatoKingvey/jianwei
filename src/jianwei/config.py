from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict


ROOT = Path(__file__).resolve().parents[2]


class MySqlSettings(TypedDict):
    host: str
    port: int
    database: str
    user: str
    password: str


def load_env_file(path: Path = ROOT / ".env") -> None:
    if os.environ.get("JIANWEI_SKIP_ENV_FILE") == "1":
        return
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def mysql_settings_from_env(env: dict[str, str] | None = None) -> MySqlSettings | None:
    values = env if env is not None else os.environ
    address = values.get("MYSQL_ADDRESS")
    user = values.get("MYSQL_USERNAME")
    password = values.get("MYSQL_PASSWORD")
    if not all([address, user, password]):
        return None

    host, port = _split_mysql_address(address or "")
    return {
        "host": host,
        "port": port,
        "database": values.get("MYSQL_DATABASE", "flask_demo"),
        "user": user or "",
        "password": password or "",
    }


def _split_mysql_address(address: str) -> tuple[str, int]:
    if ":" not in address:
        return address, 3306
    host, port = address.rsplit(":", 1)
    return host, int(port or "3306")
