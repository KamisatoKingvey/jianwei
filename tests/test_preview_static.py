from fastapi.testclient import TestClient

from jianwei.api.main import app


def test_preview_page_is_served():
    response = TestClient(app).get("/preview/")

    assert response.status_code == 200
    assert "见微" in response.text
    assert "/api/reports/demo" in response.text
