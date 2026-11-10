"""安全审计服务测试（append-only、non-blocking、查询过滤）"""

from pathlib import Path

from app.api.services.secure_audit_service import SecureAuditService


def _fresh_store(tmp_path: Path) -> SecureAuditService:
    return SecureAuditService(path=tmp_path / "secure_audit_test.sqlite3")


class TestRecordAndList:
    def test_record_and_list_roundtrip(self, tmp_path):
        store = _fresh_store(tmp_path)
        payload = store.record(
            category="security",
            action="injection.blocked",
            actor_username=None,
            session_id="sess-1",
            ip="127.0.0.1",
            result="blocked",
            detail={"hits": ["指令覆盖"], "total_weight": 3},
        )
        assert payload is not None
        events = store.list_events()
        assert len(events) == 1
        assert events[0]["action"] == "injection.blocked"
        assert events[0]["detail"]["hits"] == ["指令覆盖"]
        # 倒序：后写的在前
        store.record(category="query", action="query.answered", actor_username="admin", result="ok")
        events = store.list_events()
        assert events[0]["action"] == "query.answered"
        assert events[1]["action"] == "injection.blocked"

    def test_category_and_actor_filters(self, tmp_path):
        store = _fresh_store(tmp_path)
        store.record(category="security", action="injection.warn", result="masked")
        store.record(category="query", action="query.answered", actor_username="zhangsan", result="ok")
        store.record(category="query", action="query.answered", actor_username="lisi", result="ok")

        assert len(store.list_events(category="security")) == 1
        assert len(store.list_events(category="query")) == 2
        assert len(store.list_events(actor_username="zhangsan")) == 1

    def test_limit_caps_results(self, tmp_path):
        store = _fresh_store(tmp_path)
        for _ in range(10):
            store.record(category="query", action="query.answered")
        assert len(store.list_events(limit=3)) == 3

    def test_empty_db_returns_empty_list(self, tmp_path):
        store = _fresh_store(tmp_path)
        assert store.list_events() == []

    def test_record_never_raises(self, tmp_path):
        # non-blocking 契约：底层失败（路径不可写）也只返回 None
        store = SecureAuditService(path=tmp_path / "no_dir" / "sub" / "x.sqlite3")
        # 正常可创建；这里验证非法路径场景
        broken = SecureAuditService(path=tmp_path / "not_a_dir.sqlite3" / "nested.sqlite3")
        # 把父路径变成文件使创建失败
        (tmp_path / "not_a_dir.sqlite3").write_text("not a dir")
        result = broken.record(category="security", action="test", result="ok")
        assert result is None  # 不抛异常


class TestAppendOnly:
    def test_no_update_or_delete_methods(self, tmp_path):
        # 审计库契约：只有 record/list，不提供修改/删除入口
        store = _fresh_store(tmp_path)
        assert not hasattr(store, "update_event")
        assert not hasattr(store, "delete_event")
