from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from jianwei.radar.r60abd1 import RadarEvent


@dataclass(frozen=True)
class SleepSession:
    session_id: str
    events: list[RadarEvent]
    started_at: datetime | None
    ended_at: datetime | None


def build_session(session_id: str, events: list[RadarEvent]) -> SleepSession:
    ordered = sorted(events, key=lambda event: event.timestamp or datetime.min)
    timestamps = [event.timestamp for event in ordered if event.timestamp is not None]
    return SleepSession(
        session_id=session_id,
        events=ordered,
        started_at=timestamps[0] if timestamps else None,
        ended_at=timestamps[-1] if timestamps else None,
    )
