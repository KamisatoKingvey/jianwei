from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from jianwei.analysis.report import build_sleep_report
from jianwei.analysis.session import build_session
from jianwei.config import load_env_file
from jianwei.radar.r60abd1 import event_from_dict, events_from_hex_log
from jianwei.radar.simulator import generate_demo_events
from jianwei.storage.factory import build_event_store


ROOT = Path(__file__).resolve().parents[3]
DATA_PATH = ROOT / "data" / "events.jsonl"
STATIC_PATH = ROOT / "static"

load_env_file(ROOT / ".env")

app = FastAPI(title="Jianwei Backend", version="0.1.0")
store = build_event_store(DATA_PATH)

if STATIC_PATH.exists():
    app.mount("/preview", StaticFiles(directory=STATIC_PATH, html=True), name="preview")


class HexIngestRequest(BaseModel):
    device_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    log: str = Field(min_length=1)


class CountRequest(BaseModel):
    action: str = "get"


_template_count = 0


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "jianwei-backend",
        "storage": _storage_health(),
    }


@app.get("/api/reports/demo")
def demo_report() -> dict:
    session = build_session("demo-night", list(generate_demo_events()))
    return build_sleep_report(session)


@app.post("/api/count")
def count(request: CountRequest) -> dict[str, int | str]:
    global _template_count

    if request.action == "inc":
        _template_count += 1
    elif request.action == "clear":
        _template_count = 0
    elif request.action != "get":
        raise HTTPException(status_code=400, detail="unsupported action")

    return {"action": request.action, "count": _template_count}


@app.get("/api/reports/{device_id}/{session_id}")
def session_report(device_id: str, session_id: str) -> dict:
    rows = list(store.iter_session(device_id, session_id))
    if not rows:
        raise HTTPException(status_code=404, detail="session not found")

    events = [event_from_dict(row["event"]) for row in rows]
    report = build_sleep_report(build_session(session_id, events))
    report["device_id"] = device_id
    return report


@app.post("/api/radar/ingest-hex")
def ingest_hex_log(request: HexIngestRequest) -> dict[str, int | str]:
    events = events_from_hex_log(request.log)
    for event in events:
        store.append(request.device_id, request.user_id, request.session_id, event)
    return {"status": "ok", "ingested": len(events)}


def _storage_health() -> dict[str, bool | str]:
    if hasattr(store, "is_healthy"):
        return {"type": store.__class__.__name__, "ok": bool(store.is_healthy())}
    return {"type": store.__class__.__name__, "ok": True}
