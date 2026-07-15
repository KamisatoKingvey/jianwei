"""波形分析模块的算法验证：用合成呼吸信号确认指标和事件检测符合预期。"""

import math
from datetime import datetime, timedelta, timezone

from jianwei.analysis.waveform import (
    RESP_WAVE_HZ,
    analyze_respiration,
    concat_waveform,
)


HZ = RESP_WAVE_HZ
BASE_TIME = datetime(2026, 7, 15, 23, 0, tzinfo=timezone.utc)


def _sine_breathing(seconds: float, rate_bpm: float, amplitude: float = 40.0, hz: float = HZ):
    """生成正弦呼吸波形，返回逐样本 int 列表（居中于 0）。"""
    freq = rate_bpm / 60.0
    n = int(seconds * hz)
    return [int(round(amplitude * math.sin(2 * math.pi * freq * i / hz))) for i in range(n)]


def _samples_from_signal(signal, hz: float = HZ):
    """把一条连续波形切成每秒 5 个样本的采样行，模拟固件上报后的落库形态。"""
    per_second = int(hz)
    rows = []
    for second, offset in enumerate(range(0, len(signal), per_second)):
        rows.append(
            {
                "sampled_at": BASE_TIME + timedelta(seconds=second),
                "respiration_waveform": signal[offset:offset + per_second],
                "heart_waveform": [],
            }
        )
    return rows


def test_concat_waveform_orders_by_time():
    rows = [
        {"sampled_at": BASE_TIME + timedelta(seconds=1), "respiration_waveform": [3, 4]},
        {"sampled_at": BASE_TIME, "respiration_waveform": [1, 2]},
    ]
    assert concat_waveform(rows, "respiration_waveform") == [1, 2, 3, 4]


def test_regular_breathing_reports_rate_and_high_regularity():
    signal = _sine_breathing(seconds=180, rate_bpm=15)
    result = analyze_respiration(_samples_from_signal(signal))

    assert result is not None
    breathing = result["breathing"]
    # 15 次/分的正弦，检测出的呼吸率应接近 15
    assert breathing["mean_rate_bpm"] is not None
    assert abs(breathing["mean_rate_bpm"] - 15) <= 2
    # 完全规律 → 规律度高、节律变异性低
    assert breathing["regularity"] >= 0.8
    assert breathing["rate_variability"] <= 0.3
    # 稳定呼吸不应触发呼吸事件
    assert result["respiratory_events"]["count"] == 0


def test_short_signal_returns_none():
    signal = _sine_breathing(seconds=30, rate_bpm=15)
    assert analyze_respiration(_samples_from_signal(signal)) is None


def test_amplitude_collapse_is_flagged_as_respiratory_event():
    # 正常呼吸 90s → 幅度坍缩到近 0 持续 20s（暂停样）→ 恢复 90s
    normal_a = _sine_breathing(seconds=90, rate_bpm=15, amplitude=40)
    apnea = _sine_breathing(seconds=20, rate_bpm=15, amplitude=2)
    normal_b = _sine_breathing(seconds=90, rate_bpm=15, amplitude=40)
    signal = normal_a + apnea + normal_b

    result = analyze_respiration(_samples_from_signal(signal))
    assert result is not None
    events = result["respiratory_events"]
    assert events["count"] >= 1
    assert events["apnea_like"] >= 1
    assert events["longest_seconds"] >= 10.0


def test_irregular_breathing_has_lower_regularity_than_regular():
    regular = analyze_respiration(_samples_from_signal(_sine_breathing(180, 15)))

    # 变速呼吸：前半 12/分、后半 20/分，规律度应明显下降
    part_a = _sine_breathing(90, 12)
    part_b = _sine_breathing(90, 20)
    irregular = analyze_respiration(_samples_from_signal(part_a + part_b))

    assert regular["breathing"]["regularity"] > irregular["breathing"]["regularity"]
