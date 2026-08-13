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
