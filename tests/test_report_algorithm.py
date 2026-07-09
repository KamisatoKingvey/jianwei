from jianwei.analysis.report import build_sleep_report
from jianwei.analysis.session import build_session
from jianwei.radar.simulator import generate_demo_events


def test_demo_report_detects_conservative_breathing_risk():
    session = build_session("session-demo", list(generate_demo_events()))

    report = build_sleep_report(session)

    assert report["session_id"] == "session-demo"
    assert report["risk"]["level"] == "attention"
    assert report["metrics"]["suspected_no_breath_events"] >= 1
    assert "非诊断" in report["disclaimer"]
    assert report["summary"]


def test_report_marks_low_quality_when_coverage_is_too_low():
    events = list(generate_demo_events())[:5]
    session = build_session("session-short", events)

    report = build_sleep_report(session)

    assert report["quality"]["level"] == "low"
    assert "有效监测时长不足" in report["quality"]["reasons"]
