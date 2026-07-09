import pytest

from datetime import datetime, timezone

from jianwei.radar.r60abd1 import FrameError, RadarEvent, build_frame, event_from_dict, event_to_dict, events_from_hex_log, parse_frame


def test_parse_heart_rate_frame_from_vendor_sample():
    frame = bytes.fromhex("53 59 85 02 00 01 41 75 54 43")

    parsed = parse_frame(frame)

    assert parsed.control == 0x85
    assert parsed.command == 0x02
    assert parsed.data == bytes([0x41])
    assert parsed.event.type == "heart_rate"
    assert parsed.event.value == 65
    assert parsed.event.unit == "bpm"


def test_parse_motion_and_distance_frames_from_vendor_sample():
    log = """
    2024-01-17 16:17 53 59 80 03 00 01 03 33 54 43
    2024-01-17 16:17 53 59 80 04 00 02 00 23 55 54 43
    """

    events = events_from_hex_log(log)

    assert [event.type for event in events] == ["motion_amplitude", "distance"]
    assert events[0].value == 3
    assert events[1].value == 35
    assert events[1].unit == "cm"


def test_build_frame_round_trips_respiration_waveform():
    frame = build_frame(0x81, 0x05, bytes([128, 130, 126, 129, 127]))

    parsed = parse_frame(frame)

    assert parsed.event.type == "respiration_waveform"
    assert parsed.event.value == [0, 2, -2, 1, -1]


def test_rejects_bad_checksum():
    frame = bytearray.fromhex("53 59 85 02 00 01 41 75 54 43")
    frame[7] = 0x00

    with pytest.raises(FrameError, match="checksum"):
        parse_frame(bytes(frame))


def test_event_dict_round_trips_for_database_storage():
    event = RadarEvent(
        type="respiration_rate",
        value=16,
        unit="breaths_per_minute",
        timestamp=datetime(2026, 7, 6, 22, 30, tzinfo=timezone.utc),
        confidence=0.92,
        raw={"control": 0x81, "command": 0x02},
    )

    restored = event_from_dict(event_to_dict(event))

    assert restored == event
