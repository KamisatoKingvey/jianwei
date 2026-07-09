import os

from jianwei.config import load_env_file, mysql_settings_from_env


def test_mysql_settings_can_be_built_from_cloudrun_environment():
    env = {
        "MYSQL_ADDRESS": "10.0.0.2:3306",
        "MYSQL_USERNAME": "root",
        "MYSQL_PASSWORD": "pass word",
    }

    assert mysql_settings_from_env(env) == {
        "host": "10.0.0.2",
        "port": 3306,
        "database": "flask_demo",
        "user": "root",
        "password": "pass word",
    }


def test_mysql_settings_support_database_override_and_default_port():
    env = {
        "MYSQL_ADDRESS": "mysql.internal",
        "MYSQL_USERNAME": "jianwei",
        "MYSQL_PASSWORD": "secret",
        "MYSQL_DATABASE": "jianwei_prod",
    }

    assert mysql_settings_from_env(env) == {
        "host": "mysql.internal",
        "port": 3306,
        "database": "jianwei_prod",
        "user": "jianwei",
        "password": "secret",
    }


def test_incomplete_mysql_environment_is_ignored():
    env = {
        "MYSQL_ADDRESS": "mysql.internal:3306",
        "MYSQL_USERNAME": "root",
    }

    assert mysql_settings_from_env(env) is None


def test_load_env_file_does_not_override_existing_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("MYSQL_ADDRESS=from-file:3306\nMYSQL_USERNAME=root\n", encoding="utf-8")
    monkeypatch.delenv("JIANWEI_SKIP_ENV_FILE", raising=False)
    monkeypatch.setenv("MYSQL_ADDRESS", "existing:3306")

    load_env_file(env_file)

    assert os.environ["MYSQL_ADDRESS"] == "existing:3306"
    assert os.environ["MYSQL_USERNAME"] == "root"


def test_load_env_file_can_be_disabled_for_tests(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("MYSQL_ADDRESS=from-file:3306\n", encoding="utf-8")
    monkeypatch.delenv("MYSQL_ADDRESS", raising=False)
    monkeypatch.setenv("JIANWEI_SKIP_ENV_FILE", "1")

    load_env_file(env_file)

    assert "MYSQL_ADDRESS" not in os.environ
