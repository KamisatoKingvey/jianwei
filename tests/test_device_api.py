import pytest
from fastapi.testclient import TestClient

from jianwei.api import main
from jianwei.storage.alert_store import JsonlAlertStore
from jianwei.storage.device_store import JsonDeviceStore
from jianwei.storage.sample_store import JsonlSampleStore


client = TestClient(main.app)


def wx_headers(openid):
    """模拟云托管 callContainer 注入的可信头。"""
    return {"X-WX-OPENID": openid, "X-WX-SOURCE": "wxcloud"}


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


def test_register_device_accepts_label_bind_code():
    """产线登记：绑定码用设备标签上的码，而不是随机生成。"""
    response = client.post(
        "/api/devices/register",
        json={"device_id": "dev-1", "bind_code": "ab12cd"},
    )

    assert response.status_code == 200
    assert response.json()["bind_code"] == "AB12CD"

    bind = client.post(
        "/api/devices/bind",
        json={"bind_code": "AB12CD"},
        headers=wx_headers("openid-1"),
    )
    assert bind.status_code == 200
    assert bind.json()["device_id"] == "dev-1"


def test_register_updates_bind_code_for_existing_device():
    """设备先上报自动注册（随机码），补登记标签码后以标签码为准。"""
    first = client.post("/api/devices/register", json={"device_id": "dev-1"})
    assert first.json()["bind_code"] != "AB12CD"

    second = client.post(
        "/api/devices/register",
        json={"device_id": "dev-1", "bind_code": "AB12CD"},
    )
    assert second.json()["bind_code"] == "AB12CD"


def test_register_rejects_malformed_bind_code():
    too_short = client.post(
        "/api/devices/register",
        json={"device_id": "dev-1", "bind_code": "AB12"},
    )
    bad_chars = client.post(
        "/api/devices/register",
        json={"device_id": "dev-1", "bind_code": "AB-12!"},
    )

    assert too_short.status_code == 422
    assert bad_chars.status_code == 422


def test_register_rejects_bind_code_used_by_another_device():
    client.post("/api/devices/register", json={"device_id": "dev-1", "bind_code": "AB12CD"})

    conflict = client.post(
        "/api/devices/register",
        json={"device_id": "dev-2", "bind_code": "AB12CD"},
    )

    assert conflict.status_code == 409


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
        headers=wx_headers("openid-1"),
    )

    assert response.status_code == 200
    assert response.json()["device_id"] == "dev-1"
    assert main.device_store.devices_for_user("openid-1") == ["dev-1"]


def test_bind_rejects_unknown_code_and_missing_openid():
    unknown = client.post(
        "/api/devices/bind",
        json={"bind_code": "ZZZZZZ"},
        headers=wx_headers("openid-1"),
    )
    missing_openid = client.post("/api/devices/bind", json={"bind_code": "ABC123"})

    assert unknown.status_code == 404
    assert missing_openid.status_code == 400


def test_my_devices_lists_bound_devices_with_latest_sample():
    bind_code = client.post("/api/devices/register", json={"device_id": "jianwei-r60-a01"}).json()["bind_code"]
    client.post("/api/devices/bind", json={"bind_code": bind_code}, headers=wx_headers("openid-1"))
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

    response = client.get("/api/devices/mine", headers=wx_headers("openid-1"))

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


def test_my_devices_rejects_spoofed_openid_from_public_network():
    """公网伪造 X-WX-OPENID 不能读到设备列表（缺少平台注入的 X-WX-SOURCE）。"""
    response = client.get("/api/devices/mine", headers={"X-WX-OPENID": "spoofed"})

    assert response.status_code == 401
    assert "untrusted" in response.json()["detail"]
