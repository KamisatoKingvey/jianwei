from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from jianwei.alerts.rules import detect_alerts
from jianwei.analysis.report import build_sleep_report
from jianwei.analysis.segments import session_report, slice_sessions
from jianwei.analysis.session import build_session
from jianwei.config import load_env_file
from jianwei.notify.wechat import send_alert_notifications
from jianwei.radar.r60abd1 import event_from_dict, events_from_hex_log
from jianwei.radar.simulator import generate_demo_events
from jianwei.storage.factory import (
    build_alert_store,
    build_device_store,
    build_event_store,
    build_sample_store,
)


logger = logging.getLogger("jianwei.api")

ROOT = Path(__file__).resolve().parents[3]
DATA_PATH = ROOT / "data" / "events.jsonl"
SAMPLES_PATH = ROOT / "data" / "samples.jsonl"
DEVICES_PATH = ROOT / "data" / "devices.json"
ALERTS_PATH = ROOT / "data" / "alerts.jsonl"
STATIC_PATH = ROOT / "static"

load_env_file(ROOT / ".env")

app = FastAPI(title="Jianwei Backend", version="0.2.0")
store = build_event_store(DATA_PATH)
sample_store = build_sample_store(SAMPLES_PATH)
device_store = build_device_store(DEVICES_PATH)
alert_store = build_alert_store(ALERTS_PATH)

if STATIC_PATH.exists():
    app.mount("/preview", StaticFiles(directory=STATIC_PATH, html=True), name="preview")


# ESP32 未做 NTP 同步时 timestamp 是开机毫秒数，量级远小于 epoch 毫秒
EPOCH_MS_THRESHOLD = 10_000_000_000
ONLINE_WINDOW_MINUTES = 5


class HexIngestRequest(BaseModel):
    device_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    log: str = Field(min_length=1)


class CountRequest(BaseModel):
    action: str = "get"


class RadarSampleIn(BaseModel):
    """与 ESP32 固件（Java 版 RadarData）一致的上报体。"""

    device: str = Field(min_length=1)
    starttimestamp: int = 0
    timestamp: int = 0
    presence: int = 0
    activity: int = 0
    breath_rate: int = 0
    heart_rate: int = 0
    sleep_stage: int = 0
    sleep_score: int = 0
    in_bed: int = 0
    movement: int = 0
    distance: int = 0
    co2: int = 0
    temperature: float = 0.0
    humidity: float = 0.0


class RegisterDeviceRequest(BaseModel):
    device_id: str = Field(min_length=1)
    secret: str | None = None


class BindDeviceRequest(BaseModel):
    bind_code: str = Field(min_length=1)
    openid: str | None = None


_template_count = 0


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "jianwei-backend",
        "storage": _storage_health(),
    }


@app.get("/api/reports/demo")
def demo_report() -> dict:
    session = build_session("demo-night", list(generate_demo_events()))
    return build_sleep_report(session)


@app.post("/api/count")
def count(request: CountRequest) -> dict[str, int | str]:
    global _template_count

    if request.action == "inc":
        _template_count += 1
    elif request.action == "clear":
        _template_count = 0
    elif request.action != "get":
        raise HTTPException(status_code=400, detail="unsupported action")

    return {"action": request.action, "count": _template_count}


@app.get("/api/reports/{device_id}/{session_id}")
def session_report_by_id(device_id: str, session_id: str) -> dict:
    rows = list(store.iter_session(device_id, session_id))
    if not rows:
        raise HTTPException(status_code=404, detail="session not found")

    events = [event_from_dict(row["event"]) for row in rows]
    report = build_sleep_report(build_session(session_id, events))
    report["device_id"] = device_id
    return report


@app.post("/api/radar/ingest-hex")
def ingest_hex_log(request: HexIngestRequest) -> dict[str, int | str]:
    events = events_from_hex_log(request.log)
    for event in events:
        store.append(request.device_id, request.user_id, request.session_id, event)
    return {"status": "ok", "ingested": len(events)}


# ---------------------------------------------------------------------------
# ESP32 采样上报（与 Java 版 RadarController 的 /api/radar/data、/batch 兼容）
# ---------------------------------------------------------------------------


@app.post("/api/radar/data")
def ingest_sample(
    sample: RadarSampleIn,
    x_device_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    return _ingest([sample], x_device_secret)


@app.post("/api/radar/batch")
def ingest_batch(
    batch: list[RadarSampleIn],
    x_device_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    if not batch:
        raise HTTPException(status_code=400, detail="empty batch")
    return _ingest(batch, x_device_secret)


def _ingest(items: list[RadarSampleIn], device_secret: str | None) -> dict[str, Any]:
    device_id = items[0].device
    if any(item.device != device_id for item in items):
        raise HTTPException(status_code=400, detail="batch must contain a single device")

    _authorize_device(device_id, device_secret)

    received_at = datetime.now(timezone.utc)
    rows = _normalize_samples(device_id, items, received_at)
    sample_store.append_many(rows)

    _run_alert_pipeline(device_id, rows)

    return {
        "status": "ok",
        "received": len(rows),
        "total": sample_store.count(device_id),
    }


def _authorize_device(device_id: str, device_secret: str | None) -> None:
    # 首次上报自动注册（生成绑定码）；一旦设备登记了 secret 就强制校验
    device = device_store.upsert_device(device_id)
    expected = device.get("secret")
    if expected and expected != device_secret:
        raise HTTPException(status_code=401, detail="invalid device secret")


def _normalize_samples(
    device_id: str,
    items: list[RadarSampleIn],
    received_at: datetime,
) -> list[dict[str, Any]]:
    """时钟同步的采样直接用 epoch 毫秒；未同步的用接收时间锚定，
    并利用批内开机毫秒差还原相对时间。"""
    unsynced_boot_ms = [item.timestamp for item in items if 0 < item.timestamp < EPOCH_MS_THRESHOLD]
    boot_anchor = max(unsynced_boot_ms) if unsynced_boot_ms else 0

    rows = []
    for item in items:
        if item.timestamp >= EPOCH_MS_THRESHOLD:
            sampled_at = datetime.fromtimestamp(item.timestamp / 1000, tz=timezone.utc)
            clock_synced = True
        elif item.timestamp > 0:
            sampled_at = received_at - timedelta(milliseconds=boot_anchor - item.timestamp)
            clock_synced = False
        else:
            sampled_at = received_at
            clock_synced = False

        rows.append(
            {
                "device_id": device_id,
                "sampled_at": sampled_at,
                "clock_synced": clock_synced,
                "presence": item.presence,
                "activity": item.activity,
                "breath_rate": item.breath_rate,
                "heart_rate": item.heart_rate,
                "sleep_stage": item.sleep_stage,
                "sleep_score": item.sleep_score,
                "in_bed": item.in_bed,
                "movement": item.movement,
                "distance": item.distance,
                "co2": item.co2,
                "temperature": item.temperature,
                "humidity": item.humidity,
            }
        )
    return rows


def _run_alert_pipeline(device_id: str, rows: list[dict[str, Any]]) -> None:
    # 告警属于旁路：检测或推送失败不能影响数据入库
    try:
        alerts = detect_alerts(device_id, rows, alert_store.last_time)
        for alert in alerts:
            alert_store.append(alert)
            openids = device_store.openids_for_device(device_id)
            send_alert_notifications(openids, alert)
    except Exception:
        logger.exception("alert pipeline failed for device %s", device_id)


# ---------------------------------------------------------------------------
# 设备注册与用户绑定
# ---------------------------------------------------------------------------


@app.post("/api/devices/register")
def register_device(
    request: RegisterDeviceRequest,
    x_admin_key: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_key = os.environ.get("JIANWEI_ADMIN_KEY")
    if admin_key and x_admin_key != admin_key:
        raise HTTPException(status_code=401, detail="invalid admin key")

    device = device_store.upsert_device(request.device_id, secret=request.secret)
    return {
        "device_id": device["device_id"],
        "bind_code": device["bind_code"],
        "has_secret": bool(device.get("secret")),
    }


@app.post("/api/devices/bind")
def bind_device(
    request: BindDeviceRequest,
    x_wx_openid: str | None = Header(default=None),
) -> dict[str, Any]:
    openid = x_wx_openid or request.openid
    if not openid:
        raise HTTPException(status_code=400, detail="missing openid")

    device = device_store.find_by_bind_code(request.bind_code)
    if device is None:
        raise HTTPException(status_code=404, detail="bind code not found")

    device_store.bind_user(openid, device["device_id"])
    return {"status": "ok", "device_id": device["device_id"]}


@app.get("/api/devices/mine")
def my_devices(x_wx_openid: str | None = Header(default=None)) -> dict[str, Any]:
    openid = _require_openid(x_wx_openid)
    now = datetime.now(timezone.utc)

    devices = []
    for device_id in device_store.devices_for_user(openid):
        latest = sample_store.latest(device_id)
        last_seen = latest["sampled_at"] if latest else None
        devices.append(
            {
                "device_id": device_id,
                "online": bool(last_seen and now - last_seen < timedelta(minutes=ONLINE_WINDOW_MINUTES)),
                "last_seen": last_seen.isoformat() if last_seen else None,
                "latest": _public_sample(latest) if latest else None,
            }
        )
    return {"devices": devices}


@app.get("/api/devices/{device_id}/status")
def device_status(device_id: str) -> dict[str, Any]:
    latest = sample_store.latest(device_id)
    if latest is None:
        raise HTTPException(status_code=404, detail="no samples for device")
    now = datetime.now(timezone.utc)
    return {
        "device_id": device_id,
        "online": now - latest["sampled_at"] < timedelta(minutes=ONLINE_WINDOW_MINUTES),
        "latest": _public_sample(latest),
    }


# ---------------------------------------------------------------------------
# 基于连续采样的睡眠报告
# ---------------------------------------------------------------------------


@app.get("/api/reports/device/{device_id}/latest")
def latest_device_report(device_id: str) -> dict[str, Any]:
    start = datetime.now(timezone.utc) - timedelta(hours=36)
    samples = list(sample_store.iter_device(device_id, start=start))
    sessions = slice_sessions(samples)
    if not sessions:
        raise HTTPException(status_code=404, detail="no monitoring session found")
    return session_report(device_id, sessions[-1])


@app.get("/api/reports/device/{device_id}/nights")
def device_night_reports(device_id: str, days: int = 7) -> dict[str, Any]:
    days = max(1, min(days, 31))
    start = datetime.now(timezone.utc) - timedelta(days=days)
    samples = list(sample_store.iter_device(device_id, start=start))
    reports = [session_report(device_id, session) for session in slice_sessions(samples)]
    return {"device_id": device_id, "days": days, "reports": reports}


# ---------------------------------------------------------------------------
# 告警查询
# ---------------------------------------------------------------------------


@app.get("/api/alerts/device/{device_id}")
def device_alerts(device_id: str, limit: int = 20) -> dict[str, Any]:
    alerts = alert_store.recent([device_id], limit=max(1, min(limit, 100)))
    return {"device_id": device_id, "alerts": [_public_alert(alert) for alert in alerts]}


@app.get("/api/alerts/mine")
def my_alerts(
    x_wx_openid: str | None = Header(default=None),
    limit: int = 20,
) -> dict[str, Any]:
    openid = _require_openid(x_wx_openid)
    device_ids = device_store.devices_for_user(openid)
    alerts = alert_store.recent(device_ids, limit=max(1, min(limit, 100))) if device_ids else []
    return {"alerts": [_public_alert(alert) for alert in alerts]}


def _require_openid(x_wx_openid: str | None) -> str:
    # 云托管 callContainer 会自动注入 X-WX-OPENID；缺失说明不是小程序会话
    if not x_wx_openid:
        raise HTTPException(status_code=401, detail="missing openid (call via wx.cloud.callContainer)")
    return x_wx_openid


def _public_sample(sample: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in sample.items() if key not in {"id", "created_at"}}
    if isinstance(public.get("sampled_at"), datetime):
        public["sampled_at"] = public["sampled_at"].isoformat()
    return public


def _public_alert(alert: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in alert.items() if key != "id"}
    if isinstance(public.get("created_at"), datetime):
        public["created_at"] = public["created_at"].isoformat()
    return public


def _storage_health() -> dict[str, bool | str]:
    if hasattr(store, "is_healthy"):
        return {"type": store.__class__.__name__, "ok": bool(store.is_healthy())}
    return {"type": store.__class__.__name__, "ok": True}
