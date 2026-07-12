from __future__ import annotations

from pathlib import Path

from jianwei.config import mysql_settings_from_env
from jianwei.storage.agent_store import JsonlAgentStore, MySqlAgentStore
from jianwei.storage.alert_store import JsonlAlertStore, MySqlAlertStore
from jianwei.storage.device_store import JsonDeviceStore, MySqlDeviceStore
from jianwei.storage.jsonl_store import JsonlEventStore
from jianwei.storage.mysql_store import MySqlEventStore
from jianwei.storage.sample_store import JsonlSampleStore, MySqlSampleStore


def build_event_store(jsonl_path: Path):
    mysql_settings = mysql_settings_from_env()
    if mysql_settings:
        return MySqlEventStore(mysql_settings)
    return JsonlEventStore(jsonl_path)


def build_sample_store(jsonl_path: Path):
    mysql_settings = mysql_settings_from_env()
    if mysql_settings:
        return MySqlSampleStore(mysql_settings)
    return JsonlSampleStore(jsonl_path)


def build_device_store(json_path: Path):
    mysql_settings = mysql_settings_from_env()
    if mysql_settings:
        return MySqlDeviceStore(mysql_settings)
    return JsonDeviceStore(json_path)


def build_alert_store(jsonl_path: Path):
    mysql_settings = mysql_settings_from_env()
    if mysql_settings:
        return MySqlAlertStore(mysql_settings)
    return JsonlAlertStore(jsonl_path)


def build_agent_store(messages_path: Path, insights_path: Path):
    mysql_settings = mysql_settings_from_env()
    if mysql_settings:
        return MySqlAgentStore(mysql_settings)
    return JsonlAgentStore(messages_path, insights_path)
