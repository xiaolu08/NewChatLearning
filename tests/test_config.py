from new_chat_learning.infrastructure.config import ConfigService


def test_config_merges_defaults_and_has_stable_revision():
    service = ConfigService({"storage": {"media_quota_gb": 5.0}})

    snapshot = service.snapshot()

    assert snapshot["storage"]["media_quota_gb"] == 5.0
    assert snapshot["tts"]["enabled"] is False
    assert service.revision == service.revision
