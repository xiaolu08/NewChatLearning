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
