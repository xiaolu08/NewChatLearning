import asyncio

from new_chat_learning.application.runtime import RuntimeApplication


def test_runtime_group_settings_records_command_source(tmp_path):
    class Source(dict):
        async def save_config_async(self):
            return True

    async def scenario():
        app = RuntimeApplication(tmp_path, Source())
        await app.start()
        result = await app.update_group_settings(
            group_id="10001",
            mode="learning",
            target_user_ids=["12345"],
            expected_revision=app.config.revision,
            actor_id="7",
            source="command",
        )
        audit = await app.audit.list_entries(action="update_group_settings", limit=1)
        await app.stop()
        return result, audit["entries"][0]

    result, row = asyncio.run(scenario())

    assert result["mode"] == "learning"
    assert row["actor"] == "7"
    assert row["action"] == "update_group_settings"
    assert row["target"] == "group:10001"
    assert row["details"]["source"] == "command"


def test_runtime_share_welcome_records_audit_without_message_content(tmp_path):
    class Source(dict):
        async def save_config_async(self):
            return True

    async def scenario():
        app = RuntimeApplication(
            tmp_path,
            Source(
                {
                    "library": {
                        "share_groups": [
                            {"name": "牛牛联动组", "group_ids": ["10001"]}
                        ]
                    }
                }
            ),
        )
        await app.start()
        await app.update_share_welcome_message(
            group_name="牛牛联动组",
            message="欢迎加入！",
            expected_revision=app.config.revision,
            actor_id="7",
        )
        audit = await app.audit.list_entries(
            action="update_share_welcome_message", limit=1
        )
        await app.stop()
        return audit["entries"][0]

    row = asyncio.run(scenario())

    assert row["target"] == "share_group:牛牛联动组"
    assert row["details"] == {"operation": "set", "source": "legacy_command"}


def test_runtime_share_reply_cooldown_records_minimal_audit(tmp_path):
    class Source(dict):
        async def save_config_async(self):
            return True

    async def scenario():
        app = RuntimeApplication(
            tmp_path,
            Source(
                {
                    "library": {
                        "share_groups": [
                            {"name": "牛牛联动组", "group_ids": ["10001"]}
                        ]
                    }
                }
            ),
        )
        await app.start()
        await app.update_share_reply_cooldown(
            group_name="牛牛联动组",
            minutes=50,
            expected_revision=app.config.revision,
            actor_id="7",
        )
        audit = await app.audit.list_entries(
            action="update_share_reply_cooldown", limit=1
        )
        await app.stop()
        return audit["entries"][0]

    row = asyncio.run(scenario())

    assert row["target"] == "share_group:牛牛联动组"
    assert row["details"] == {
        "operation": "set",
        "cooldown_minutes": 50,
        "source": "legacy_command",
    }
