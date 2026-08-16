from new_chat_learning.domain.reply_policy import (
    classify_trigger_components,
    normalize_trigger_type,
)


def test_trigger_type_aliases_are_normalized() -> None:
    assert normalize_trigger_type("Plain") == "text"
    assert normalize_trigger_type("表情包") == "marketface"
    assert normalize_trigger_type("music_share") == "music"
    assert normalize_trigger_type("unknown-value") is None


def test_trigger_components_ignore_routing_and_detect_mixed_messages() -> None:
    assert classify_trigger_components(
        (
            {"type": "Reply", "data": {"id": "1"}},
            {"type": "At", "data": {"qq": "123"}},
            {"type": "Plain", "data": {"text": "你好"}},
        )
    ) == "text"
    assert classify_trigger_components(
        (
            {"type": "Plain", "data": {"text": "看图"}},
            {"type": "Image", "data": {"file": "a.jpg"}},
        )
    ) == "mixed"
    assert classify_trigger_components(
        ({"type": "MarketFace", "data": {"emoji_id": "1"}},)
    ) == "marketface"
