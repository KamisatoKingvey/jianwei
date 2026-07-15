from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from jianwei.analysis.report import build_sleep_report
from jianwei.analysis.session import build_session
from jianwei.analysis.waveform import analyze_respiration
from jianwei.radar.r60abd1 import RadarEvent


BEIJING = timezone(timedelta(hours=8))

SESSION_GAP_MINUTES = 15


def slice_sessions(samples: list[dict[str, Any]], gap_minutes: int = SESSION_GAP_MINUTES) -> list[list[dict[str, Any]]]:
    """把按时间排好序的连续采样流切成若干次"监测会话"。

    只保留有人/在床的采样；相邻采样间隔超过 gap_minutes 视为一次会话结束
    （对应离床、无人或设备断电时段）。
    """
    active = [
        sample
        for sample in samples
        if sample.get("presence") == 1 or sample.get("in_bed") == 1
    ]
    active.sort(key=lambda sample: sample["sampled_at"])

    sessions: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for sample in active:
        if current and sample["sampled_at"] - current[-1]["sampled_at"] > timedelta(minutes=gap_minutes):
            sessions.append(current)
            current = []
        current.append(sample)
    if current:
        sessions.append(current)
    return sessions


def session_report(device_id: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    """把一段会话的平铺采样转换成 RadarEvent 流并生成睡眠报告。"""
    events = events_from_samples(samples)
    started_at = samples[0]["sampled_at"]
    session_id = "night-" + started_at.astimezone(BEIJING).strftime("%Y%m%d-%H%M")

    report = build_sleep_report(build_session(session_id, events))
    report["device_id"] = device_id
    report["sample_count"] = len(samples)
    report["environment"] = _environment_summary(samples)
    report["device_sleep"] = _device_sleep_summary(samples)
    # 波形分析（呼吸节律变异性 + 呼吸事件筛查）；波形不足时为 None，前端需容错
    report["respiration_analysis"] = analyze_respiration(samples)
    return report


def events_from_samples(samples: list[dict[str, Any]]) -> list[RadarEvent]:
    events: list[RadarEvent] = []
    prev_in_bed: int | None = None
    prev_presence: int | None = None

    for sample in samples:
        timestamp: datetime = sample["sampled_at"]

        events.append(RadarEvent("respiration_rate", int(sample.get("breath_rate") or 0), "rpm", source="esp32", timestamp=timestamp))
        events.append(RadarEvent("motion_amplitude", int(sample.get("movement") or 0), source="esp32", timestamp=timestamp))

        heart_rate = int(sample.get("heart_rate") or 0)
        if heart_rate > 0:
            events.append(RadarEvent("heart_rate", heart_rate, "bpm", source="esp32", timestamp=timestamp))

        in_bed = sample.get("in_bed")
        if in_bed is not None and in_bed != prev_in_bed:
            if prev_in_bed is not None:
                events.append(
                    RadarEvent("bed_state", "in_bed" if in_bed == 1 else "out_of_bed", source="esp32", timestamp=timestamp)
                )
            prev_in_bed = in_bed

        presence = sample.get("presence")
        if presence is not None and presence != prev_presence:
            events.append(
                RadarEvent("presence", "present" if presence == 1 else "absent", source="esp32", timestamp=timestamp)
            )
            prev_presence = presence

    return events


def _environment_summary(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    co2 = [int(sample["co2"]) for sample in samples if sample.get("co2")]
    temperature = [float(sample["temperature"]) for sample in samples if sample.get("temperature")]
    humidity = [float(sample["humidity"]) for sample in samples if sample.get("humidity")]
    if not (co2 or temperature or humidity):
        return None
    return {
        "average_co2": round(mean(co2)) if co2 else None,
        "max_co2": max(co2) if co2 else None,
        "average_temperature": round(mean(temperature), 1) if temperature else None,
        "average_humidity": round(mean(humidity), 1) if humidity else None,
    }


def _device_sleep_summary(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    """雷达模组自带的睡眠状态/评分汇总，作为算法报告的参考对照。"""
    stages = [sample.get("sleep_stage") for sample in samples if sample.get("sleep_stage") is not None]
    scores = [int(sample["sleep_score"]) for sample in samples if sample.get("sleep_score")]
    if not stages and not scores:
        return None

    stage_counts: dict[str, int] = {}
    for stage in stages:
        key = str(stage)
        stage_counts[key] = stage_counts.get(key, 0) + 1

    return {
        "stage_sample_counts": stage_counts or None,
        "latest_score": scores[-1] if scores else None,
    }
