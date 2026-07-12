"""MySQL DATETIME 读出来是 naive datetime；回归验证读取路径统一补成 UTC，
避免与 aware 的 now() 相减时抛 TypeError（线上 500 的根因）。"""
from datetime import datetime, timezone

from jianwei.storage.alert_store import MySqlAlertStore
from jianwei.storage.mysql_store import ensure_utc
from jianwei.storage.sample_store import MySqlSampleStore


SETTINGS = {
    "host": "mysql.internal",
    "port": 3306,
    "database": "flask_demo",
    "user": "root",
    "password": "secret",
}

NAIVE = datetime(2026, 7, 12, 6, 30)
AWARE = datetime(2026, 7, 12, 6, 30, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.connection.statements.append((sql, params))

    def executemany(self, sql, params):
        self.connection.statements.append((sql, params))

    def fetchall(self):
        return self.connection.rows

    def fetchone(self):
        return self.connection.rows[0] if self.connection.rows else None


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass


def test_ensure_utc_attaches_timezone_only_when_naive():
    assert ensure_utc(NAIVE) == AWARE
    assert ensure_utc(AWARE) is AWARE
    assert ensure_utc(None) is None
    assert ensure_utc("2026-07-12") == "2026-07-12"


def test_sample_store_latest_returns_aware_datetime():
    fake = FakeConnection(rows=[{"device_id": "dev-1", "sampled_at": NAIVE, "clock_synced": 1}])
    store = MySqlSampleStore(SETTINGS, connect=lambda _: fake)

    latest = store.latest("dev-1")

    assert latest["sampled_at"] == AWARE
    assert latest["clock_synced"] is True
    # 修复前这里会抛 TypeError: can't subtract offset-naive and offset-aware datetimes
    assert datetime.now(timezone.utc) - latest["sampled_at"] is not None


def test_sample_store_iter_device_returns_aware_datetimes():
    fake = FakeConnection(rows=[{"device_id": "dev-1", "sampled_at": NAIVE, "clock_synced": 0}])
    store = MySqlSampleStore(SETTINGS, connect=lambda _: fake)

    rows = list(store.iter_device("dev-1"))

    assert rows[0]["sampled_at"] == AWARE
    assert rows[0]["clock_synced"] is False


def test_alert_store_reads_aware_datetimes():
    fake = FakeConnection(
        rows=[
            {
                "device_id": "dev-1",
                "alert_type": "suspected_no_breath",
                "level": "attention",
                "message": "m",
                "created_at": NAIVE,
            }
        ]
    )
    store = MySqlAlertStore(SETTINGS, connect=lambda _: fake)

    assert store.recent(["dev-1"])[0]["created_at"] == AWARE

    fake_last = FakeConnection(rows=[{"last_time": NAIVE}])
    store_last = MySqlAlertStore(SETTINGS, connect=lambda _: fake_last)
    assert store_last.last_time("dev-1", "suspected_no_breath") == AWARE
