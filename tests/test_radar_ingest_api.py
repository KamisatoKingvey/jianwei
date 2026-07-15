from datetime import datetime, timedelta, timezone

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


def make_sample(**overrides):
    sample = {
        "device": "jianwei-r60-a01",
        "starttimestamp": 195350,
        "timestamp": 1783698174000,
        "presence": 1,
        "activity": 1,
        "breath_rate": 21,
        "heart_rate": 93,
        "sleep_stage": 0,
        "sleep_score": 0,
        "in_bed": 1,
        "movement": 1,
        "distance": 47,
        "co2": 1141,
        "temperature": 27.5,
        "humidity": 64.9,
    }
    sample.update(overrides)
    return sample


def test_single_data_endpoint_stores_sample():
    response = client.post("/api/radar/data", json=make_sample())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["received"] == 1
    assert body["total"] == 1

    stored = main.sample_store.latest("jianwei-r60-a01")
    assert stored["breath_rate"] == 21
    assert stored["co2"] == 1141
    assert stored["clock_synced"] is True
    assert stored["sampled_at"] == datetime.fromtimestamp(1783698174, tz=timezone.utc)


def test_firmware_waveform_fields_are_stored_and_read_back():
    """固件按秒上报 respiration_waveform/heart_waveform，后端需落库且原样读回。"""
    sample = make_sample(
        respiration_waveform=[-3, 1, 4, 2, -1],
        heart_waveform=[10, -12, 7, 0, -5],
    )

    response = client.post("/api/radar/data", json=sample)
    assert response.status_code == 200

    stored = main.sample_store.latest("jianwei-r60-a01")
    assert stored["respiration_waveform"] == [-3, 1, 4, 2, -1]
    assert stored["heart_waveform"] == [10, -12, 7, 0, -5]


def test_samples_without_waveforms_default_to_empty_lists():
    """老固件/缺省不带波形时不应报错，读回为空列表。"""
    response = client.post("/api/radar/data", json=make_sample())
    assert response.status_code == 200

    stored = main.sample_store.latest("jianwei-r60-a01")
    assert stored["respiration_waveform"] == []
    assert stored["heart_waveform"] == []


def test_batch_endpoint_stores_all_samples():
    batch = [
        make_sample(timestamp=1783698174000),
        make_sample(timestamp=1783698204000, breath_rate=18),
    ]

    response = client.post("/api/radar/batch", json=batch)

    assert response.status_code == 200
    assert response.json()["received"] == 2
    rows = list(main.sample_store.iter_device("jianwei-r60-a01"))
    assert [row["breath_rate"] for row in rows] == [21, 18]


def test_unsynced_boot_timestamps_fall_back_to_server_time():
    batch = [
        make_sample(timestamp=100_000),
        make_sample(timestamp=130_000),
    ]

    response = client.post("/api/radar/batch", json=batch)

    assert response.status_code == 200
    rows = list(main.sample_store.iter_device("jianwei-r60-a01"))
    assert all(row["clock_synced"] is False for row in rows)
    # 批内相对间隔按开机毫秒差还原
    assert rows[1]["sampled_at"] - rows[0]["sampled_at"] == timedelta(seconds=30)
    assert datetime.now(timezone.utc) - rows[1]["sampled_at"] < timedelta(seconds=10)


def test_first_report_auto_registers_device_with_bind_code():
    client.post("/api/radar/data", json=make_sample())

    device = main.device_store.get_device("jianwei-r60-a01")
    assert device is not None
    assert len(device["bind_code"]) == 6


def test_device_secret_is_enforced_once_registered():
    main.device_store.upsert_device("jianwei-r60-a01", secret="s3cret")

    missing = client.post("/api/radar/data", json=make_sample())
    wrong = client.post("/api/radar/data", json=make_sample(), headers={"X-Device-Secret": "nope"})
    right = client.post("/api/radar/data", json=make_sample(), headers={"X-Device-Secret": "s3cret"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert right.status_code == 200


def test_batch_rejects_mixed_devices():
    batch = [make_sample(), make_sample(device="other-device")]

    response = client.post("/api/radar/batch", json=batch)

    assert response.status_code == 400


def test_empty_batch_rejected():
    response = client.post("/api/radar/batch", json=[])

    assert response.status_code == 400


def test_ingest_triggers_no_breath_alert():
    base = 1783698174000
    batch = [
        make_sample(timestamp=base + i * 30_000, breath_rate=0, movement=0)
        for i in range(3)
    ]

    response = client.post("/api/radar/batch", json=batch)

    assert response.status_code == 200
    alerts = main.alert_store.recent(["jianwei-r60-a01"])
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "suspected_no_breath"
