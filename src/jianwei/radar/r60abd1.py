from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any


HEADER = bytes([0x53, 0x59])
TAIL = bytes([0x54, 0x43])


class FrameError(ValueError):
    """Raised when an R60ABD1 frame is malformed."""


@dataclass(frozen=True)
class RadarEvent:
    type: str
    value: Any
    unit: str | None = None
    source: str = "r60abd1"
    timestamp: datetime | None = None
    confidence: float = 1.0
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class RadarFrame:
    control: int
    command: int
    data: bytes
    checksum: int
    event: RadarEvent


def checksum(payload_without_checksum: bytes) -> int:
    return sum(payload_without_checksum) & 0xFF


def build_frame(control: int, command: int, data: bytes) -> bytes:
    length = len(data).to_bytes(2, "big")
    body = HEADER + bytes([control, command]) + length + data
    return body + bytes([checksum(body)]) + TAIL


def parse_frame(frame: bytes, timestamp: datetime | None = None) -> RadarFrame:
    if len(frame) < 9:
        raise FrameError("frame too short")
    if not frame.startswith(HEADER):
        raise FrameError("invalid frame header")
    if not frame.endswith(TAIL):
        raise FrameError("invalid frame tail")

    data_len = int.from_bytes(frame[4:6], "big")
    expected_len = 2 + 1 + 1 + 2 + data_len + 1 + 2
    if len(frame) != expected_len:
        raise FrameError(f"length mismatch: expected {expected_len}, got {len(frame)}")

    expected_checksum = checksum(frame[: 6 + data_len])
    actual_checksum = frame[6 + data_len]
    if actual_checksum != expected_checksum:
        raise FrameError(
            f"checksum mismatch: expected 0x{expected_checksum:02X}, got 0x{actual_checksum:02X}"
        )

    control = frame[2]
    command = frame[3]
    data = frame[6 : 6 + data_len]
    event = normalize_event(control, command, data, timestamp)
    return RadarFrame(control=control, command=command, data=data, checksum=actual_checksum, event=event)


def normalize_event(control: int, command: int, data: bytes, timestamp: datetime | None = None) -> RadarEvent:
    raw = {"control": control, "command": command, "data_hex": data.hex(" ").upper()}

    if (control, command) == (0x80, 0x01):
        return RadarEvent("presence", {0: "absent", 1: "present"}.get(data[0], "unknown"), timestamp=timestamp, raw=raw)
    if (control, command) == (0x80, 0x02):
        return RadarEvent("motion_state", {0: "none", 1: "still", 2: "active"}.get(data[0], "unknown"), timestamp=timestamp, raw=raw)
    if (control, command) == (0x80, 0x03):
        return RadarEvent("motion_amplitude", data[0], unit="percent", timestamp=timestamp, raw=raw)
    if (control, command) == (0x80, 0x04):
        return RadarEvent("distance", int.from_bytes(data, "big"), unit="cm", timestamp=timestamp, raw=raw)
    if (control, command) == (0x80, 0x05):
        return RadarEvent("position", _decode_position(data), unit="cm", timestamp=timestamp, raw=raw)

    if (control, command) == (0x81, 0x01):
        return RadarEvent("respiration_status", {1: "normal", 2: "high", 3: "low", 4: "none"}.get(data[0], "unknown"), timestamp=timestamp, raw=raw)
    if (control, command) == (0x81, 0x02):
        return RadarEvent("respiration_rate", data[0], unit="breaths_per_minute", timestamp=timestamp, raw=raw)
    if (control, command) == (0x81, 0x05):
        return RadarEvent("respiration_waveform", [sample - 128 for sample in data], timestamp=timestamp, raw=raw)

    if (control, command) == (0x85, 0x02):
        return RadarEvent("heart_rate", data[0], unit="bpm", timestamp=timestamp, raw=raw)
    if (control, command) == (0x85, 0x05):
        return RadarEvent("heart_waveform", [sample - 128 for sample in data], timestamp=timestamp, raw=raw)

    if (control, command) == (0x84, 0x01):
        return RadarEvent("bed_state", {0: "out_of_bed", 1: "in_bed", 2: "none"}.get(data[0], "unknown"), timestamp=timestamp, raw=raw)
    if (control, command) == (0x84, 0x02):
        return RadarEvent("vendor_sleep_state", {0: "deep", 1: "light", 2: "awake", 3: "none"}.get(data[0], "unknown"), timestamp=timestamp, raw=raw)
    if (control, command) == (0x84, 0x0C):
        return RadarEvent("vendor_ten_minute_summary", _decode_ten_minute_summary(data), timestamp=timestamp, raw=raw)
    if (control, command) == (0x84, 0x0D):
        return RadarEvent("vendor_sleep_analysis", _decode_sleep_analysis(data), timestamp=timestamp, raw=raw)

    return RadarEvent("unknown", data.hex(" ").upper(), timestamp=timestamp, confidence=0.0, raw=raw)


def events_from_hex_log(text: str) -> list[RadarEvent]:
    events: list[RadarEvent] = []
    for line in text.splitlines():
        hex_bytes = re.findall(r"\b[0-9A-Fa-f]{2}\b", line)
        if not hex_bytes:
            continue
        try:
            start = hex_bytes.index("53")
        except ValueError:
            continue
        frame_bytes = bytes(int(part, 16) for part in hex_bytes[start:])
        events.append(parse_frame(frame_bytes).event)
    return events


def event_to_dict(event: RadarEvent) -> dict[str, Any]:
    return {
        "type": event.type,
        "value": event.value,
        "unit": event.unit,
        "source": event.source,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "confidence": event.confidence,
        "raw": event.raw,
    }


def event_from_dict(data: dict[str, Any]) -> RadarEvent:
    timestamp = data.get("timestamp")
    if isinstance(timestamp, str) and timestamp:
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    else:
        timestamp = None
    return RadarEvent(
        type=data["type"],
        value=data.get("value"),
        unit=data.get("unit"),
        source=data.get("source", "r60abd1"),
        timestamp=timestamp,
        confidence=float(data.get("confidence", 1.0)),
        raw=data.get("raw"),
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _decode_position(data: bytes) -> dict[str, int]:
    if len(data) != 6:
        return {"x": 0, "y": 0, "z": 0}
    axes = []
    for offset in range(0, 6, 2):
        value = int.from_bytes(data[offset : offset + 2], "big")
        negative = bool(value & 0x8000)
        magnitude = value & 0x7FFF
        axes.append(-magnitude if negative else magnitude)
    return {"x": axes[0], "y": axes[1], "z": axes[2]}


def _decode_ten_minute_summary(data: bytes) -> dict[str, Any]:
    padded = data + bytes(max(0, 8 - len(data)))
    return {
        "presence": "present" if padded[0] == 1 else "absent",
        "sleep_state": {0: "deep", 1: "light", 2: "awake", 3: "out_of_bed"}.get(padded[1], "unknown"),
        "average_respiration": padded[2],
        "average_heart_rate": padded[3],
        "turn_count": padded[4],
        "large_motion_percent": padded[5],
        "small_motion_percent": padded[6],
        "vendor_apnea_count_reserved": padded[7],
    }


def _decode_sleep_analysis(data: bytes) -> dict[str, Any]:
    padded = data + bytes(max(0, 12 - len(data)))
    return {
        "score": padded[0],
        "total_sleep_minutes": int.from_bytes(padded[1:3], "big"),
        "awake_percent": padded[3],
        "light_percent": padded[4],
        "deep_percent": padded[5],
        "out_of_bed_minutes": padded[6],
        "out_of_bed_count": padded[7],
        "turn_count": padded[8],
        "average_respiration": padded[9],
        "average_heart_rate": padded[10],
        "vendor_apnea_count_reserved": padded[11],
    }
