from fastapi.testclient import TestClient

from jianwei.api import main
from jianwei.radar.r60abd1 import event_to_dict
from jianwei.radar.simulator import generate_demo_events


client = TestClient(main.app)


class InMemoryStore:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.appended = []

    def append(self, device_id, user_id, session_id, event):
        self.appended.append((device_id, user_id, session_id, event))

    def iter_session(self, device_id, session_id):
        for row in self.rows:
            if row["device_id"] == device_id and row["session_id"] == session_id:
                yield row

    def is_healthy(self):
        return True


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "jianwei-backend"
    assert body["storage"]["ok"] is True


def test_demo_report_endpoint():
    response = client.get("/api/reports/demo")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "demo-night"
    assert "summary" in body
    assert "metrics" in body


def test_count_endpoint_supports_cloudbase_template_smoke_check():
    response = client.post("/api/count", json={"action": "inc"})

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "inc"
    assert isinstance(body["count"], int)
    assert body["count"] >= 1


def test_ingest_hex_log_endpoint():
    fake_store = InMemoryStore()
    main.store = fake_store
    response = client.post(
        "/api/radar/ingest-hex",
        json={
            "device_id": "dev-1",
            "user_id": "user-1",
            "session_id": "session-1",
            "log": "53 59 85 02 00 01 41 75 54 43",
        },
    )

    assert response.status_code == 200
    assert response.json()["ingested"] == 1
    assert fake_store.appended[0][0:3] == ("dev-1", "user-1", "session-1")


def test_session_report_endpoint_reads_stored_events():
    rows = [
        {
            "device_id": "dev-1",
            "user_id": "user-1",
            "session_id": "session-demo",
            "event": event_to_dict(event),
        }
        for event in generate_demo_events()
    ]
    main.store = InMemoryStore(rows)

    response = client.get("/api/reports/dev-1/session-demo")

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == "dev-1"
    assert body["session_id"] == "session-demo"
    assert body["risk"]["level"] == "attention"


def test_session_report_endpoint_returns_404_when_session_missing():
    main.store = InMemoryStore([])

    response = client.get("/api/reports/dev-1/missing")

    assert response.status_code == 404
