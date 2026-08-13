from new_chat_learning.commands.group_settings import (
    GroupSettingsCommand,
    parse_legacy_group_command,
    parse_on_off,
    transition_toggle,
)


def test_parse_legacy_group_commands():
    assert parse_legacy_group_command("!learning on") == GroupSettingsCommand(
        "learning", ("on",)
    )
    assert parse_legacy_group_command(" !REPLY OFF ") == GroupSettingsCommand(
        "reply", ("off",)
    )
    assert parse_legacy_group_command("!grouplist") == GroupSettingsCommand("mode")
    assert parse_legacy_group_command("!add learnings") == GroupSettingsCommand(
        "learning", ("on",)
    )
    assert parse_legacy_group_command("!remove reply") == GroupSettingsCommand(
        "reply", ("off",)
    )


def test_legacy_parser_does_not_intercept_unrelated_or_cross_group_forms():
    assert parse_legacy_group_command("hello !learning on") is None
    assert parse_legacy_group_command("!grouplist extra") is None
    assert parse_legacy_group_command("!unknown") is None


def test_toggle_transitions_preserve_unrelated_capability():
    assert transition_toggle("reply", "learning", True) == "learning_reply"
    assert transition_toggle("learning_reply", "learning", False) == "reply"
    assert transition_toggle("learning", "reply", True) == "learning_reply"
    assert transition_toggle("learning_reply", "reply", False) == "learning"
    assert transition_toggle("reply", "silent", True) == "silent"
    assert transition_toggle("silent", "silent", False) == "learning"
    assert transition_toggle("silent", "reply", True) == "learning_reply"


def test_parse_on_off_is_strict():
    assert parse_on_off(("ON",)) is True
    assert parse_on_off(("off",)) is False
    assert parse_on_off(()) is None
    assert parse_on_off(("yes",)) is None
