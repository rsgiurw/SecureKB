"""[MOD] 基于 SQLite 的本地聊天持久化（FileChatStore，2026-11-10）。

满足 MemoryService 的 ChatStore 协议（save_exchange / get_session_messages），
作为无 PostgreSQL 部署（本地模式默认）的持久化后端注入。由此：
- 对话消息在后端重启后可恢复（get_chat_history 的 PG 恢复路径自动生效——
  上游已实现恢复逻辑，此前因 chat_store 恒为 None 而从未生效）；
- 配合 SessionService 的会话快照，重启后会话与消息完整还原。

设计：sqlite（WAL）单文件，与 secure_audit 同风格；async 方法内的 sqlite
操作为毫秒级快操作，直接同步执行（与 add_conversation 的锁外调用约定一致）。
失败语义与协议注释一致：save 失败抛异常由调用方 graceful 吸收（内存已保存）；
get 失败返回空列表（回退内存路径）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.lib.logger import get_logger

logger = get_logger(__name__)


class FileChatStore:
    """本地文件聊天存储（ChatStore Protocol 实现）"""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def save_exchange(
        self,
        session_id: str,
        user_message: str,
        assistant_response: str,
        user_metadata: dict[str, Any] | None = None,
        assistant_metadata: dict[str, Any] | None = None,
    ) -> None:
        """原子保存一轮对话（user + assistant 两行）。失败抛异常（调用方吸收）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now().isoformat()
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO chat_messages
                    (session_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id, "user", user_message,
                    json.dumps(user_metadata or {}, ensure_ascii=False, default=str), now,
                ),
            )
            connection.execute(
                """
                INSERT INTO chat_messages
                    (session_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id, "assistant", assistant_response,
                    json.dumps(assistant_metadata or {}, ensure_ascii=False, default=str), now,
                ),
            )
            connection.commit()

    async def get_session_messages(
        self, session_id: str, company_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        """按时间序返回会话全部消息（恢复路径）。失败返回空列表。"""
        if not self.path.exists():
            return []
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                rows = connection.execute(
                    """
                    SELECT role, content, metadata, created_at
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY seq
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
            import json as _json

            messages: list[dict[str, Any]] = []
            for row in rows:
                try:
                    metadata = _json.loads(row["metadata"]) if row["metadata"] else {}
                except Exception:  # noqa: BLE001
                    metadata = {}
                messages.append({
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "metadata": metadata,
                })
            return messages
        except Exception as error:  # noqa: BLE001 - 读失败回退内存路径
            logger.warning(f"FileChatStore 读取失败，回退内存路径: {error}")
            return []

    def list_session_ids(self) -> list[str]:
        """存储中有消息记录的会话 id 列表（启动恢复会话元数据用）。"""
        if not self.path.exists():
            return []
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                rows = connection.execute(
                    "SELECT DISTINCT session_id FROM chat_messages"
                ).fetchall()
            return [row["session_id"] for row in rows]
        except Exception as error:  # noqa: BLE001
            logger.warning(f"FileChatStore 会话列表读取失败: {error}")
            return []

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session
            ON chat_messages(session_id, created_at)
            """
        )
