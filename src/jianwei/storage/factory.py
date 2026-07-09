from __future__ import annotations

from pathlib import Path

from jianwei.config import mysql_settings_from_env
from jianwei.storage.jsonl_store import JsonlEventStore
from jianwei.storage.mysql_store import MySqlEventStore


def build_event_store(jsonl_path: Path):
    mysql_settings = mysql_settings_from_env()
    if mysql_settings:
        return MySqlEventStore(mysql_settings)
    return JsonlEventStore(jsonl_path)
