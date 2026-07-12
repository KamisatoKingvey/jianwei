import json
from datetime import datetime, timedelta, timezone

import pytest

from jianwei.agent import tools
from jianwei.agent.tools import AgentContext
from jianwei.storage.alert_store import JsonlAlertStore
from jianwei.storage.device_store import JsonDeviceStore
from jianwei.storage.sample_store import JsonlSampleStore


BASE = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


@pytest.fixture()
def context(tmp_path):
    sample_store = JsonlSampleStore(tmp_path / "samples.jsonl")
    device_store = JsonDeviceStore(tmp_path / "devices.json")
    alert_store = JsonlAlertStore(tmp_path / "alerts.jsonl")

    device_store.upsert_device("dev-mine")
    device_store.bind_user("openid-1", "dev-mine")
    device_store.upsert_device("dev-other")
    device_store.bind_user("openid-2", "dev-other")

    ctx = AgentContext(
        openid="openid-1",
        sample_store=sample_store,
        device_store=device_store,
        alert_store=alert_store,
    )
    tools.set_context(ctx)
    return ctx


def seed_night(sample_store, device_id, start, count=60):
    rows = []
    for index in range(count):
        rows.append(
            {
                "device_id": device_id,
                "sampled_at": start + timedelta(seconds=index * 30),
                "clock_synced": True,
                "presence": 1,
                "activity": 1,
                "breath_rate": 15,
                "heart_rate": 66,
                "sleep_stage": 1,
                "sleep_score": 80,
                "in_bed": 1,
                "movement": 3,
                "distance": 60,
                "co2": 900,
                "temperature": 24.0,
                "humidity": 55.0,
            }
        )
    sample_store.append_many(rows)


def test_get_my_devices_lists_only_bound_devices(context):
    result = json.loads(tools.get_my_devices())

    assert [d["device_id"] for d in result["devices"]] == ["dev-mine"]


def test_tools_reject_unbound_device(context):
    for call in (
        tools.get_latest_report("dev-other"),
        tools.get_night_reports("dev-other"),
        tools.get_realtime_status("dev-other"),
        tools.get_recent_alerts("dev-other"),
    ):
        assert "权限错误" in json.loads(call)["error"]


def test_get_latest_report_returns_session_report(context):
    seed_night(context.sample_store, "dev-mine", datetime.now(timezone.utc) - timedelta(hours=8))

    report = json.loads(tools.get_latest_report("dev-mine"))

    assert report["device_id"] == "dev-mine"
    assert report["metrics"]["average_respiration"] == 15
    assert report["environment"]["average_co2"] == 900


def test_get_latest_report_without_sessions_reports_friendly_error(context):
    result = json.loads(tools.get_latest_report("dev-mine"))

    assert "没有监测会话" in result["error"]


def test_get_night_reports_returns_compact_summaries(context):
    seed_night(context.sample_store, "dev-mine", datetime.now(timezone.utc) - timedelta(days=2))
    seed_night(context.sample_store, "dev-mine", datetime.now(timezone.utc) - timedelta(hours=8))

    result = json.loads(tools.get_night_reports("dev-mine", days=7))

    assert len(result["nights"]) == 2
    night = result["nights"][0]
    assert set(night) >= {"session_id", "duration_minutes", "risk", "quality"}


def test_get_realtime_status_reports_latest_sample(context):
    seed_night(context.sample_store, "dev-mine", datetime.now(timezone.utc) - timedelta(minutes=2), count=3)

    status = json.loads(tools.get_realtime_status("dev-mine"))

    assert status["online"] is True
    assert status["breath_rate"] == 15
    assert status["co2"] == 900


def test_get_recent_alerts_defaults_to_all_my_devices(context):
    context.alert_store.append(
        {
            "device_id": "dev-mine",
            "alert_type": "night_bed_exit",
            "level": "info",
            "message": "夜间离床",
            "created_at": BASE,
        }
    )
    context.alert_store.append(
        {
            "device_id": "dev-other",
            "alert_type": "night_bed_exit",
            "level": "info",
            "message": "别人的告警",
            "created_at": BASE,
        }
    )

    result = json.loads(tools.get_recent_alerts())

    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["device_id"] == "dev-mine"
