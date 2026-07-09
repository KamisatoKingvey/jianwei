from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_cloudrun_dockerfile_starts_fastapi_service_on_port_80():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "jianwei.api.main:app" in dockerfile
    assert "--host 0.0.0.0" in dockerfile
    assert "--port 80" in dockerfile
    assert "EXPOSE 80" in dockerfile


def test_cloudrun_build_context_excludes_local_secrets_and_test_artifacts():
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".env" in dockerignore
    assert ".pytest_cache" in dockerignore
    assert "tests/" in dockerignore


def test_cloudrun_container_config_uses_template_mysql_database():
    config = json.loads((ROOT / "container.config.json").read_text(encoding="utf-8"))

    assert config["containerPort"] == 80
    assert config["dataBaseName"] == "flask_demo"
    assert any("CREATE DATABASE IF NOT EXISTS flask_demo" in sql for sql in config["executeSQLs"])
