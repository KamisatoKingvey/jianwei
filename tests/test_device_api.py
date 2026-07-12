import pytest
from fastapi.testclient import TestClient

from jianwei.api import main
from jianwei.storage.alert_store import JsonlAlertStore
from jianwei.storage.device_store import JsonDeviceStore
from jianwei.storage.sample_store import JsonlSampleStore


client = TestClient(main.app)


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "sample_store", JsonlSampleStore(tmp_path / "samples.jsonl"))
    monkeypatch.setattr(main, "device_store", JsonDeviceStore(tmp_path / "devices.json"))
    monkeypatch.setattr(main, "alert_store", JsonlAlertStore(tmp_path / "alerts.jsonl"))


def test_register_device_returns_bind_code():
    response = client.post("/api/devices/register", json={"device_id": "dev-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == "dev-1"
    assert len(body["bind_code"]) == 6
    assert body["has_secret"] is False


def test_register_requires_admin_key_when_configured(monkeypatch):
    monkeypatch.setenv("JIANWEI_ADMIN_KEY", "adm1n")

    denied = client.post("/api/devices/register", json={"device_id": "dev-1"})
    allowed = client.post(
        "/api/devices/register",
        json={"device_id": "dev-1", "secret": "s3cret"},
        headers={"X-Admin-Key": "adm1n"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["has_secret"] is True


def test_bind_device_with_openid_header():
    bind_code = client.post("/api/devices/register", json={"device_id": "dev-1"}).json()["bind_code"]

    response = client.post(
        "/api/devices/bind",
        json={"bind_code": bind_code},
        headers={"X-WX-OPENID": "openid-1"},
    )

    assert response.status_code == 200
    assert response.json()["device_id"] == "dev-1"
    assert main.device_store.devices_for_user("openid-1") == ["dev-1"]


def test_bind_rejects_unknown_code_and_missing_openid():
    unknown = client.post(
        "/api/devices/bind",
        json={"bind_code": "ZZZZZZ"},
        headers={"X-WX-OPENID": "openid-1"},
    )
    missing_openid = client.post("/api/devices/bind", json={"bind_code": "ABC123"})

    assert unknown.status_code == 404
    assert missing_openid.status_code == 400


def test_my_devices_lists_bound_devices_with_latest_sample():
    bind_code = client.post("/api/devices/register", json={"device_id": "jianwei-r60-a01"}).json()["bind_code"]
    client.post("/api/devices/bind", json={"bind_code": bind_code}, headers={"X-WX-OPENID": "openid-1"})
    client.post(
        "/api/radar/data",
        json={
            "device": "jianwei-r60-a01",
            "timestamp": 0,
            "presence": 1,
            "breath_rate": 15,
            "heart_rate": 70,
            "in_bed": 1,
        },
    )

    response = client.get("/api/devices/mine", headers={"X-WX-OPENID": "openid-1"})

    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["device_id"] == "jianwei-r60-a01"
    assert devices[0]["online"] is True
    assert devices[0]["latest"]["breath_rate"] == 15


def test_my_devices_requires_openid():
    response = client.get("/api/devices/mine")

    assert response.status_code == 401


def test_device_status_404_without_samples():
    response = client.get("/api/devices/unknown/status")

    assert response.status_code == 404
