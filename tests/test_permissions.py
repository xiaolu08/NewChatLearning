import asyncio

from new_chat_learning.application.runtime import RuntimeApplication
from new_chat_learning.commands.permissions import is_group_admin, is_plugin_admin


class Event:
    def __init__(self, sender_id: str, admin: bool = False, group_id: str = "10001"):
        self.sender_id = sender_id
        self.admin = admin
        self.group_id = group_id

    def is_admin(self):
        return self.admin

    def get_sender_id(self):
        return self.sender_id

    def get_group_id(self):
        return self.group_id


def test_astrbot_admin_is_allowed():
    assert is_plugin_admin(Event("1", admin=True), {}) is True


def test_configured_plugin_admin_is_allowed():
    config = {"permissions": {"plugin_admin_ids": ["42"]}}
    assert is_plugin_admin(Event("42"), config) is True


def test_regular_member_is_silently_rejected():
    assert is_plugin_admin(Event("7"), {}) is False


def test_group_sub_admin_is_scoped_to_configured_group():
    config = {
        "permissions": {
            "group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]
        }
    }
    assert is_group_admin(Event("7", group_id="10001"), config) is True
    assert is_group_admin(Event("7", group_id="10002"), config) is False


def test_runtime_permission_update_audits_counts_without_ids(tmp_path):
    class Source(dict):
        async def save_config_async(self):
            return True

    async def scenario():
        source = Source(
            {
                "permissions": {
                    "plugin_admin_ids": ["12345"],
                    "group_sub_admins": [
                        {"group_id": "10001", "admin_ids": ["23456"]}
                    ],
                }
            }
        )
        app = RuntimeApplication(tmp_path, source)
        await app.start()
        try:
            result = await app.update_permission_settings(
                values={
                    "plugin_admin_ids": ["12345", "34567"],
                    "group_sub_admins": [
                        {"group_id": "10001", "admin_ids": ["23456", "45678"]},
                        {"group_id": "10002", "admin_ids": ["56789"]},
                    ],
                },
                expected_revision=app.config.revision,
                actor_id="webui:test",
            )
            audit = await app.audit.list_entries(action="update_permission_settings")
            return result, audit
        finally:
            await app.stop()

    result, audit = asyncio.run(scenario())

    assert result["revision"]
    assert audit["entries"][0]["details"] == {
        "before_plugin_admin_count": 1,
        "after_plugin_admin_count": 2,
        "before_group_count": 1,
        "after_group_count": 2,
        "before_sub_admin_count": 1,
        "after_sub_admin_count": 3,
        "source": "webui",
    }


def test_runtime_cross_group_update_audits_operation_without_group_ids(tmp_path):
    class Source(dict):
        async def save_config_async(self):
            return True

    async def scenario():
        source = Source()
        app = RuntimeApplication(tmp_path, source)
        await app.start()
        try:
            result = await app.update_cross_group_settings(
                action="add",
                category="learning",
                group_ids=["10001", "10002"],
                expected_revision=app.config.revision,
                actor_id="12345",
            )
            audit = await app.audit.list_entries(
                action="update_cross_group_settings"
            )
            return result, audit
        finally:
            await app.stop()

    result, audit = asyncio.run(scenario())

    assert result["learning_group_ids"] == ["10001", "10002"]
    assert audit["entries"][0]["details"] == {
        "operation": "add",
        "category": "learning",
        "group_count": 2,
        "source": "legacy_command",
    }


def test_runtime_global_switch_update_is_audited(tmp_path):
    class Source(dict):
        async def save_config_async(self):
            return True

    async def scenario():
        app = RuntimeApplication(tmp_path, Source())
        await app.start()
        try:
            result = await app.update_global_switch(
                capability="reply",
                enabled=True,
                expected_revision=app.config.revision,
                actor_id="12345",
            )
            audit = await app.audit.list_entries(action="update_global_switch")
            return result, audit
        finally:
            await app.stop()

    result, audit = asyncio.run(scenario())

    assert result["reply_enabled"] is True
    assert audit["entries"][0]["details"] == {
        "capability": "reply",
        "enabled": True,
        "source": "legacy_private_command",
    }
