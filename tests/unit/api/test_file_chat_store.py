"""FileChatStore 测试（sqlite 持久化、会话恢复、协议契约）"""

import asyncio
from pathlib import Path

import pytest

from app.modules.core.session.services.file_chat_store import FileChatStore


@pytest.fixture
def store(tmp_path: Path) -> FileChatStore:
    return FileChatStore(path=tmp_path / "chat_messages_test.sqlite3")


def test_save_and_restore_roundtrip(store: FileChatStore, tmp_path: Path):
    """核心场景：写入对话 → 用全新实例（模拟重启）读回。"""

    async def scenario():
        await store.save_exchange(
            session_id="s-1",
            user_message="年假有几天",
            assistant_response="根据《员工考勤与请假制度》…",
            user_metadata={"message_id": "m1"},
            assistant_metadata={"message_id": "m1", "tokens_used": 39},
        )
        await store.save_exchange(
            session_id="s-1",
            user_message="病假呢",
            assistant_response="病假期间工资按 70% 发放。",
            user_metadata={"message_id": "m2"},
            assistant_metadata={"message_id": "m2"},
        )

    asyncio.run(scenario())

    # 模拟重启：全新实例指向同一文件
    revived = FileChatStore(path=store.path)

    async def read_back():
        return await revived.get_session_messages("s-1")

    messages = asyncio.run(read_back())
    assert len(messages) == 4  # 2 轮 = 4 条消息
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "年假有几天"
    assert messages[1]["role"] == "assistant"
    assert "考勤与请假制度" in messages[1]["content"]
    assert messages[1]["metadata"]["tokens_used"] == 39
    # 时间序
    assert messages[2]["content"] == "病假呢"


def test_session_isolation(store: FileChatStore):
    async def scenario():
        await store.save_exchange("s-a", "问题A", "回答A", {}, {})
        await store.save_exchange("s-b", "问题B", "回答B", {}, {})

    asyncio.run(scenario())
    messages = asyncio.run(store.get_session_messages("s-a"))
    assert len(messages) == 2
    assert messages[0]["content"] == "问题A"


def test_list_session_ids(store: FileChatStore):
    async def scenario():
        await store.save_exchange("s-a", "q", "a", {}, {})
        await store.save_exchange("s-b", "q", "a", {}, {})

    asyncio.run(scenario())
    ids = store.list_session_ids()
    assert set(ids) == {"s-a", "s-b"}


def test_missing_session_returns_empty(store: FileChatStore):
    messages = asyncio.run(store.get_session_messages("nonexistent"))
    assert messages == []


def test_read_failure_returns_empty(tmp_path: Path):
    # 目录路径被文件占位 → sqlite 打开失败 → 返回空（不抛异常）
    broken_dir = tmp_path / "blocker"
    broken_dir.write_text("not a dir")
    store = FileChatStore(path=broken_dir / "chat.sqlite3")
    messages = asyncio.run(store.get_session_messages("any"))
    assert messages == []
