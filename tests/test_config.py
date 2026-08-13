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
