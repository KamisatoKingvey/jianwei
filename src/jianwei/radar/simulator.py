from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from jianwei.radar.r60abd1 import RadarEvent, build_frame, parse_frame

CHINA_TIMEZONE = timezone(timedelta(hours=8))


def generate_demo_frames() -> Iterator[bytes]:
    yield build_frame(0x84, 0x01, bytes([0x01]))

    for index in range(240):
        minute = index // 20
        respiration = _respiration_for_index(index)
        heart_rate = 65 + (index % 7)
        motion = _motion_for_index(index)

        yield build_frame(0x80, 0x03, bytes([motion]))
        yield build_frame(0x81, 0x02, bytes([respiration]))
        yield build_frame(0x85, 0x02, bytes([heart_rate]))

        if index % 20 == 0:
            state = 2 if minute < 1 else 1 if minute < 7 else 0
            yield build_frame(0x84, 0x02, bytes([state]))

    yield build_frame(0x84, 0x01, bytes([0x00]))


def generate_demo_events() -> Iterator[RadarEvent]:
    start = datetime(2026, 7, 6, 22, 30, tzinfo=CHINA_TIMEZONE)
    for event_index, frame in enumerate(generate_demo_frames()):
        yield parse_frame(frame, timestamp=start + timedelta(seconds=event_index * 3)).event


def _respiration_for_index(index: int) -> int:
    if 80 <= index <= 93:
        return 0
    if 150 <= index <= 153:
        return 28
    return 15 + (index % 4)


def _motion_for_index(index: int) -> int:
    if 60 <= index <= 64:
        return 45
    if 120 <= index <= 123:
        return 65
    return 1 + (index % 4)
