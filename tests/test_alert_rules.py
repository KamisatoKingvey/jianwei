from datetime import datetime, timedelta, timezone

from jianwei.alerts.rules import detect_alerts


NIGHT = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)  # 北京时间 23:00
NOON = datetime(2026, 7, 10, 4, 0, tzinfo=timezone.utc)  # 北京时间 12:00


def make_rows(start, count, step_seconds=30, **overrides):
    rows = []
    for index in range(count):
        row = {
            "device_id": "dev-1",
            "sampled_at": start + timedelta(seconds=index * step_seconds),
            "presence": 1,
            "breath_rate": 15,
            "movement": 3,
            "in_bed": 1,
        }
        row.update(overrides)
        rows.append(row)
    return rows


def no_history(device_id, alert_type):
    return None


def test_no_breath_streak_fires_single_alert():
    rows = make_rows(NIGHT, 4, breath_rate=0, movement=0)

    alerts = detect_alerts("dev-1", rows, no_history)

    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "suspected_no_breath"
    assert alerts[0]["device_id"] == "dev-1"


def test_normal_breathing_fires_nothing():
    rows = make_rows(NIGHT, 10)

    assert detect_alerts("dev-1", rows, no_history) == []


def test_low_breath_rate_fires():
    rows = make_rows(NIGHT, 3, breath_rate=6)

    alerts = detect_alerts("dev-1", rows, no_history)

    assert [alert["alert_type"] for alert in alerts] == ["low_breath_rate"]


def test_cooldown_suppresses_repeat_alert():
    rows = make_rows(NIGHT, 3, breath_rate=0, movement=0)

    def recent_history(device_id, alert_type):
        return NIGHT - timedelta(minutes=5)

    assert detect_alerts("dev-1", rows, recent_history) == []


def test_alert_fires_again_after_cooldown():
    rows = make_rows(NIGHT, 3, breath_rate=0, movement=0)

    def old_history(device_id, alert_type):
        return NIGHT - timedelta(hours=2)

    alerts = detect_alerts("dev-1", rows, old_history)

    assert len(alerts) == 1


def test_night_bed_exit_fires_at_night_only():
    def rows_at(start):
        rows = make_rows(start, 4)
        rows[2]["in_bed"] = 0
        rows[3]["in_bed"] = 0
        return rows

    night_alerts = detect_alerts("dev-1", rows_at(NIGHT), no_history)
    noon_alerts = detect_alerts("dev-1", rows_at(NOON), no_history)

    assert [alert["alert_type"] for alert in night_alerts] == ["night_bed_exit"]
    assert noon_alerts == []


def test_breath_zero_with_high_movement_is_not_alert():
    rows = make_rows(NIGHT, 5, breath_rate=0, movement=50)

    assert detect_alerts("dev-1", rows, no_history) == []
