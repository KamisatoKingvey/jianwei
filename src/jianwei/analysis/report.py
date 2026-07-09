from __future__ import annotations

from statistics import mean
from typing import Any

from jianwei.analysis.session import SleepSession
from jianwei.radar.r60abd1 import RadarEvent


def build_sleep_report(session: SleepSession) -> dict[str, Any]:
    respiration = [event for event in session.events if event.type == "respiration_rate"]
    heart = [event for event in session.events if event.type == "heart_rate"]
    motion = [event for event in session.events if event.type == "motion_amplitude"]
    bed_states = [event for event in session.events if event.type == "bed_state"]

    duration_minutes = _duration_minutes(session)
    valid_respiration = [event.value for event in respiration if isinstance(event.value, int) and event.value > 0]
    suspected_no_breath_events = _count_suspected_no_breath_events(respiration, motion)
    high_motion_events = sum(1 for event in motion if isinstance(event.value, int) and event.value >= 31)
    bed_exit_count = sum(1 for event in bed_states if event.value == "out_of_bed")

    quality_reasons: list[str] = []
    if duration_minutes < 20:
        quality_reasons.append("有效监测时长不足")
    if len(respiration) < 20:
        quality_reasons.append("呼吸采样数量不足")

    quality_level = "low" if quality_reasons else "usable"
    risk_level = _risk_level(quality_level, suspected_no_breath_events, high_motion_events)
    summary = _summary_text(quality_level, risk_level, suspected_no_breath_events)

    return {
        "session_id": session.session_id,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "summary": summary,
        "quality": {
            "level": quality_level,
            "reasons": quality_reasons,
        },
        "risk": {
            "level": risk_level,
            "label": {"low": "整体平稳", "attention": "建议关注", "invalid": "数据不足"}[risk_level],
        },
        "metrics": {
            "duration_minutes": round(duration_minutes, 1),
            "average_respiration": round(mean(valid_respiration), 1) if valid_respiration else None,
            "average_heart_rate": round(mean([event.value for event in heart if isinstance(event.value, int)]), 1)
            if heart
            else None,
            "high_motion_events": high_motion_events,
            "bed_exit_count": bed_exit_count,
            "suspected_no_breath_events": suspected_no_breath_events,
        },
        "recommendations": _recommendations(risk_level, quality_level),
        "disclaimer": "本报告用于家庭健康科普与风险提示，属于非诊断结果，不能替代医院 PSG 或医生判断。",
    }


def _duration_minutes(session: SleepSession) -> float:
    if not session.started_at or not session.ended_at:
        return 0.0
    return max(0.0, (session.ended_at - session.started_at).total_seconds() / 60)


def _count_suspected_no_breath_events(respiration: list[RadarEvent], motion: list[RadarEvent]) -> int:
    motion_by_time = {event.timestamp: event.value for event in motion if event.timestamp is not None}
    streak = 0
    count = 0
    for event in respiration:
        motion_value = motion_by_time.get(event.timestamp, 0)
        low_motion = isinstance(motion_value, int) and motion_value <= 10
        if event.value == 0 and low_motion:
            streak += 1
        else:
            if streak >= 3:
                count += 1
            streak = 0
    if streak >= 3:
        count += 1
    return count


def _risk_level(quality_level: str, suspected_no_breath_events: int, high_motion_events: int) -> str:
    if quality_level == "low":
        return "invalid"
    if suspected_no_breath_events >= 1 or high_motion_events >= 5:
        return "attention"
    return "low"


def _summary_text(quality_level: str, risk_level: str, suspected_no_breath_events: int) -> str:
    if quality_level == "low":
        return "昨晚数据覆盖不足，建议检查设备安装角度与供电后继续观察。"
    if risk_level == "attention":
        return f"昨晚出现 {suspected_no_breath_events} 段疑似呼吸中断样波动，建议结合连续多晚趋势关注。"
    return "昨晚呼吸与体动整体平稳，暂未发现需要特别关注的异常波动。"


def _recommendations(risk_level: str, quality_level: str) -> list[str]:
    if quality_level == "low":
        return ["确认雷达位于床头上方并向下倾斜 30-45 度。", "确认胸腔距离雷达约 0.4-1.5 米。"]
    if risk_level == "attention":
        return ["连续观察 3-7 晚趋势。", "如果伴随严重打鼾、白天嗜睡或高血压，建议咨询医生。"]
    return ["保持当前睡眠习惯。", "继续积累长期趋势，关注明显变化。"]
