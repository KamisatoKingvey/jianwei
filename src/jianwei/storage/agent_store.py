from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from jianwei.config import MySqlSettings
from jianwei.storage.mysql_store import _connect, ensure_utc


MESSAGES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_messages (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  conversation_id VARCHAR(64) NOT NULL,
  openid VARCHAR(128) NOT NULL,
  role VARCHAR(16) NOT NULL,
  content TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  KEY idx_agent_messages_conv (conversation_id, id),
  KEY idx_agent_messages_user_time (openid, role, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

INSIGHTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_insights (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  device_id VARCHAR(128) NOT NULL,
  session_id VARCHAR(128) NOT NULL,
  insights TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_agent_insights (device_id, session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class JsonlAgentStore:
    """本地 JSONL：对话消息 + 晨报解读缓存。"""

    def __init__(self, messages_path: Path, insights_path: Path):
        self.messages_path = messages_path
        self.insights_path = insights_path
        self.messages_path.parent.mkdir(parents=True, exist_ok=True)

    def append_message(self, conversation_id: str, openid: str, role: str, content: str, created_at: datetime) -> None:
        row = {
            "conversation_id": conversation_id,
            "openid": openid,
            "role": role,
            "content": content,
            "created_at": created_at.isoformat(),
        }
        with self.messages_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def recent_messages(self, conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = [row for row in self._scan_messages() if row["conversation_id"] == conversation_id]
        return rows[-limit:]

    def conversation_owner(self, conversation_id: str) -> str | None:
        for row in self._scan_messages():
            if row["conversation_id"] == conversation_id:
                return row["openid"]
        return None

    def count_user_messages_since(self, openid: str, since: datetime) -> int:
        return sum(
            1
            for row in self._scan_messages()
            if row["openid"] == openid and row["role"] == "user" and row["created_at"] >= since
        )

    def get_insight(self, device_id: str, session_id: str) -> str | None:
        if not self.insights_path.exists():
            return None
        for line in self.insights_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["device_id"] == device_id and row["session_id"] == session_id:
                return row["insights"]
        return None

    def put_insight(self, device_id: str, session_id: str, insights: str) -> None:
        row = {"device_id": device_id, "session_id": session_id, "insights": insights}
        with self.insights_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _scan_messages(self) -> Iterator[dict[str, Any]]:
        if not self.messages_path.exists():
            return
        with self.messages_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    row["created_at"] = datetime.fromisoformat(row["created_at"])
                    yield row


class MySqlAgentStore:
    def __init__(self, settings: MySqlSettings, connect: Callable[[MySqlSettings], Any] | None = None):
        self.settings = settings
        self._connect = connect or _connect
        self._schema_ready = False

    def append_message(self, conversation_id: str, openid: str, role: str, content: str, created_at: datetime) -> None:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agent_messages (conversation_id, openid, role, content, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (conversation_id, openid, role, content, created_at),
                )
            connection.commit()

    def recent_messages(self, conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT conversation_id, openid, role, content, created_at
                    FROM agent_messages
                    WHERE conversation_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (conversation_id, limit),
                )
                rows = [dict(row) for row in cursor.fetchall()]
        rows.reverse()
        for row in rows:
            row["created_at"] = ensure_utc(row.get("created_at"))
        return rows

    def conversation_owner(self, conversation_id: str) -> str | None:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT openid FROM agent_messages WHERE conversation_id = %s ORDER BY id LIMIT 1",
                    (conversation_id,),
                )
                row = cursor.fetchone()
        return row["openid"] if row else None

    def count_user_messages_since(self, openid: str, since: datetime) -> int:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS total FROM agent_messages
                    WHERE openid = %s AND role = 'user' AND created_at >= %s
                    """,
                    (openid, since),
                )
                row = cursor.fetchone()
        return int(row["total"]) if row else 0

    def get_insight(self, device_id: str, session_id: str) -> str | None:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT insights FROM agent_insights WHERE device_id = %s AND session_id = %s",
                    (device_id, session_id),
                )
                row = cursor.fetchone()
        return row["insights"] if row else None

    def put_insight(self, device_id: str, session_id: str, insights: str) -> None:
        with self._connect(self.settings) as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agent_insights (device_id, session_id, insights)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE insights = VALUES(insights)
                    """,
                    (device_id, session_id, insights),
                )
            connection.commit()

    def _ensure_schema(self, connection: Any) -> None:
        if self._schema_ready:
            return
        with connection.cursor() as cursor:
            cursor.execute(MESSAGES_SCHEMA_SQL)
            cursor.execute(INSIGHTS_SCHEMA_SQL)
        connection.commit()
        self._schema_ready = True
