from datetime import datetime, timedelta, timezone

from jianwei.analysis.segments import events_from_samples, session_report, slice_sessions


BASE = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)  # 北京时间 23:00


def make_samples(start, count, step_seconds=30, **overrides):
    samples = []
    for index in range(count):
        sample = {
            "device_id": "dev-1",
            "sampled_at": start + timedelta(seconds=index * step_seconds),
            "clock_synced": True,
            "presence": 1,
            "activity": 1,
            "breath_rate": 15,
            "heart_rate": 68,
            "sleep_stage": 1,
            "sleep_score": 80,
            "in_bed": 1,
            "movement": 3,
            "distance": 60,
            "co2": 900,
            "temperature": 24.0,
            "humidity": 55.0,
        }
        sample.update(overrides)
        samples.append(sample)
    return samples


def test_slice_sessions_splits_on_long_gap():
    first = make_samples(BASE, 30)
    second = make_samples(BASE + timedelta(hours=2), 30)

    sessions = slice_sessions(first + second)

    assert len(sessions) == 2
    assert len(sessions[0]) == 30
    assert sessions[1][0]["sampled_at"] == BASE + timedelta(hours=2)


def test_slice_sessions_drops_absent_samples():
    present = make_samples(BASE, 10)
    absent = make_samples(BASE + timedelta(minutes=5), 5, presence=0, in_bed=0)

    sessions = slice_sessions(present + absent)

    assert len(sessions) == 1
    assert len(sessions[0]) == 10


def test_events_from_samples_emits_bed_state_only_on_transitions():
    samples = make_samples(BASE, 4)
    samples[2]["in_bed"] = 0
    samples[3]["in_bed"] = 0

    events = events_from_samples(samples)

    bed_events = [event for event in events if event.type == "bed_state"]
    assert len(bed_events) == 1
    assert bed_events[0].value == "out_of_bed"


def test_session_report_includes_metrics_and_environment():
    samples = make_samples(BASE, 60)  # 30 分钟

    report = session_report("dev-1", samples)

    assert report["device_id"] == "dev-1"
    assert report["session_id"].startswith("night-20260710-2300")
    assert report["quality"]["level"] == "usable"
    assert report["metrics"]["average_respiration"] == 15
    assert report["metrics"]["average_heart_rate"] == 68
    assert report["metrics"]["duration_minutes"] == 29.5
    assert report["environment"]["average_co2"] == 900
    assert report["environment"]["average_temperature"] == 24.0
    assert report["device_sleep"]["latest_score"] == 80
    assert report["sample_count"] == 60


def test_session_report_flags_short_session_as_low_quality():
    samples = make_samples(BASE, 10)  # 不足 20 分钟

    report = session_report("dev-1", samples)

    assert report["quality"]["level"] == "low"
    assert report["risk"]["level"] == "invalid"
