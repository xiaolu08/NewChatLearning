import asyncio

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
    before = dict(source)
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            service.update_filter_settings(
                values={"enabled": True, "contains": ["new"], "group_rules": []},
                expected_revision=service.revision,
            )
        )
    assert source == before


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
