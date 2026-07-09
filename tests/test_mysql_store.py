import json
from datetime import datetime, timezone

from jianwei.radar.r60abd1 import RadarEvent
from jianwei.storage.mysql_store import MySqlEventStore


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.connection.statements.append((sql, params))

    def fetchall(self):
        return self.connection.rows

    def fetchone(self):
        return {"ok": 1}


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.statements = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


def test_mysql_store_creates_schema_and_inserts_event():
    fake = FakeConnection()
    store = MySqlEventStore(
        {
            "host": "mysql.internal",
            "port": 3306,
            "database": "flask_demo",
            "user": "root",
            "password": "secret",
        },
        connect=lambda _: fake,
    )
    event = RadarEvent(
        type="heart_rate",
        value=68,
        unit="bpm",
        timestamp=datetime(2026, 7, 6, 22, 31, tzinfo=timezone.utc),
    )

    store.append("device-1", "user-1", "session-1", event)

    sql_text = "\n".join(sql for sql, _ in fake.statements)
    assert "CREATE TABLE IF NOT EXISTS radar_events" in sql_text
    assert "INSERT INTO radar_events" in sql_text
    insert_params = [params for sql, params in fake.statements if "INSERT INTO radar_events" in sql][0]
    assert insert_params[:4] == ("device-1", "user-1", "session-1", "heart_rate")
    assert json.loads(insert_params[4])["value"] == 68
    assert insert_params[-1] == event.timestamp
    assert fake.commits == 2


def test_mysql_store_reads_session_rows_and_decodes_json_events():
    row = {
        "device_id": "device-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "event": '{"type":"heart_rate","value":68,"unit":"bpm","timestamp":null,"confidence":1.0,"raw":null}',
    }
    fake = FakeConnection(rows=[row])
    store = MySqlEventStore(
        {
            "host": "mysql.internal",
            "port": 3306,
            "database": "flask_demo",
            "user": "root",
            "password": "secret",
        },
        connect=lambda _: fake,
    )

    rows = list(store.iter_session("device-1", "session-1"))

    assert rows == [
        {
            "device_id": "device-1",
            "user_id": "user-1",
            "session_id": "session-1",
            "event": {"type": "heart_rate", "value": 68, "unit": "bpm", "timestamp": None, "confidence": 1.0, "raw": None},
        }
    ]
    assert any("ORDER BY event_timestamp IS NULL" in sql for sql, _ in fake.statements)


def test_mysql_store_health_uses_select_one():
    fake = FakeConnection()
    store = MySqlEventStore(
        {
            "host": "mysql.internal",
            "port": 3306,
            "database": "flask_demo",
            "user": "root",
            "password": "secret",
        },
        connect=lambda _: fake,
    )

    assert store.is_healthy()
    assert any("SELECT 1" in sql for sql, _ in fake.statements)
