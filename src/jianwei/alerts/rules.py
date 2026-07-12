from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from jianwei.analysis.segments import BEIJING


DEFAULT_COOLDOWN_MINUTES = 30

NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 7

# 与报告算法保持一致：在床且低体动时呼吸值连续为 0 视为疑似中断
NO_BREATH_STREAK = 3
LOW_BREATH_THRESHOLD = 10
LOW_BREATH_STREAK = 3


def detect_alerts(
    device_id: str,
    rows: list[dict[str, Any]],
    last_time: Callable[[str, str], datetime | None],
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
) -> list[dict[str, Any]]:
    """对一批入库采样做实时规则检测，返回需要记录/推送的告警。

    last_time(device_id, alert_type) 用于冷却：同类型告警在冷却窗口内不重复。
    """
    candidates = [
        *_breath_alerts(rows),
        *_night_bed_exit_alerts(rows),
    ]

    alerts: list[dict[str, Any]] = []
    fired_types: set[str] = set()
    for candidate in candidates:
        alert_type = candidate["alert_type"]
        if alert_type in fired_types:
            continue
        previous = last_time(device_id, alert_type)
        if previous is not None and candidate["created_at"] - previous < timedelta(minutes=cooldown_minutes):
            continue
        fired_types.add(alert_type)
        alerts.append({"device_id": device_id, **candidate})
    return alerts


def _breath_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    no_breath_streak = 0
    low_breath_streak = 0

    for row in rows:
        active = row.get("presence") == 1 or row.get("in_bed") == 1
        breath = int(row.get("breath_rate") or 0)
        movement = int(row.get("movement") or 0)

        if active and breath == 0 and movement <= 10:
            no_breath_streak += 1
        else:
            no_breath_streak = 0

        if active and 0 < breath < LOW_BREATH_THRESHOLD:
            low_breath_streak += 1
        else:
            low_breath_streak = 0

        if no_breath_streak == NO_BREATH_STREAK:
            alerts.append(
                {
                    "alert_type": "suspected_no_breath",
                    "level": "attention",
                    "message": "在床低体动状态下呼吸值连续为 0，出现疑似呼吸中断样波动，建议留意。",
                    "created_at": row["sampled_at"],
                }
            )
        if low_breath_streak == LOW_BREATH_STREAK:
            alerts.append(
                {
                    "alert_type": "low_breath_rate",
                    "level": "attention",
                    "message": f"呼吸频率持续低于 {LOW_BREATH_THRESHOLD} 次/分钟，建议关注。",
                    "created_at": row["sampled_at"],
                }
            )
    return alerts


def _night_bed_exit_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    prev_in_bed: int | None = None
    for row in rows:
        in_bed = row.get("in_bed")
        if in_bed is None:
            continue
        if prev_in_bed == 1 and in_bed == 0 and _is_night(row["sampled_at"]):
            alerts.append(
                {
                    "alert_type": "night_bed_exit",
                    "level": "info",
                    "message": "夜间检测到离床，若长时间未回床建议查看情况。",
                    "created_at": row["sampled_at"],
                }
            )
        prev_in_bed = in_bed
    return alerts


def _is_night(timestamp: datetime) -> bool:
    hour = timestamp.astimezone(BEIJING).hour
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR
