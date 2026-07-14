from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jianwei.api import main
from jianwei.storage.agent_store import JsonlAgentStore
from jianwei.storage.alert_store import JsonlAlertStore
from jianwei.storage.device_store import JsonDeviceStore
from jianwei.storage.sample_store import JsonlSampleStore


client = TestClient(main.app)

OPENID_HEADER = {"X-WX-OPENID": "openid-1", "X-WX-SOURCE": "wxcloud"}


def wx_headers(openid):
    """模拟云托管 callContainer 注入的可信头。"""
    return {"X-WX-OPENID": openid, "X-WX-SOURCE": "wxcloud"}


class FakeRunner:
    def __init__(self, reply="你昨晚睡得不错。", available=True, error=None):
        self.reply = reply
        self.available = available
        self.error = error
        self.calls = []

    async def run(self, prompt, context):
        self.calls.append({"prompt": prompt, "openid": context.openid})
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "sample_store", JsonlSampleStore(tmp_path / "samples.jsonl"))
    monkeypatch.setattr(main, "device_store", JsonDeviceStore(tmp_path / "devices.json"))
    monkeypatch.setattr(main, "alert_store", JsonlAlertStore(tmp_path / "alerts.jsonl"))
    monkeypatch.setattr(
        main, "agent_store", JsonlAgentStore(tmp_path / "agent_messages.jsonl", tmp_path / "agent_insights.jsonl")
    )
    monkeypatch.setattr(main, "agent_runner", FakeRunner())


def seed_device_with_night(device_id="dev-1", openid="openid-1"):
    main.device_store.upsert_device(device_id)
    main.device_store.bind_user(openid, device_id)
    start = datetime.now(timezone.utc) - timedelta(hours=8)
    main.sample_store.append_many(
        [
            {
                "device_id": device_id,
                "sampled_at": start + timedelta(seconds=index * 30),
                "clock_synced": True,
                "presence": 1,
                "activity": 1,
                "breath_rate": 15,
                "heart_rate": 66,
                "sleep_stage": 1,
                "sleep_score": 80,
                "in_bed": 1,
                "movement": 3,
                "distance": 60,
                "co2": 900,
                "temperature": 24.0,
                "humidity": 55.0,
            }
            for index in range(60)
        ]
    )


def poll_task(task_id, headers=OPENID_HEADER):
    """TestClient 会在响应返回前跑完 BackgroundTasks，轮询一次即可拿到终态。"""
    response = client.get(f"/api/agent/chat/tasks/{task_id}", headers=headers)
    return response


def test_chat_returns_pending_task_then_plain_reply():
    response = client.post("/api/agent/chat", json={"message": "我昨晚睡得怎么样"}, headers=OPENID_HEADER)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["task_id"]
    assert body["conversation_id"]

    task = poll_task(body["task_id"]).json()
    assert task["status"] == "done"
    # 免责声明改由小程序输入框下方固定展示，回复本体不再拼接
    assert task["reply"] == "你昨晚睡得不错。"
    assert task["conversation_id"] == body["conversation_id"]

    stored = main.agent_store.recent_messages(body["conversation_id"])
    assert [row["role"] for row in stored] == ["user", "assistant"]
    assert main.agent_runner.calls[0]["openid"] == "openid-1"


def test_chat_injects_history_on_followup():
    first = client.post("/api/agent/chat", json={"message": "第一句"}, headers=OPENID_HEADER).json()

    client.post(
        "/api/agent/chat",
        json={"message": "第二句", "conversation_id": first["conversation_id"]},
        headers=OPENID_HEADER,
    )

    followup_prompt = main.agent_runner.calls[1]["prompt"]
    assert "第一句" in followup_prompt
    assert "第二句" in followup_prompt


def test_chat_requires_openid():
    response = client.post("/api/agent/chat", json={"message": "hi"})

    assert response.status_code == 401


def test_chat_503_when_agent_not_configured(monkeypatch):
    monkeypatch.setattr(main, "agent_runner", FakeRunner(available=False))

    response = client.post("/api/agent/chat", json={"message": "hi"}, headers=OPENID_HEADER)

    assert response.status_code == 503


def test_chat_task_fails_gracefully_when_agent_errors(monkeypatch):
    monkeypatch.setattr(main, "agent_runner", FakeRunner(error=RuntimeError("boom")))

    response = client.post("/api/agent/chat", json={"message": "hi"}, headers=OPENID_HEADER)
    assert response.status_code == 200

    task = poll_task(response.json()["task_id"]).json()
    assert task["status"] == "failed"
    assert task["error"]


def test_chat_task_polling_enforces_ownership_and_missing():
    created = client.post("/api/agent/chat", json={"message": "hi"}, headers=OPENID_HEADER).json()

    forbidden = poll_task(created["task_id"], headers=wx_headers("openid-2"))
    missing = poll_task("nope")

    assert forbidden.status_code == 403
    assert missing.status_code == 404


def test_chat_task_pending_too_long_reports_timeout(monkeypatch):
    """实例被回收会留下永远 pending 的任务，超时后轮询应返回失败而不是让前端干等。"""
    created = client.post("/api/agent/chat", json={"message": "hi"}, headers=OPENID_HEADER).json()
    stale = datetime.now(timezone.utc) - timedelta(seconds=600)
    main.agent_store.create_task("stale-task", "openid-1", created["conversation_id"], "hi", stale)

    task = poll_task("stale-task").json()

    assert task["status"] == "failed"
    assert "超时" in task["error"]


def test_chat_rejects_foreign_conversation():
    mine = client.post("/api/agent/chat", json={"message": "hi"}, headers=OPENID_HEADER).json()

    response = client.post(
        "/api/agent/chat",
        json={"message": "hack", "conversation_id": mine["conversation_id"]},
        headers=wx_headers("openid-2"),
    )

    assert response.status_code == 403


def test_chat_daily_limit(monkeypatch):
    monkeypatch.setenv("JIANWEI_AGENT_DAILY_LIMIT", "2")

    for _ in range(2):
        assert client.post("/api/agent/chat", json={"message": "hi"}, headers=OPENID_HEADER).status_code == 200
    third = client.post("/api/agent/chat", json={"message": "hi"}, headers=OPENID_HEADER)

    assert third.status_code == 429


def test_conversation_history_endpoint_enforces_ownership():
    mine = client.post("/api/agent/chat", json={"message": "hi"}, headers=OPENID_HEADER).json()
    cid = mine["conversation_id"]

    ok = client.get(f"/api/agent/conversations/{cid}", headers=OPENID_HEADER)
    forbidden = client.get(f"/api/agent/conversations/{cid}", headers=wx_headers("openid-2"))
    missing = client.get("/api/agent/conversations/nope", headers=OPENID_HEADER)

    assert ok.status_code == 200
    assert len(ok.json()["messages"]) == 2
    assert forbidden.status_code == 403
    assert missing.status_code == 404


def test_report_insights_returns_rules_first_then_cached_agent():
    """未缓存时立即返回规则版摘要（15 秒内响应），agent 版后台生成进缓存。"""
    seed_device_with_night()

    first = client.get("/api/agent/report-insights/dev-1", headers=OPENID_HEADER)
    second = client.get("/api/agent/report-insights/dev-1", headers=OPENID_HEADER)

    assert first.status_code == 200
    assert first.json()["source"] == "rules"
    assert second.json()["source"] == "agent"
    assert len(main.agent_runner.calls) == 1  # 后台只生成一次，之后走缓存


def test_report_insights_falls_back_to_rules_when_agent_down(monkeypatch):
    seed_device_with_night()
    monkeypatch.setattr(main, "agent_runner", FakeRunner(available=False))

    response = client.get("/api/agent/report-insights/dev-1", headers=OPENID_HEADER)

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "rules"
    assert body["insights"]


def test_report_insights_falls_back_to_rules_on_agent_error(monkeypatch):
    seed_device_with_night()
    monkeypatch.setattr(main, "agent_runner", FakeRunner(error=RuntimeError("boom")))

    response = client.get("/api/agent/report-insights/dev-1", headers=OPENID_HEADER)

    assert response.status_code == 200
    assert response.json()["source"] == "rules"


def test_report_insights_rejects_unbound_device():
    seed_device_with_night(device_id="dev-1", openid="openid-2")

    response = client.get("/api/agent/report-insights/dev-1", headers=OPENID_HEADER)

    assert response.status_code == 403


def test_chat_rejects_spoofed_openid_without_trusted_source():
    """公网域名可以随便伪造 X-WX-OPENID；没有平台注入的 X-WX-SOURCE 一律拒绝，
    否则任何人都能白嫖 agent 并消耗模型额度。"""
    response = client.post(
        "/api/agent/chat",
        json={"message": "hi"},
        headers={"X-WX-OPENID": "spoofed"},
    )

    assert response.status_code == 401
    assert "untrusted" in response.json()["detail"]
    assert main.agent_runner.calls == []  # 根本没调到模型


def test_public_openid_allowed_when_debug_flag_set(monkeypatch):
    monkeypatch.setenv("JIANWEI_ALLOW_PUBLIC_OPENID", "1")

    response = client.post(
        "/api/agent/chat",
        json={"message": "hi"},
        headers={"X-WX-OPENID": "openid-1"},
    )

    assert response.status_code == 200
