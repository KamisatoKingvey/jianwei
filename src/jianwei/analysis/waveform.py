"""R60ABD1 原始波形分析。

固件按秒上报呼吸/心跳波形增量（每通道约 5 个样本，5Hz、int8 居中）。
把连续多秒的增量拼回完整波形后，可以算出比"每分钟一个整数"丰富得多的指标：

- 呼吸节律变异性（深睡时呼吸极规律，REM/清醒时紊乱）——睡眠分期的关键判据；
- 呼吸幅度包络及其漂移；
- 基于包络显著下降的"呼吸事件"筛查（暂停/低通气样波动），给出每小时事件指数。

设计约束：
- 纯 Python，无 numpy/scipy，保持云托管容器精简；信号短（会话级）足够快。
- R60ABD1 输出的是模组内部处理后的波形，不是原始相位；心跳波形 5Hz 偏低，
  精细 HRV 不可靠，因此这里只做呼吸侧分析，心跳仍以聚合值为准。
- 呼吸事件基于胸腔运动包络，缺少气流/血氧，属**非诊断筛查**，不等同临床 AHI。
"""

from __future__ import annotations

from statistics import median, pstdev
from typing import Any


RESP_WAVE_HZ = 5.0  # 固件每通道约 5 个样本/秒

# 临床定义：呼吸事件需持续 ≥10s；低通气≈幅度降 ≥30%，暂停≈降 ≥90%。
# 这里以胸腔运动包络近似，阈值沿用临床定义作为有原则的默认值。
EVENT_MIN_SECONDS = 10.0
HYPOPNEA_RATIO = 0.30  # 包络降到基线的 30% 以下
APNEA_RATIO = 0.10     # 包络降到基线的 10% 以下
BASELINE_WINDOW_SEC = 120.0  # 计算局部基线包络的滑动窗口

# 呼吸峰值检测：最快约 40 次/分 → 最小峰间距 1s（5 个样本）
MIN_PEAK_DISTANCE_SEC = 1.0
MIN_ANALYZABLE_SECONDS = 60.0  # 有效波形不足 1 分钟不出结论


def concat_waveform(samples: list[dict[str, Any]], field: str) -> list[int]:
    """按时间顺序把每秒的波形增量拼成一条连续信号。"""
    ordered = sorted(samples, key=lambda row: row["sampled_at"])
    signal: list[int] = []
    for row in ordered:
        chunk = row.get(field) or []
        signal.extend(int(value) for value in chunk)
    return signal


def analyze_respiration(samples: list[dict[str, Any]], hz: float = RESP_WAVE_HZ) -> dict[str, Any] | None:
    """从一段会话的呼吸波形算出报告用的分析块；波形不足时返回 None。"""
    signal = concat_waveform(samples, "respiration_waveform")
    if len(signal) < MIN_ANALYZABLE_SECONDS * hz:
        return None

    detrended = _detrend(signal, int(hz * 4))
    peaks = _find_peaks(detrended, min_distance=int(MIN_PEAK_DISTANCE_SEC * hz))
    breathing = _breathing_metrics(peaks, detrended, hz)
    events = _respiratory_events(detrended, hz)

    return {
        "waveform_seconds": round(len(signal) / hz, 1),
        "sample_rate_hz": hz,
        "breathing": breathing,
        "respiratory_events": events,
        "disclaimer": "呼吸事件基于胸腔运动包络的非诊断性筛查，不能替代 PSG 或血氧监测。",
    }


def _breathing_metrics(peaks: list[int], signal: list[int], hz: float) -> dict[str, Any]:
    if len(peaks) < 3:
        return {
            "mean_rate_bpm": None,
            "rate_variability": None,
            "amplitude_cv": None,
            "regularity": None,
        }

    intervals = [(peaks[i + 1] - peaks[i]) / hz for i in range(len(peaks) - 1)]
    mean_interval = sum(intervals) / len(intervals)
    mean_rate = 60.0 / mean_interval if mean_interval > 0 else None

    # 呼吸间期变异性：RMSSD（相邻间期差的均方根），越小越规律（越接近深睡）
    diffs = [intervals[i + 1] - intervals[i] for i in range(len(intervals) - 1)]
    rmssd = (sum(d * d for d in diffs) / len(diffs)) ** 0.5 if diffs else 0.0

    # 每次呼吸的峰-谷幅度及其离散度
    amplitudes = _breath_amplitudes(peaks, signal)
    amp_mean = sum(amplitudes) / len(amplitudes) if amplitudes else 0.0
    amp_cv = (pstdev(amplitudes) / amp_mean) if amplitudes and amp_mean > 0 else None

    # 规律度：间期变异系数映射到 0-1（越大越规律）
    interval_cv = (pstdev(intervals) / mean_interval) if mean_interval > 0 else 1.0
    regularity = max(0.0, min(1.0, 1.0 - interval_cv))

    return {
        "mean_rate_bpm": round(mean_rate, 1) if mean_rate else None,
        "rate_variability": round(rmssd, 3),
        "amplitude_cv": round(amp_cv, 3) if amp_cv is not None else None,
        "regularity": round(regularity, 3),
    }


def _respiratory_events(signal: list[float], hz: float) -> dict[str, Any]:
    """基于呼吸幅度包络的暂停/低通气样事件筛查。"""
    envelope = _amplitude_envelope(signal, hz)
    if not envelope:
        return {"count": 0, "index_per_hour": 0.0, "apnea_like": 0, "hypopnea_like": 0, "longest_seconds": 0.0}

    baseline_win = int(BASELINE_WINDOW_SEC * hz)
    min_len = int(EVENT_MIN_SECONDS * hz)

    apnea = 0
    hypopnea = 0
    longest = 0
    run = 0
    run_is_apnea = False

    for index, value in enumerate(envelope):
        baseline = _local_baseline(envelope, index, baseline_win)
        if baseline <= 0:
            run = 0
            run_is_apnea = False
            continue
        ratio = value / baseline
        if ratio < HYPOPNEA_RATIO:
            run += 1
            if ratio < APNEA_RATIO:
                run_is_apnea = True
        else:
            if run >= min_len:
                if run_is_apnea:
                    apnea += 1
                else:
                    hypopnea += 1
                longest = max(longest, run)
            run = 0
            run_is_apnea = False
    if run >= min_len:
        if run_is_apnea:
            apnea += 1
        else:
            hypopnea += 1
        longest = max(longest, run)

    total = apnea + hypopnea
    hours = len(signal) / hz / 3600.0
    index_per_hour = (total / hours) if hours > 0 else 0.0

    return {
        "count": total,
        "index_per_hour": round(index_per_hour, 1),
        "apnea_like": apnea,
        "hypopnea_like": hypopnea,
        "longest_seconds": round(longest / hz, 1),
    }


# ---- 纯 Python DSP 基础函数 ----


def _moving_average(signal: list[int] | list[float], window: int) -> list[float]:
    if window <= 1:
        return [float(value) for value in signal]
    half = window // 2
    result: list[float] = []
    for index in range(len(signal)):
        lo = max(0, index - half)
        hi = min(len(signal), index + half + 1)
        segment = signal[lo:hi]
        result.append(sum(segment) / len(segment))
    return result


def _detrend(signal: list[int], window: int) -> list[float]:
    """减去慢漂移基线，突出呼吸周期波动。"""
    baseline = _moving_average(signal, max(2, window))
    return [signal[i] - baseline[i] for i in range(len(signal))]


def _find_peaks(signal: list[float], min_distance: int) -> list[int]:
    """检测局部极大值：高于自适应阈值且与上一个峰间隔足够。"""
    if len(signal) < 3:
        return []
    positive = [value for value in signal if value > 0]
    threshold = (sum(positive) / len(positive)) * 0.5 if positive else 0.0

    peaks: list[int] = []
    for index in range(1, len(signal) - 1):
        value = signal[index]
        if value <= threshold:
            continue
        if value >= signal[index - 1] and value > signal[index + 1]:
            if peaks and index - peaks[-1] < max(1, min_distance):
                if value > signal[peaks[-1]]:
                    peaks[-1] = index  # 距离过近时保留更高的峰
                continue
            peaks.append(index)
    return peaks


def _breath_amplitudes(peaks: list[int], signal: list[float]) -> list[float]:
    """每次呼吸的峰-谷幅度（相邻两峰之间的最小值到峰的距离）。"""
    amplitudes: list[float] = []
    for i in range(len(peaks) - 1):
        segment = signal[peaks[i]:peaks[i + 1] + 1]
        if segment:
            amplitudes.append(max(segment) - min(segment))
    return amplitudes


def _amplitude_envelope(signal: list[float], hz: float, window_sec: float = 4.0) -> list[float]:
    """逐样本峰-谷包络：短滑窗内的 max-min，直接反映呼吸幅度强弱。

    不依赖峰检测，因此幅度坍缩（暂停/低通气）时包络会真实下降，
    避免"坍缩段无峰、被相邻大幅段桥接"导致漏检。
    """
    if not signal:
        return []
    half = max(1, int(window_sec * hz) // 2)
    envelope: list[float] = []
    for index in range(len(signal)):
        lo = max(0, index - half)
        hi = min(len(signal), index + half + 1)
        segment = signal[lo:hi]
        envelope.append(max(segment) - min(segment) if segment else 0.0)
    return envelope


def _local_baseline(envelope: list[float], index: int, window: int) -> float:
    """事件检测的局部基线：取前向窗口内包络的中位数（对短暂下降稳健）。"""
    lo = max(0, index - window)
    segment = [value for value in envelope[lo:index + 1] if value > 0]
    return median(segment) if segment else 0.0
