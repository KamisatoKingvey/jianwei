from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from jianwei.radar.r60abd1 import RadarEvent, event_to_dict


class JsonlEventStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, device_id: str, user_id: str, session_id: str, event: RadarEvent) -> None:
        row = {
            "device_id": device_id,
            "user_id": user_id,
            "session_id": session_id,
            "event": event_to_dict(event),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def iter_session(self, device_id: str, session_id: str) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row["device_id"] == device_id and row["session_id"] == session_id:
                    yield row
