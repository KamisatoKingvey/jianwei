from datetime import datetime, timezone

from jianwei.radar.r60abd1 import RadarEvent
from jianwei.storage.jsonl_store import JsonlEventStore


def test_jsonl_store_round_trips_events(tmp_path):
    store = JsonlEventStore(tmp_path / "events.jsonl")
    event = RadarEvent(
        type="respiration_rate",
        value=16,
        unit="breaths_per_minute",
        timestamp=datetime(2026, 7, 6, 22, 30, tzinfo=timezone.utc),
    )

    store.append("device-1", "user-1", "session-1", event)
    rows = list(store.iter_session("device-1", "session-1"))

    assert rows[0]["device_id"] == "device-1"
    assert rows[0]["user_id"] == "user-1"
    assert rows[0]["session_id"] == "session-1"
    assert rows[0]["event"]["type"] == "respiration_rate"
    assert rows[0]["event"]["value"] == 16
