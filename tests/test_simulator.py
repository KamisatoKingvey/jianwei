from jianwei.radar.r60abd1 import parse_frame
from jianwei.radar.simulator import generate_demo_events, generate_demo_frames


def test_generate_demo_frames_are_parseable():
    frames = list(generate_demo_frames())

    assert len(frames) > 100
    assert parse_frame(frames[0]).event.type == "bed_state"


def test_generate_demo_events_include_expected_risk_pattern():
    events = list(generate_demo_events())
    event_types = [event.type for event in events]
    respiration_values = [event.value for event in events if event.type == "respiration_rate"]

    assert "bed_state" in event_types
    assert "motion_amplitude" in event_types
    assert "heart_rate" in event_types
    assert 0 in respiration_values
    assert max(respiration_values) >= 20


def test_generate_demo_events_use_china_local_night_time():
    first_event = next(generate_demo_events())

    assert first_event.timestamp is not None
    assert first_event.timestamp.hour == 22
    assert first_event.timestamp.utcoffset().total_seconds() == 8 * 60 * 60
