from new_chat_learning.commands.group_settings import (
    CrossGroupCommand,
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
    assert parse_legacy_group_command("!grouplist") == CrossGroupCommand("list")
    assert parse_legacy_group_command("!sharelist") == CrossGroupCommand("share_list")
    assert parse_legacy_group_command("!add learnings 10001 10002") == CrossGroupCommand(
        "add", "learnings", ("10001", "10002")
    )
    assert parse_legacy_group_command("!remove reply 10001") == CrossGroupCommand(
        "remove", "reply", ("10001",)
    )
    assert parse_legacy_group_command("!add tag Friends 10001 10002") == CrossGroupCommand(
        "add", "tag", ("10001", "10002"), "Friends"
    )
    assert parse_legacy_group_command("!remove tag 10001") == CrossGroupCommand(
        "remove", "tag", ("10001",)
    )
    assert parse_legacy_group_command("!add subadmin 10001") == CrossGroupCommand(
        "add", "subadmin", ("10001",)
    )
    assert parse_legacy_group_command("!remove unmerge 10001") == CrossGroupCommand(
        "remove", "unmerge", ("10001",)
    )
    assert parse_legacy_group_command("!add globe 10001 10002") == CrossGroupCommand(
        "add", "globe", ("10001", "10002")
    )
    assert parse_legacy_group_command("!remove globe 10001") == CrossGroupCommand(
        "remove", "globe", ("10001",)
    )
    assert parse_legacy_group_command(
        "!add share 123456789 987654321 联动词库1"
    ) == CrossGroupCommand(
        "add", "share", ("123456789", "987654321"), "联动词库1"
    )
    assert parse_legacy_group_command(
        '!remove share 123456789 "联动 词库2"'
    ) == CrossGroupCommand(
        "remove", "share", ("123456789",), "联动 词库2"
    )
    assert parse_legacy_group_command("!reply -s 25 10001 10002") == CrossGroupCommand(
        "set", "reply_probability", ("10001", "10002"), "25"
    )
    assert parse_legacy_group_command("!reply -d 10001") == CrossGroupCommand(
        "remove", "reply_probability", ("10001",)
    )
    assert parse_legacy_group_command(
        "!reply -s xml 12.5 10001 10002"
    ) == CrossGroupCommand(
        "set",
        "reply_type_probability",
        ("10001", "10002"),
        "12.5",
        "xml",
    )
    assert parse_legacy_group_command("!reply -d 表情包 10001") == CrossGroupCommand(
        "remove",
        "reply_type_probability",
        ("10001",),
        message_type="marketface",
    )
    assert parse_legacy_group_command("！reply -s text 20 10001") == CrossGroupCommand(
        "set",
        "reply_type_probability",
        ("10001",),
        "20",
        "text",
    )


def test_legacy_parser_does_not_intercept_unrelated_forms():
    assert parse_legacy_group_command("hello !learning on") is None
    assert parse_legacy_group_command("!grouplist extra") is None
    assert parse_legacy_group_command("!sharelist extra") is None
    assert parse_legacy_group_command("!unknown") is None
    assert parse_legacy_group_command("!globe") is None
    assert parse_legacy_group_command("!globe on") is None
    assert parse_legacy_group_command("!add share 10001") == CrossGroupCommand(
        "add", "share"
    )


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
