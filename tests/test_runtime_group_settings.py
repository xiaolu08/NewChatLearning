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
