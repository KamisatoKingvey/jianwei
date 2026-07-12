"""Agent 可调用的只读数据工具。

不依赖 claude-agent-sdk（便于单测和无 SDK 环境）；runner 在运行时把这些
实现包装成 SDK 的 @tool。所有工具通过 contextvar 拿到当前请求的 openid
和存储层，device_id 必须属于该用户，否则返回权限错误——这是 agent 的
数据安全边界。
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from jianwei.analysis.segments import session_report, slice_sessions


@dataclass
class AgentContext:
    openid: str
    sample_store: Any
    device_store: Any
    alert_store: Any


_current_context: ContextVar[AgentContext | None] = ContextVar("jianwei_agent_context", default=None)

ONLINE_WINDOW_MINUTES = 5
REPORT_LOOKBACK_HOURS = 36


def set_context(context: AgentContext) -> None:
    _current_context.set(context)


def get_context() -> AgentContext:
    context = _current_context.get()
    if context is None:
        raise RuntimeError("agent context is not set")
    return context


def _authorized_device(context: AgentContext, device_id: str) -> str | None:
    if device_id not in context.device_store.devices_for_user(context.openid):
        return "权限错误：该设备不属于当前用户或尚未绑定。"
    return None


def _as_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def get_my_devices() -> str:
    """当前用户绑定的设备列表及在线状态。"""
    context = get_context()
    now = datetime.now(timezone.utc)
    devices = []
    for device_id in context.device_store.devices_for_user(context.openid):
        latest = context.sample_store.latest(device_id)
        last_seen = latest["sampled_at"] if latest else None
        devices.append(
            {
                "device_id": device_id,
                "online": bool(last_seen and now - last_seen < timedelta(minutes=ONLINE_WINDOW_MINUTES)),
                "last_seen": last_seen.isoformat() if last_seen else None,
            }
        )
    return _as_json({"devices": devices})


def get_latest_report(device_id: str) -> str:
    """指定设备最近一晚的睡眠报告（含指标、环境、建议）。"""
    context = get_context()
    if error := _authorized_device(context, device_id):
        return _as_json({"error": error})

    start = datetime.now(timezone.utc) - timedelta(hours=REPORT_LOOKBACK_HOURS)
    samples = list(context.sample_store.iter_device(device_id, start=start))
    sessions = slice_sessions(samples)
    if not sessions:
        return _as_json({"error": "最近 36 小时内没有监测会话，可能昨晚未监测。"})
    return _as_json(session_report(device_id, sessions[-1]))


def get_night_reports(device_id: str, days: int = 7) -> str:
    """指定设备最近 N 晚的报告摘要，用于趋势分析。"""
    context = get_context()
    if error := _authorized_device(context, device_id):
        return _as_json({"error": error})

    days = max(1, min(int(days), 31))
    start = datetime.now(timezone.utc) - timedelta(days=days)
    samples = list(context.sample_store.iter_device(device_id, start=start))
    summaries = []
    for session in slice_sessions(samples):
        report = session_report(device_id, session)
        summaries.append(
            {
                "session_id": report["session_id"],
                "started_at": report["started_at"],
                "duration_minutes": report["metrics"]["duration_minutes"],
                "average_respiration": report["metrics"]["average_respiration"],
                "average_heart_rate": report["metrics"]["average_heart_rate"],
                "suspected_no_breath_events": report["metrics"]["suspected_no_breath_events"],
                "bed_exit_count": report["metrics"]["bed_exit_count"],
                "quality": report["quality"]["level"],
                "risk": report["risk"]["level"],
                "environment": report.get("environment"),
            }
        )
    return _as_json({"device_id": device_id, "days": days, "nights": summaries})


def get_realtime_status(device_id: str) -> str:
    """指定设备当前的实时状态（是否在线、在床、最新呼吸/心率/环境）。"""
    context = get_context()
    if error := _authorized_device(context, device_id):
        return _as_json({"error": error})

    latest = context.sample_store.latest(device_id)
    if latest is None:
        return _as_json({"error": "该设备还没有上传过数据。"})

    now = datetime.now(timezone.utc)
    return _as_json(
        {
            "device_id": device_id,
            "online": now - latest["sampled_at"] < timedelta(minutes=ONLINE_WINDOW_MINUTES),
            "sampled_at": latest["sampled_at"].isoformat(),
            "presence": latest.get("presence"),
            "in_bed": latest.get("in_bed"),
            "breath_rate": latest.get("breath_rate"),
            "heart_rate": latest.get("heart_rate"),
            "movement": latest.get("movement"),
            "co2": latest.get("co2"),
            "temperature": latest.get("temperature"),
            "humidity": latest.get("humidity"),
        }
    )


def get_recent_alerts(device_id: str | None = None, limit: int = 10) -> str:
    """最近的告警记录；不传 device_id 时查当前用户的全部设备。"""
    context = get_context()
    if device_id:
        if error := _authorized_device(context, device_id):
            return _as_json({"error": error})
        device_ids = [device_id]
    else:
        device_ids = context.device_store.devices_for_user(context.openid)

    limit = max(1, min(int(limit), 50))
    alerts = context.alert_store.recent(device_ids, limit=limit) if device_ids else []
    return _as_json(
        {
            "alerts": [
                {
                    "device_id": alert["device_id"],
                    "type": alert["alert_type"],
                    "level": alert["level"],
                    "message": alert["message"],
                    "created_at": alert["created_at"].isoformat()
                    if isinstance(alert["created_at"], datetime)
                    else alert["created_at"],
                }
                for alert in alerts
            ]
        }
    )


# runner 据此注册 SDK 工具：名称、描述、参数 schema、实现
TOOL_SPECS = [
    ("get_my_devices", "查询当前用户绑定的所有睡眠监测设备及其在线状态", {}, get_my_devices),
    ("get_latest_report", "获取指定设备最近一晚的完整睡眠报告", {"device_id": str}, get_latest_report),
    ("get_night_reports", "获取指定设备最近 N 晚的报告摘要，用于趋势分析", {"device_id": str, "days": int}, get_night_reports),
    ("get_realtime_status", "获取指定设备当前的实时监测状态", {"device_id": str}, get_realtime_status),
    ("get_recent_alerts", "查询最近的异常告警记录", {"device_id": str, "limit": int}, get_recent_alerts),
]
