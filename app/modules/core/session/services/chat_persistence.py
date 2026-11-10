"""[MOD] 会话快照持久化（SecureKB 安全模块配套，2026-11-10）。

解决"后端重启即丢失全部会话与对话"的问题：SessionService.sessions（会话
元数据）与 MemoryService.memories（对话消息）都是纯内存结构，上游的
chat_store 注入只覆盖消息的"读写恢复"，会话元数据（侧栏列表、TTL 校验、
messages_metadata 对齐）仍依赖内存。

本模块以 JSON 快照覆盖两层：
- 保存：sessions dict（datetime 用 default=str）+ 各会话的 LangChain 消息
  （type/content），原子写（tmp + replace）；
- 恢复：启动时回填 SessionService.sessions（last_accessed 等字符串解析回
  datetime，TTL 校验依赖 datetime 类型）与 MemoryService.memories（重建
  InMemoryChatMessageHistory）。

挂接点：
- 保存：chat_service.add_conversation_to_session 末尾 + 会话删除端点；
- 恢复：main.py 启动完成（服务 DI 就绪）后调用一次 restore()。

注意：消息全文由 FileChatStore（chat_store 协议）持久化，恢复的消息正文
以 FileChatStore 为准；本快照中的消息副本用于 MemoryService 内存窗口的
即时恢复（get_context_string/内存路径），两层各司其职。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.lib.logger import get_logger

logger = get_logger(__name__)

_DATETIME_FIELDS = ("created_at", "updated_at", "last_accessed")


class ChatSnapshotPersistence:
    """会话元数据 + 内存消息的 JSON 快照（保存/恢复）"""

    def __init__(
        self,
        session_service: Any,
        memory_service: Any,
        path: Path,
    ) -> None:
        self.session_service = session_service
        self.memory_service = memory_service
        self.path = path

    # ----------------------------------------
    # 保存
    # ----------------------------------------
    def save(self) -> None:
        """原子写快照。失败只记日志（不阻断对话主流程）。"""
        try:
            snapshot: dict[str, Any] = {"sessions": {}, "messages": {}}
            for session_id, session in self.session_service.sessions.items():
                snapshot["sessions"][session_id] = self._serialize_session(session)
            for session_id, history in self.memory_service.memories.items():
                snapshot["messages"][session_id] = [
                    {"type": m.__class__.__name__, "content": m.content}
                    for m in history.messages
                ]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, default=str)
            tmp.replace(self.path)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"会话快照保存失败（不阻断业务）: {error}")

    # ----------------------------------------
    # 恢复
    # ----------------------------------------
    def restore(self) -> int:
        """启动时从快照恢复会话与消息。返回恢复的会话数。"""
        if not self.path.exists():
            return 0
        try:
            snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001
            logger.warning(f"会话快照解析失败，跳过恢复: {error}")
            return 0

        restored = 0
        for session_id, session in snapshot.get("sessions", {}).items():
            try:
                revived = self._revive_session(session)
                self.session_service.sessions[session_id] = revived
                restored += 1
            except Exception as error:  # noqa: BLE001
                logger.warning(f"会话恢复失败 session_id={session_id}: {error}")

        from langchain_core.messages import AIMessage, HumanMessage

        for session_id, messages in snapshot.get("messages", {}).items():
            if session_id not in self.session_service.sessions:
                continue  # 只恢复有会话记录的消息（防止孤儿数据无限增长）
            history = self.memory_service.memories.get(session_id)
            if history is None:
                history = self.memory_service.create_memory(session_id)
            revived_messages = []
            for m in messages:
                content = m.get("content", "")
                if m.get("type") == "HumanMessage":
                    revived_messages.append(HumanMessage(content=content))
                elif m.get("type") == "AIMessage":
                    revived_messages.append(AIMessage(content=content))
            history.messages = revived_messages

        if restored:
            logger.info(f"会话快照恢复完成: {restored} 个会话")
        return restored

    # ----------------------------------------
    # 内部
    # ----------------------------------------
    def _serialize_session(self, session: dict[str, Any]) -> dict[str, Any]:
        """会话 dict 序列化（datetime → isoformat 由 default=str 完成）。"""
        serializable: dict[str, Any] = {}
        for key, value in session.items():
            if key == "location":
                serializable[key] = None  # IP 地理数据不入快照（非必要）
            else:
                serializable[key] = value
        return serializable

    def _revive_session(self, session: dict[str, Any]) -> dict[str, Any]:
        """恢复会话 dict：把时间字符串字段解析回 datetime（TTL 校验依赖）。"""
        from datetime import timezone

        revived = dict(session)
        for key in _DATETIME_FIELDS:
            value = revived.get(key)
            if isinstance(value, str):
                try:
                    revived[key] = datetime.fromisoformat(value)
                except ValueError:
                    revived[key] = datetime.now(timezone.utc)
        last_accessed = revived.get("last_accessed")
        if not isinstance(last_accessed, datetime):
            revived["last_accessed"] = datetime.now(timezone.utc)
        return revived
