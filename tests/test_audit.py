import asyncio
import json

import pytest

from new_chat_learning.application.audit import AuditService
from new_chat_learning.infrastructure.database import SQLiteStore


def test_audit_service_pages_filters_and_redacts_sensitive_values(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "audit.sqlite3")
        await store.open()
        service = AuditService(store)
        try:
            await store.record_audit(
                actor_id="127.0.0.1",
                action="webui_login",
                target="webui",
                details={"result": "success", "password": "must-not-appear"},
            )
            await store.record_audit(
                actor_id="webui:abcdef1234567890",
                action="restore_database_backup",
                target="database",
                details={
                    "backup_name": "chosen.sqlite3",
                    "safety_backup_name": "before-restore.sqlite3",
                    "backup_path": "C:\\private\\chosen.sqlite3",
                    "message_text": "must-not-appear",
                },
            )
            await store.record_audit(
                actor_id="7",
                action="delete_answer",
                target="private/answer:10",
                details={"group_id": "10001", "question_id": 1},
            )
            first = await service.list_entries(limit=2)
            second = await service.list_entries(
                before_id=first["next_before_id"], limit=2
            )
            filtered = await service.list_entries(action="webui_login", limit=10)
            return first, second, filtered
        finally:
            await store.close()

    first, second, filtered = asyncio.run(scenario())
    assert [entry["action"] for entry in first["entries"]] == [
        "delete_answer",
        "restore_database_backup",
    ]
    assert first["has_more"] is True
    assert first["next_before_id"] == first["entries"][-1]["id"]
    assert first["entries"][0]["target"] == "已隐藏"
    assert second["entries"][0]["action"] == "webui_login"
    assert second["entries"][0]["actor"] == "客户端地址已隐藏"
    restored = first["entries"][1]
    assert restored["actor"] == "WebUI 会话"
    assert restored["details"] == {
        "backup_name": "chosen.sqlite3",
        "safety_backup_name": "before-restore.sqlite3",
    }
    assert [entry["action"] for entry in filtered["entries"]] == ["webui_login"]
    assert any(item["action"] == "delete_answer" for item in first["actions"])
    assert "must-not-appear" not in json.dumps(first, ensure_ascii=False)
    assert "C:\\private" not in json.dumps(first, ensure_ascii=False)


def test_audit_service_rejects_invalid_action_filter(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "audit.sqlite3")
        await store.open()
        try:
            with pytest.raises(ValueError, match="invalid_audit_action"):
                await AuditService(store).list_entries(action="delete_answer OR 1=1")
        finally:
            await store.close()

    asyncio.run(scenario())


def test_permission_audit_exposes_counts_without_admin_ids(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "audit.sqlite3")
        await store.open()
        try:
            await store.record_audit(
                actor_id="webui:abcdef1234567890",
                action="update_permission_settings",
                target="permissions",
                details={
                    "before_plugin_admin_count": 1,
                    "after_plugin_admin_count": 2,
                    "before_group_count": 1,
                    "after_group_count": 2,
                    "before_sub_admin_count": 1,
                    "after_sub_admin_count": 3,
                    "plugin_admin_ids": ["12345", "67890"],
                    "source": "webui",
                },
            )
            return await AuditService(store).list_entries(limit=10)
        finally:
            await store.close()

    result = asyncio.run(scenario())
    entry = result["entries"][0]
    assert entry["action_label"] == "更新权限设置"
    assert entry["details"]["after_sub_admin_count"] == 3
    assert "plugin_admin_ids" not in entry["details"]
