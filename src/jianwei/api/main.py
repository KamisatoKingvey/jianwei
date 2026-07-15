from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from jianwei.agent.prompts import REPORT_INSIGHTS_PROMPT, build_chat_prompt
from jianwei.agent.runner import AgentUnavailable, ClaudeAgentRunner
from jianwei.agent.tools import AgentContext
from jianwei.alerts.rules import detect_alerts
from jianwei.analysis.report import build_sleep_report
from jianwei.analysis.segments import session_report, slice_sessions
from jianwei.analysis.session import build_session
from jianwei.config import load_env_file
from jianwei.notify.wechat import send_alert_notifications
from jianwei.radar.r60abd1 import event_from_dict, events_from_hex_log
from jianwei.radar.simulator import generate_demo_events
from jianwei.storage.device_store import BindCodeConflictError
from jianwei.storage.factory import (
    build_agent_store,
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

app = FastAPI(title="Jianwei Backend", version="0.3.1")
store = build_event_store(DATA_PATH)
sample_store = build_sample_store(SAMPLES_PATH)
device_store = build_device_store(DEVICES_PATH)
alert_store = build_alert_store(ALERTS_PATH)
agent_store = build_agent_store(ROOT / "data" / "agent_messages.jsonl", ROOT / "data" / "agent_insights.jsonl")
agent_runner = ClaudeAgentRunner()

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
    # R60ABD1 原始波形增量：每秒约 5 个样本/通道，5Hz、int8 居中（raw-128）。
    # 固件按秒随聚合值一起上报，是分期/呼吸事件等高级算法的输入。
    respiration_waveform: list[int] | None = None
    heart_waveform: list[int] | None = None


class RegisterDeviceRequest(BaseModel):
    device_id: str = Field(min_length=1)
    secret: str | None = None
    # 可选：指定绑定码（与设备标签一致，6 位字母/数字）；缺省时由后端随机生成
    bind_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{6}$")


class BindDeviceRequest(BaseModel):
    bind_code: str = Field(min_length=1)
    openid: str | None = None


_template_count = 0


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "jianwei-backend",
        "version": app.version,
        "storage": _storage_health(),
        "agent": agent_runner.diagnostics(),
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
                "respiration_waveform": list(item.respiration_waveform or []),
                "heart_waveform": list(item.heart_waveform or []),
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

    try:
        device = device_store.upsert_device(
            request.device_id,
            secret=request.secret,
            bind_code=request.bind_code,
        )
    except BindCodeConflictError:
        raise HTTPException(status_code=409, detail="bind code already used by another device")
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
def my_devices(
    x_wx_openid: str | None = Header(default=None),
    x_wx_source: str | None = Header(default=None),
) -> dict[str, Any]:
    openid = _require_openid(x_wx_openid, x_wx_source)
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
# 见微睡眠助手（agent）
# ---------------------------------------------------------------------------


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


AGENT_HISTORY_LIMIT = 20


# callContainer 网关 15 秒硬超时（官方不可调），而 agent 一次多轮推理往往超过
# 15 秒，所以 chat 必须异步化：POST 立即返回 task_id，前端轮询任务结果。
AGENT_TASK_PENDING_TIMEOUT = timedelta(seconds=180)


@app.post("/api/agent/chat")
async def agent_chat(
    request: AgentChatRequest,
    background_tasks: BackgroundTasks,
    x_wx_openid: str | None = Header(default=None),
    x_wx_source: str | None = Header(default=None),
) -> dict[str, Any]:
    openid = _require_openid(x_wx_openid, x_wx_source)
    if not agent_runner.available:
        raise HTTPException(status_code=503, detail="助手暂未开通")

    _enforce_daily_limit(openid)

    conversation_id = request.conversation_id or uuid.uuid4().hex
    if request.conversation_id:
        owner = agent_store.conversation_owner(conversation_id)
        if owner is not None and owner != openid:
            raise HTTPException(status_code=403, detail="conversation belongs to another user")

    task_id = uuid.uuid4().hex
    agent_store.create_task(task_id, openid, conversation_id, request.message, datetime.now(timezone.utc))
    background_tasks.add_task(_execute_chat_task, task_id, openid, conversation_id, request.message)

    return {"conversation_id": conversation_id, "task_id": task_id, "status": "pending"}


async def _execute_chat_task(task_id: str, openid: str, conversation_id: str, message: str) -> None:
    try:
        history = agent_store.recent_messages(conversation_id, limit=AGENT_HISTORY_LIMIT)
        prompt = build_chat_prompt(history, message)
        reply = await agent_runner.run(prompt, _agent_context(openid))
    except AgentUnavailable:
        agent_store.finish_task(task_id, "failed", datetime.now(timezone.utc), error="助手暂未开通")
        return
    except Exception:
        logger.exception("agent chat task %s failed for %s", task_id, openid)
        agent_store.finish_task(task_id, "failed", datetime.now(timezone.utc), error="助手暂时不可用，请稍后再试")
        return

    # 免责声明由小程序在输入框下方固定展示，不再拼进每条回复
    now = datetime.now(timezone.utc)
    agent_store.append_message(conversation_id, openid, "user", message, now)
    agent_store.append_message(conversation_id, openid, "assistant", reply, now)
    agent_store.finish_task(task_id, "done", now, reply=reply)


@app.get("/api/agent/chat/tasks/{task_id}")
def agent_chat_task(
    task_id: str,
    x_wx_openid: str | None = Header(default=None),
    x_wx_source: str | None = Header(default=None),
) -> dict[str, Any]:
    openid = _require_openid(x_wx_openid, x_wx_source)
    task = agent_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["openid"] != openid:
        raise HTTPException(status_code=403, detail="task belongs to another user")

    status = task["status"]
    error = task.get("error")
    # 实例被回收会留下永远 pending 的任务，超过时限直接按失败上报
    if status == "pending" and datetime.now(timezone.utc) - task["created_at"] > AGENT_TASK_PENDING_TIMEOUT:
        status = "failed"
        error = "助手响应超时，请稍后再试"

    return {
        "task_id": task_id,
        "conversation_id": task["conversation_id"],
        "status": status,
        "reply": task.get("reply") or "",
        "error": error or "",
    }


@app.get("/api/agent/conversations/{conversation_id}")
def agent_conversation(
    conversation_id: str,
    x_wx_openid: str | None = Header(default=None),
    x_wx_source: str | None = Header(default=None),
) -> dict[str, Any]:
    openid = _require_openid(x_wx_openid, x_wx_source)
    owner = agent_store.conversation_owner(conversation_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if owner != openid:
        raise HTTPException(status_code=403, detail="conversation belongs to another user")

    messages = agent_store.recent_messages(conversation_id, limit=100)
    return {
        "conversation_id": conversation_id,
        "messages": [
            {"role": row["role"], "content": row["content"], "created_at": row["created_at"].isoformat()}
            for row in messages
        ],
    }


# 防止缓存生成完成前反复进入仪表盘触发重复的 agent 调用（单实例内去重即可）
_insights_in_flight: set[str] = set()


@app.get("/api/agent/report-insights/{device_id}")
async def agent_report_insights(
    device_id: str,
    background_tasks: BackgroundTasks,
    x_wx_openid: str | None = Header(default=None),
    x_wx_source: str | None = Header(default=None),
) -> dict[str, Any]:
    openid = _require_openid(x_wx_openid, x_wx_source)
    if device_id not in device_store.devices_for_user(openid):
        raise HTTPException(status_code=403, detail="device not bound to current user")

    start = datetime.now(timezone.utc) - timedelta(hours=36)
    samples = list(sample_store.iter_device(device_id, start=start))
    sessions = slice_sessions(samples)
    if not sessions:
        raise HTTPException(status_code=404, detail="no monitoring session found")

    report = session_report(device_id, sessions[-1])
    session_id = report["session_id"]

    cached = agent_store.get_insight(device_id, session_id)
    if cached:
        return {"device_id": device_id, "session_id": session_id, "source": "agent", "insights": cached}

    # agent 生成可能超过 callContainer 的 15 秒硬超时：先返回规则版摘要，
    # 后台生成 agent 解读写入缓存，下次进入页面自然升级成 agent 版。
    key = f"{device_id}:{session_id}"
    if agent_runner.available and key not in _insights_in_flight:
        _insights_in_flight.add(key)
        background_tasks.add_task(_generate_report_insight, openid, device_id, session_id)

    return {"device_id": device_id, "session_id": session_id, "source": "rules", "insights": report["summary"]}


async def _generate_report_insight(openid: str, device_id: str, session_id: str) -> None:
    try:
        insights = await agent_runner.run(
            REPORT_INSIGHTS_PROMPT.format(device_id=device_id),
            _agent_context(openid),
        )
        agent_store.put_insight(device_id, session_id, insights)
    except Exception:
        logger.exception("agent insights failed for %s", device_id)
    finally:
        _insights_in_flight.discard(f"{device_id}:{session_id}")


def _agent_context(openid: str) -> AgentContext:
    return AgentContext(
        openid=openid,
        sample_store=sample_store,
        device_store=device_store,
        alert_store=alert_store,
    )


def _enforce_daily_limit(openid: str) -> None:
    limit = int(os.environ.get("JIANWEI_AGENT_DAILY_LIMIT", "30"))
    since = datetime.now(timezone.utc) - timedelta(days=1)
    if agent_store.count_user_messages_since(openid, since) >= limit:
        raise HTTPException(status_code=429, detail="今日对话次数已用完，明天再来吧")


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
    x_wx_source: str | None = Header(default=None),
    limit: int = 20,
) -> dict[str, Any]:
    openid = _require_openid(x_wx_openid, x_wx_source)
    device_ids = device_store.devices_for_user(openid)
    alerts = alert_store.recent(device_ids, limit=max(1, min(limit, 100))) if device_ids else []
    return {"alerts": [_public_alert(alert) for alert in alerts]}


def _require_openid(x_wx_openid: str | None, x_wx_source: str | None = None) -> str:
    """校验请求确实来自微信生态。

    云托管只在 callContainer 链路上注入可信的 X-WX-OPENID 和 X-WX-SOURCE，
    并会剥离客户端伪造的同名头；公网域名不做任何过滤（官方明确不提供防护），
    所以没有 X-WX-SOURCE 的请求里 openid 是不可信的，必须拒绝——否则任何人
    都能伪造 openid 调用 agent 并消耗模型额度。
    本地调试可设 JIANWEI_ALLOW_PUBLIC_OPENID=1 放行。
    """
    if not x_wx_openid:
        raise HTTPException(status_code=401, detail="missing openid (call via wx.cloud.callContainer)")

    if not x_wx_source and os.environ.get("JIANWEI_ALLOW_PUBLIC_OPENID") != "1":
        raise HTTPException(status_code=401, detail="untrusted request source (call via wx.cloud.callContainer)")

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
