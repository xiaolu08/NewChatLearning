import asyncio
from copy import deepcopy
from threading import Lock

import pytest

from new_chat_learning.infrastructure.config import ConfigService


def test_config_merges_defaults_and_has_stable_revision():
    service = ConfigService({"storage": {"media_quota_gb": 5.0}})

    snapshot = service.snapshot()

    assert snapshot["storage"]["media_quota_gb"] == 5.0
    assert snapshot["tts"]["enabled"] is False
    assert snapshot["learning"]["enabled"] is False
    assert service.revision == service.revision


def test_learning_requires_global_switch_and_explicit_group():
    service = ConfigService({"learning": {"enabled": True, "group_ids": ["10001"]}})

    assert service.learning_enabled_for("10001") is True
    assert service.learning_enabled_for("10002") is False
    assert service.learning_interval_seconds == 900

    disabled = ConfigService(
        {
            "general": {"enabled": False},
            "learning": {"enabled": True, "group_ids": ["10001"]},
        }
    )
    assert disabled.learning_enabled_for("10001") is False


def test_reply_requires_explicit_group_and_silent_group_wins():
    service = ConfigService(
        {
            "reply": {
                "enabled": True,
                "group_ids": ["10001", "10002"],
                "silent_group_ids": ["10002"],
                "probability_percent": 120,
                "cooldown_seconds": -1,
            }
        }
    )

    assert service.reply_enabled_for("10001") is True
    assert service.reply_enabled_for("10002") is False
    assert service.reply_enabled_for("10003") is False
    assert service.reply_settings()["probability_percent"] == 100.0
    assert service.reply_settings()["cooldown_seconds"] == 0.0


def test_reply_type_probability_overrides_group_base_probability():
    service = ConfigService(
        {
            "reply": {
                "probability_percent": 50,
                "group_probability_overrides": [
                    {"group_id": "10001", "probability_percent": 20}
                ],
                "group_type_probability_overrides": [
                    {
                        "group_id": "10001",
                        "message_type": "xml",
                        "probability_percent": 80,
                    }
                ],
            }
        }
    )

    assert service.reply_settings("10001", "text")["probability_percent"] == 20
    assert service.reply_settings("10001", "xml")["probability_percent"] == 80
    assert service.reply_settings("10002", "xml")["probability_percent"] == 50


def test_legacy_probability_update_clears_type_specific_overrides():
    class Source(dict):
        async def save_config_async(self):
            return True

    source = Source(
        {
            "reply": {
                "group_probability_overrides": [],
                "group_type_probability_overrides": [
                    {
                        "group_id": "10001",
                        "message_type": "xml",
                        "probability_percent": 80,
                    }
                ],
            }
        }
    )
    service = ConfigService(source)

    asyncio.run(
        service.update_cross_group_settings(
            action="set",
            category="reply_probability",
            group_ids=["10001"],
            expected_revision=service.revision,
            tag="25",
        )
    )

    assert service.reply_settings("10001", "xml")["probability_percent"] == 25
    assert source["reply"]["group_type_probability_overrides"] == []


def test_type_probability_update_preserves_group_base_and_other_types():
    class Source(dict):
        async def save_config_async(self):
            return True

    source = Source(
        {
            "reply": {
                "group_probability_overrides": [
                    {"group_id": "10001", "probability_percent": 25}
                ],
                "group_type_probability_overrides": [
                    {
                        "group_id": "10001",
                        "message_type": "text",
                        "probability_percent": 60,
                    }
                ],
            }
        }
    )
    service = ConfigService(source)

    result = asyncio.run(
        service.update_cross_group_settings(
            action="set",
            category="reply_type_probability",
            group_ids=["10001"],
            expected_revision=service.revision,
            tag="80",
            message_type="xml",
        )
    )

    assert service.reply_settings("10001", "text")["probability_percent"] == 60
    assert service.reply_settings("10001", "xml")["probability_percent"] == 80
    assert service.reply_settings("10001", "image")["probability_percent"] == 25
    assert len(result["group_reply_type_probabilities"]) == 2


def test_matching_settings_are_bounded_and_type_thresholds_are_normalized():
    service = ConfigService(
        {
            "reply": {
                "regex_timeout_ms": 0,
                "similarity_threshold": 2,
                "similarity_max_length": -1,
                "type_frequency_thresholds": {"Image": 3, "Plain": -2},
            }
        }
    )

    settings = service.reply_settings()

    assert settings["regex_timeout_ms"] == 1
    assert settings["similarity_threshold"] == 1.0
    assert settings["similarity_max_length"] == 1
    assert settings["type_frequency_thresholds"] == {"image": 3, "plain": 0}


def test_media_settings_convert_units_and_enforce_bounds():
    service = ConfigService(
        {
            "storage": {
                "media_persistence_enabled": True,
                "media_quota_gb": 1,
                "media_max_file_mb": 2,
                "media_download_timeout_seconds": 0,
            }
        }
    )

    settings = service.media_settings()

    assert settings["enabled"] is True
    assert settings["quota_bytes"] == 1024**3
    assert settings["max_file_bytes"] == 2 * 1024**2
    assert settings["timeout_seconds"] == 1.0


def test_group_library_scope_isolated_by_default():
    service = ConfigService({})

    assert service.reply_library_scopes("10001", ["10001", "10002"]) == (("10001",),)


def test_global_library_excludes_groups_and_tagged_libraries():
    service = ConfigService(
        {
            "library": {
                "mode": "global",
                "excluded_group_ids": ["10003"],
                "group_tags": [
                    {"group_id": "10002", "tags": ["friends"]},
                    {"group_id": "10004", "tags": ["friends"]},
                ],
            }
        }
    )

    assert service.reply_library_scopes("10001", ["10001", "10002", "10003", "10004"]) == (
        ("10001",),
    )
    assert service.reply_library_scopes("10002", ["10001", "10002", "10003", "10004"]) == (
        ("10002", "10004"),
    )


def test_multiple_tags_create_separate_weighted_scopes_and_accept_legacy_dict():
    service = ConfigService(
        {
            "library": {
                "mode": "global",
                "group_tags": {
                    "10001": ["friends", "games", "friends"],
                    "10002": ["friends"],
                    "10003": ["games"],
                },
            }
        }
    )

    assert service.reply_library_scopes("10001", ["10001", "10002", "10003"]) == (
        ("10001", "10002"),
        ("10001", "10003"),
    )


def test_local_only_group_does_not_query_global_or_tagged_libraries():
    service = ConfigService(
        {
            "library": {
                "mode": "global",
                "local_only_group_ids": ["10001"],
                "group_tags": [
                    {"group_id": "10001", "tags": ["friends"]},
                    {"group_id": "10002", "tags": ["friends"]},
                ],
            }
        }
    )

    assert service.reply_library_scopes("10001", ["10001", "10002"]) == (("10001",),)


def test_explicit_global_group_queries_shared_library_in_group_mode():
    service = ConfigService(
        {
            "library": {
                "mode": "group",
                "global_group_ids": ["10001"],
            }
        }
    )

    assert service.reply_library_scopes("10001", ["10001", "10002"]) == (
        ("10001", "10002"),
    )
    assert service.reply_library_scopes("10002", ["10001", "10002"]) == (("10002",),)


def test_share_groups_append_only_direct_member_group_libraries():
    service = ConfigService(
        {
            "library": {
                "mode": "group",
                "local_only_group_ids": ["10001"],
                "global_group_ids": ["10002"],
                "share_groups": [
                    {"name": "联动词库1", "group_ids": ["10001", "10002"]},
                    {"name": "联动词库2", "group_ids": ["10002", "10003"]},
                ],
            }
        }
    )

    available = ["10001", "10002", "10003", "10004"]
    assert service.reply_library_scopes("10001", available) == (
        ("10001",),
        ("10002",),
    )
    assert service.reply_library_scopes("10004", available) == (("10004",),)


def test_share_group_membership_is_created_updated_and_removed_atomically():
    class Source(dict):
        async def save_config_async(self):
            return True

    service = ConfigService(Source())
    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="share",
            group_ids=["123456789", "987654321"],
            tag="联动词库1",
            expected_revision=service.revision,
        )
    )
    assert result["share_groups"] == [
        {
            "name": "联动词库1",
            "group_ids": ["123456789", "987654321"],
        }
    ]
    assert service.configured_group_ids() == ["123456789", "987654321"]

    result = asyncio.run(
        service.update_cross_group_settings(
            action="remove",
            category="share",
            group_ids=["123456789"],
            tag="联动词库1",
            expected_revision=service.revision,
        )
    )
    assert result["share_groups"] == [
        {"name": "联动词库1", "group_ids": ["987654321"]}
    ]

    result = asyncio.run(
        service.update_cross_group_settings(
            action="remove",
            category="share",
            group_ids=["987654321"],
            tag="联动词库1",
            expected_revision=service.revision,
        )
    )
    assert result["share_groups"] == []


def test_share_group_welcome_can_be_set_used_preserved_and_removed():
    class Source(dict):
        async def save_config_async(self):
            return True

    service = ConfigService(
        Source(
            {
                "library": {
                    "share_groups": [
                        {"name": "牛牛联动组", "group_ids": ["10001", "10002"]}
                    ]
                }
            }
        )
    )
    result = asyncio.run(
        service.update_share_welcome_message(
            group_name="牛牛联动组",
            message="博士，欢迎加入这盛大的庆典！",
            expected_revision=service.revision,
        )
    )
    assert result["share_groups"][0]["welcome_message"] == "博士，欢迎加入这盛大的庆典！"
    assert service.share_welcome_messages_for("10001") == (
        "博士，欢迎加入这盛大的庆典！",
    )
    assert service.share_welcome_messages_for("99999") == ()

    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="share",
            group_ids=["10003"],
            tag="牛牛联动组",
            expected_revision=service.revision,
        )
    )
    assert result["share_groups"][0]["welcome_message"] == "博士，欢迎加入这盛大的庆典！"
    assert service.share_welcome_messages_for("10003") == (
        "博士，欢迎加入这盛大的庆典！",
    )

    result = asyncio.run(
        service.update_share_welcome_message(
            group_name="牛牛联动组",
            message=None,
            expected_revision=service.revision,
        )
    )
    assert "welcome_message" not in result["share_groups"][0]
    assert service.share_welcome_messages_for("10001") == ()


def test_share_group_reply_cooldown_can_be_set_used_preserved_and_removed():
    class Source(dict):
        async def save_config_async(self):
            return True

    service = ConfigService(
        Source(
            {
                "library": {
                    "share_groups": [
                        {
                            "name": "牛牛联动组",
                            "group_ids": ["10001", "10002"],
                            "welcome_message": "欢迎加入！",
                        }
                    ]
                }
            }
        )
    )
    result = asyncio.run(
        service.update_share_reply_cooldown(
            group_name="牛牛联动组",
            minutes=50,
            expected_revision=service.revision,
        )
    )
    assert result["share_groups"][0]["reply_cooldown_minutes"] == 50
    assert result["share_groups"][0]["welcome_message"] == "欢迎加入！"
    assert service.share_reply_cooldowns_for("10001") == (("牛牛联动组", 3000),)
    assert service.share_reply_cooldowns_for("99999") == ()

    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="share",
            group_ids=["10003"],
            tag="牛牛联动组",
            expected_revision=service.revision,
        )
    )
    assert result["share_groups"][0]["reply_cooldown_minutes"] == 50
    assert service.share_reply_cooldowns_for("10003") == (("牛牛联动组", 3000),)

    result = asyncio.run(
        service.update_share_reply_cooldown(
            group_name="牛牛联动组",
            minutes=None,
            expected_revision=service.revision,
        )
    )
    assert "reply_cooldown_minutes" not in result["share_groups"][0]
    assert result["share_groups"][0]["welcome_message"] == "欢迎加入！"
    assert service.share_reply_cooldowns_for("10001") == ()

    with pytest.raises(ValueError, match="invalid_share_reply_cooldown"):
        asyncio.run(
            service.update_share_reply_cooldown(
                group_name="牛牛联动组",
                minutes=0,
                expected_revision=service.revision,
            )
        )


def test_share_group_sanhao_learning_is_set_preserved_and_removed():
    class Source(dict):
        async def save_config_async(self):
            return True

    service = ConfigService(
        Source(
            {
                "library": {
                    "share_groups": [
                        {
                            "name": "牛牛联动组",
                            "group_ids": ["10001", "10002"],
                            "welcome_message": "欢迎加入！",
                            "reply_cooldown_minutes": 50,
                        }
                    ]
                }
            }
        )
    )
    result = asyncio.run(
        service.update_share_sanhao_learning(
            group_name="牛牛联动组",
            enabled=True,
            expected_revision=service.revision,
        )
    )
    assert result["share_groups"][0]["sanhao_learning_enabled"] is True
    assert result["share_groups"][0]["welcome_message"] == "欢迎加入！"
    assert result["share_groups"][0]["reply_cooldown_minutes"] == 50
    assert service.sanhao_learning_enabled_for("10001") is True
    assert service.sanhao_learning_enabled_for("99999") is False

    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="share",
            group_ids=["10003"],
            tag="牛牛联动组",
            expected_revision=service.revision,
        )
    )
    assert result["share_groups"][0]["sanhao_learning_enabled"] is True
    assert service.sanhao_learning_enabled_for("10003") is True

    result = asyncio.run(
        service.update_share_sanhao_learning(
            group_name="牛牛联动组",
            enabled=False,
            expected_revision=service.revision,
        )
    )
    assert "sanhao_learning_enabled" not in result["share_groups"][0]
    assert result["share_groups"][0]["welcome_message"] == "欢迎加入！"
    assert result["share_groups"][0]["reply_cooldown_minutes"] == 50
    assert service.sanhao_learning_enabled_for("10001") is False


def test_cross_group_settings_lists_effective_global_library_groups():
    service = ConfigService(
        {
            "reply": {"group_ids": ["10001", "10002", "10003"]},
            "library": {
                "mode": "global",
                "global_group_ids": ["10004"],
                "local_only_group_ids": ["10002"],
            },
        }
    )

    settings = service.cross_group_settings()

    assert settings["global_group_ids"] == ["10001", "10003", "10004"]
    assert settings["local_only_group_ids"] == ["10002"]


def test_target_users_are_group_scoped_and_normalized():
    service = ConfigService(
        {
            "learning": {
                "enabled": True,
                "group_ids": ["10001"],
                "target_users": [
                    {"group_id": "10001", "user_ids": ["7", "7", "bad", "8"]},
                    {"group_id": "10002", "user_ids": ["9"]},
                ],
            }
        }
    )

    assert service.learning_target_users_for("10001") == ("7", "8")
    assert service.learning_target_users_for("10002") == ("9",)
    assert service.group_settings("10001")["mode"] == "learning"


def test_group_settings_save_to_astrbot_config_and_reject_stale_revision():
    class Source(dict):
        saves = 0

        async def save_config_async(self):
            self.saves += 1
            return True

    source = Source()
    service = ConfigService(source)
    revision = service.revision

    result = asyncio.run(
        service.update_group_settings(
            group_id="10001",
            mode="silent",
            target_user_ids=["12345", "67890"],
            expected_revision=revision,
        )
    )

    assert result["mode"] == "silent"
    assert result["target_user_ids"] == ["12345", "67890"]
    assert source["learning"]["group_ids"] == ["10001"]
    assert source["reply"]["group_ids"] == []
    assert source["reply"]["silent_group_ids"] == ["10001"]
    assert source.saves == 1
    with pytest.raises(ValueError, match="revision_conflict"):
        asyncio.run(
            service.update_group_settings(
                group_id="10001",
                mode="disabled",
                target_user_ids=[],
                expected_revision=revision,
            )
        )


def test_group_settings_roll_back_when_astrbot_save_fails():
    class Source(dict):
        async def save_config_async(self):
            raise OSError("disk full")

    source = Source({"learning": {"enabled": False, "group_ids": []}})
    service = ConfigService(source)

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            service.update_group_settings(
                group_id="10001",
                mode="learning",
                target_user_ids=[],
                expected_revision=service.revision,
            )
        )

    assert source == {"learning": {"enabled": False, "group_ids": []}}


def test_group_settings_refuse_memory_only_configuration():
    source = {"learning": {"enabled": False, "group_ids": []}}
    service = ConfigService(source)

    with pytest.raises(RuntimeError, match="config_persistence_unavailable"):
        asyncio.run(
            service.update_group_settings(
                group_id="10001",
                mode="learning",
                target_user_ids=[],
                expected_revision=service.revision,
            )
        )

    assert source == {"learning": {"enabled": False, "group_ids": []}}


def test_filter_settings_merge_group_rules_and_persist_with_revision():
    class Source(dict):
        async def save_config_async(self):
            return True

    source = Source(
        {
            "filters": {
                "contains": ["global"],
                "group_rules": [{"group_id": "10001", "contains": ["local"]}],
            }
        }
    )
    service = ConfigService(source)
    assert service.filter_settings("10001")["contains"] == ["global", "local"]

    result = asyncio.run(
        service.update_filter_settings(
            values={
                "enabled": True,
                "contains": ["new"],
                "exact": [],
                "regex": ["^ok$"],
                "component_types": ["At"],
                "sensitive": ["secret"],
                "blacklist_threshold": 3,
                "blacklist_scope": "group",
                "regex_timeout_ms": 20,
                "group_rules": [],
            },
            expected_revision=service.revision,
        )
    )
    assert result["contains"] == ["new"]
    assert result["blacklist_scope"] == "group"
    with pytest.raises(ValueError, match="revision_conflict"):
        asyncio.run(
            service.update_filter_settings(
                values=source["filters"],
                expected_revision="stale",
            )
        )


def test_filter_settings_roll_back_on_save_failure():
    class Source(dict):
        async def save_config_async(self):
            raise OSError("disk full")

    source = Source({"filters": {"enabled": True, "contains": ["old"]}})
    service = ConfigService(source)
    before = deepcopy(source)
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            service.update_filter_settings(
                values={"enabled": True, "contains": ["new"], "group_rules": []},
                expected_revision=service.revision,
            )
        )
    assert source == before


def test_cross_group_settings_update_all_legacy_categories_atomically():
    class Source(dict):
        saves = 0

        async def save_config_async(self):
            self.saves += 1
            return True

    source = Source(
        {
            "reply": {"silent_group_ids": ["10001"]},
            "permissions": {"plugin_admin_ids": ["99999"]},
        }
    )
    service = ConfigService(source)

    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="learnings",
            group_ids=["10001", "10002", "10001"],
            expected_revision=service.revision,
        )
    )
    assert result["learning_group_ids"] == ["10001", "10002"]
    assert result["reply_group_ids"] == ["10001", "10002"]
    assert result["silent_group_ids"] == []

    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="tag",
            group_ids=["10001", "10002"],
            tag="friends",
            expected_revision=service.revision,
        )
    )
    assert result["group_tags"] == [
        {"group_id": "10001", "tags": ["friends"]},
        {"group_id": "10002", "tags": ["friends"]},
    ]

    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="subadmin",
            group_ids=["10001"],
            sub_admins={"10001": ["12345", "23456"]},
            expected_revision=service.revision,
        )
    )
    assert result["group_sub_admins"] == [
        {"group_id": "10001", "admin_ids": ["12345", "23456"]}
    ]
    assert source["permissions"]["plugin_admin_ids"] == ["99999"]

    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="unmerge",
            group_ids=["10002"],
            expected_revision=service.revision,
        )
    )
    assert result["excluded_group_ids"] == ["10002"]

    result = asyncio.run(
        service.update_cross_group_settings(
            action="remove",
            category="globe",
            group_ids=["10001", "10002"],
            expected_revision=service.revision,
        )
    )
    assert result["local_only_group_ids"] == ["10001", "10002"]
    assert result["global_group_ids"] == []
    assert source["library"]["mode"] == "group"

    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="globe",
            group_ids=["10002"],
            expected_revision=service.revision,
        )
    )
    assert result["local_only_group_ids"] == ["10001"]
    assert result["global_group_ids"] == ["10002"]
    assert source.saves == 6


def test_cross_group_settings_accepts_astrbot_config_wrapper_with_lock():
    class Source(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._internal_lock = Lock()

        async def save_config_async(self):
            return True

    source = Source({"permissions": {"plugin_admin_ids": ["99999"]}})
    service = ConfigService(source)

    result = asyncio.run(
        service.update_cross_group_settings(
            action="add",
            category="learning",
            group_ids=["399375745"],
            expected_revision=service.revision,
        )
    )

    assert result["learning_group_ids"] == ["399375745"]
    assert source["learning"]["group_ids"] == ["399375745"]


def test_cross_group_reply_probability_override_and_reset():
    class Source(dict):
        async def save_config_async(self):
            return True

    service = ConfigService(Source({"reply": {"probability_percent": 50.0}}))
    result = asyncio.run(
        service.update_cross_group_settings(
            action="set",
            category="reply_probability",
            group_ids=["10001", "10002"],
            tag="25",
            expected_revision=service.revision,
        )
    )

    assert service.reply_settings("10001")["probability_percent"] == 25.0
    assert service.reply_settings("10003")["probability_percent"] == 50.0
    assert result["group_reply_probabilities"] == [
        {"group_id": "10001", "probability_percent": 25.0},
        {"group_id": "10002", "probability_percent": 25.0},
    ]

    asyncio.run(
        service.update_cross_group_settings(
            action="remove",
            category="reply_probability",
            group_ids=["10001"],
            expected_revision=service.revision,
        )
    )
    assert service.reply_settings("10001")["probability_percent"] == 50.0


def test_global_switch_settings_persist_and_roll_back():
    class Source(dict):
        fail = False

        async def save_config_async(self):
            if self.fail:
                raise OSError("disk full")
            return True

    source = Source()
    service = ConfigService(source)
    result = asyncio.run(
        service.update_global_switch(
            capability="learning",
            enabled=True,
            expected_revision=service.revision,
        )
    )
    assert result["learning_enabled"] is True
    assert result["reply_enabled"] is False
    assert source["learning"]["enabled"] is True

    before = deepcopy(source)
    source.fail = True
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            service.update_global_switch(
                capability="reply",
                enabled=True,
                expected_revision=service.revision,
            )
        )
    assert source == before


def test_global_switch_rejects_invalid_capability_and_stale_revision():
    service = ConfigService({})
    with pytest.raises(ValueError, match="invalid_global_switch"):
        asyncio.run(
            service.update_global_switch(
                capability="silent",
                enabled=True,
                expected_revision=service.revision,
            )
        )
    with pytest.raises(ValueError, match="revision_conflict"):
        asyncio.run(
            service.update_global_switch(
                capability="learning",
                enabled=True,
                expected_revision="stale",
            )
        )


def test_cross_group_settings_remove_tag_and_roll_back_on_save_failure():
    class Source(dict):
        fail = False

        async def save_config_async(self):
            if self.fail:
                raise OSError("disk full")
            return True

    source = Source(
        {
            "library": {
                "group_tags": [
                    {"group_id": "10001", "tags": ["friends", "games"]}
                ]
            }
        }
    )
    service = ConfigService(source)
    result = asyncio.run(
        service.update_cross_group_settings(
            action="remove",
            category="tag",
            group_ids=["10001"],
            expected_revision=service.revision,
        )
    )
    assert result["group_tags"] == []

    before = deepcopy(source)
    source.fail = True
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            service.update_cross_group_settings(
                action="add",
                category="learning",
                group_ids=["10002"],
                expected_revision=service.revision,
            )
        )
    assert source == before


def test_cross_group_globe_update_rolls_back_on_save_failure():
    class Source(dict):
        async def save_config_async(self):
            raise OSError("disk full")

    source = Source({"library": {"local_only_group_ids": ["10001"]}})
    service = ConfigService(source)
    before = deepcopy(source)

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            service.update_cross_group_settings(
                action="add",
                category="globe",
                group_ids=["10001"],
                expected_revision=service.revision,
            )
        )

    assert source == before


def test_cross_group_share_update_rolls_back_on_save_failure():
    class Source(dict):
        async def save_config_async(self):
            raise OSError("disk full")

    source = Source(
        {
            "library": {
                "share_groups": [
                    {"name": "联动词库1", "group_ids": ["10001"]}
                ]
            }
        }
    )
    service = ConfigService(source)
    before = deepcopy(source)

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            service.update_cross_group_settings(
                action="add",
                category="share",
                group_ids=["10002"],
                tag="联动词库1",
                expected_revision=service.revision,
            )
        )

    assert source == before


def test_cross_group_settings_validate_ids_and_revision():
    service = ConfigService({})
    with pytest.raises(ValueError, match="invalid_cross_group_settings"):
        asyncio.run(
            service.update_cross_group_settings(
                action="add",
                category="learning",
                group_ids=["bad"],
                expected_revision=service.revision,
            )
        )
    with pytest.raises(ValueError, match="revision_conflict"):
        asyncio.run(
            service.update_cross_group_settings(
                action="add",
                category="learning",
                group_ids=["10001"],
                expected_revision="stale",
            )
        )


def test_permission_settings_normalize_persist_and_reject_stale_revision():
    class Source(dict):
        saves = 0

        async def save_config_async(self):
            self.saves += 1
            return True

    source = Source()
    service = ConfigService(source)
    revision = service.revision
    result = asyncio.run(
        service.update_permission_settings(
            values={
                "plugin_admin_ids": ["12345", "12345", "67890"],
                "group_sub_admins": [
                    {
                        "group_id": "10001",
                        "admin_ids": ["23456", "23456", "34567"],
                    },
                    {"group_id": "10002", "admin_ids": []},
                ],
            },
            expected_revision=revision,
        )
    )

    assert result["plugin_admin_ids"] == ["12345", "67890"]
    assert result["group_sub_admins"] == [
        {"group_id": "10001", "admin_ids": ["23456", "34567"]}
    ]
    assert source.saves == 1
    with pytest.raises(ValueError, match="revision_conflict"):
        asyncio.run(
            service.update_permission_settings(
                values={"plugin_admin_ids": [], "group_sub_admins": []},
                expected_revision=revision,
            )
        )


def test_permission_settings_validate_ids_counts_and_duplicate_groups():
    service = ConfigService({})

    for values in (
        {"plugin_admin_ids": ["bad"], "group_sub_admins": []},
        {
            "plugin_admin_ids": [],
            "group_sub_admins": [
                {"group_id": "10001", "admin_ids": ["12345"]},
                {"group_id": "10001", "admin_ids": ["23456"]},
            ],
        },
        {"plugin_admin_ids": [str(10000 + index) for index in range(101)], "group_sub_admins": []},
    ):
        with pytest.raises(ValueError, match="invalid_permissions"):
            service._validated_permission_update(values)


def test_permission_settings_roll_back_on_save_failure():
    class Source(dict):
        async def save_config_async(self):
            raise OSError("disk full")

    source = Source({"permissions": {"plugin_admin_ids": ["12345"], "group_sub_admins": []}})
    service = ConfigService(source)
    before = dict(source)
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            service.update_permission_settings(
                values={"plugin_admin_ids": ["67890"], "group_sub_admins": []},
                expected_revision=service.revision,
            )
        )
    assert source == before


def test_tts_settings_persist_validate_local_endpoint_and_reject_stale_revision():
    class Source(dict):
        async def save_config_async(self):
            return True

    source = Source()
    service = ConfigService(source)
    revision = service.revision
    result = asyncio.run(
        service.update_tts_settings(
            values={
                "enabled": True,
                "driver": "local_http",
                "probability_percent": 35,
                "max_text_length": 120,
                "voice": "local-voice",
                "endpoint_url": "http://127.0.0.1:9000/tts",
                "timeout_seconds": 8,
            },
            expected_revision=revision,
        )
    )

    assert result["probability_percent"] == 35
    assert source["tts"]["endpoint_url"] == "http://127.0.0.1:9000/tts"
    with pytest.raises(ValueError, match="revision_conflict"):
        asyncio.run(
            service.update_tts_settings(
                values=source["tts"], expected_revision=revision
            )
        )
    with pytest.raises(ValueError, match="tts_endpoint_must_be_loopback"):
        service._validated_tts_update(
            {
                "enabled": True,
                "driver": "local_http",
                "probability_percent": 10,
                "endpoint_url": "http://192.168.1.2:9000/tts",
            }
        )


def test_tts_settings_accept_cloud_driver_and_roll_back_on_save_failure():
    service = ConfigService({})
    result = service._validated_tts_update(
        {"enabled": False, "driver": "openai", "probability_percent": 0}
    )
    assert result["driver"] == "openai"

    class Source(dict):
        async def save_config_async(self):
            raise OSError("disk full")

    source = Source({"tts": {"enabled": False, "driver": "windows"}})
    service = ConfigService(source)
    before = dict(source)
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            service.update_tts_settings(
                values={"enabled": False, "driver": "windows"},
                expected_revision=service.revision,
            )
        )
    assert source == before
