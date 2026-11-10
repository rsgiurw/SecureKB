"""[MOD] SecureKB 安全审计服务（2026-11-10 · 安全模块：审计日志）。

三类审计事件统一落库（append-only，SQLite WAL，零外部依赖）：
  security  安全事件——提示注入拦截（injection.blocked/warn）、PII 入口掩码
            （pii.masked）、未授权访问拒绝（auth.denied）
  query     查询行为——登录用户问了什么（掩码文本）、命中哪些文档、走了
            哪条路径（SSE/REST/寒暄短路）——Security Trimming 的访问留痕
  admin     管理操作——文档上传/删除等（upload.py 的 operational audit
            沿用旧表，本表只接新事件）

与 upload.py 的 audit_events（操作审计）分表：两者 schema 语义不同
（操作 vs 行为/安全），混表会让查询和保留策略互相干扰。

非阻塞原则与 upload.py 一致：审计失败只记警告日志，绝不阻断业务请求。
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

# 数据文件与 upload 审计库同目录（data/），独立文件避免 schema 干扰
DEFAULT_DB_PATH = Path("data/secure_audit_events.sqlite3")


class SecureAuditService:
    """安全审计服务（append-only）"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_DB_PATH

    # ----------------------------------------
    # 写入（non-blocking 由调用方保证：本类方法自身不抛异常）
    # ----------------------------------------
    def record(
        self,
        *,
        category: str,
        action: str,
        actor_username: str | None = None,
        actor_role: str | None = None,
        session_id: str | None = None,
        ip: str | None = None,
        result: str = "ok",
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """记录一条审计事件。失败返回 None（不抛出——审计绝不阻断业务）。"""
        try:
            payload = {
                "event_id": str(uuid4()),
                "created_at": datetime.now().isoformat(),
                "category": category,
                "action": action,
                "actor_username": actor_username,
                "actor_role": actor_role,
                "session_id": session_id,
                "ip": ip,
                "result": result,
                "detail": detail or {},
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.execute(
                    """
                    INSERT INTO secure_audit_events (
                        event_id, created_at, category, action,
                        actor_username, actor_role, session_id, ip,
                        result, detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["event_id"],
                        payload["created_at"],
                        payload["category"],
                        payload["action"],
                        payload["actor_username"],
                        payload["actor_role"],
                        payload["session_id"],
                        payload["ip"],
                        payload["result"],
                        json.dumps(payload["detail"], ensure_ascii=False, default=str),
                    ),
                )
                connection.commit()
            return payload
        except Exception as error:  # noqa: BLE001 - 审计失败不阻断业务
            logger.warning(f"审计事件记录失败: {error}")
            return None

    # ----------------------------------------
    # 查询（管理端）
    # ----------------------------------------
    def list_events(
        self,
        category: str | None = None,
        actor_username: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """按时间倒序查询审计事件（管理端）。"""
        try:
            if not self.path.exists():
                return []
            limit = max(1, min(int(limit), 1000))
            with self._connect() as connection:
                self._ensure_schema(connection)
                conditions: list[str] = []
                params: list[Any] = []
                if category:
                    conditions.append("category = ?")
                    params.append(category)
                if actor_username:
                    conditions.append("actor_username = ?")
                    params.append(actor_username)
                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                params.append(limit)
                rows = connection.execute(
                    f"""
                    SELECT event_id, created_at, category, action,
                           actor_username, actor_role, session_id, ip,
                           result, detail
                    FROM secure_audit_events
                    {where}
                    ORDER BY created_at DESC, event_id DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            events = []
            for row in rows:
                events.append({
                    "event_id": row["event_id"],
                    "created_at": row["created_at"],
                    "category": row["category"],
                    "action": row["action"],
                    "actor_username": row["actor_username"],
                    "actor_role": row["actor_role"],
                    "session_id": row["session_id"],
                    "ip": row["ip"],
                    "result": row["result"],
                    "detail": json.loads(row["detail"]) if row["detail"] else {},
                })
            return events
        except Exception as error:  # noqa: BLE001
            logger.warning(f"审计事件查询失败: {error}")
            return []

    # ----------------------------------------
    # 内部
    # ----------------------------------------
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS secure_audit_events (
                event_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                category TEXT NOT NULL,
                action TEXT NOT NULL,
                actor_username TEXT,
                actor_role TEXT,
                session_id TEXT,
                ip TEXT,
                result TEXT NOT NULL,
                detail TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_secure_audit_category_created
            ON secure_audit_events(category, created_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_secure_audit_actor
            ON secure_audit_events(actor_username, created_at)
            """
        )


# 模块级单例（与 upload.py 的 _audit_event_store 同模式：惰性创建、进程内共享）
_instance: SecureAuditService | None = None


def get_secure_audit_service() -> SecureAuditService:
    global _instance
    if _instance is None:
        _instance = SecureAuditService()
    return _instance
