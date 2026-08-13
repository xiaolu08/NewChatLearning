from dataclasses import dataclass

from new_chat_learning.platform.napcat.normalizer import (
    normalize_group_message,
    parse_recall_notice,
    reply_matching_key,
)


@dataclass
class Plain:
    text: str
    type: str = "Plain"

    def dict(self):
        return {"type": self.type, "text": self.text}


@dataclass
class Image:
    file: str
    url: str
    type: str = "Image"

    def dict(self):
        return {"type": self.type, "file": self.file, "url": self.url}


class MessageObject:
    def __init__(self, raw, message_id="88", timestamp=1000):
        self.raw_message = raw
        self.message_id = message_id
        self.timestamp = timestamp


class Event:
    def __init__(self, raw, components=None, text="hello", sender="42", self_id="9"):
        self.message_obj = MessageObject(raw)
        self.components = components or []
        self.text = text
        self.sender = sender
        self.self_id = self_id

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return "10001"

    def get_sender_id(self):
        return self.sender

    def get_self_id(self):
        return self.self_id

    def get_message_str(self):
        return self.text

    def get_messages(self):
        return self.components


def test_normalizes_components_and_removes_transient_matching_fields():
    event = Event(
        {"post_type": "message", "message_id": 88, "time": 1000},
        [Plain("hello"), Image("image-id", "https://temporary.example/image")],
    )

    result = normalize_group_message(event)

    assert result is not None
    assert result.components[1]["data"]["url"].startswith("https://")
    assert "url" not in result.matching_components[1]["data"]
    assert result.normalized_key


def test_excludes_commands_and_own_messages():
    command = Event({"post_type": "message"}, [Plain("/ncl status")], text="/ncl status")
    astrbot_command = Event({"post_type": "message"}, [Plain("/help")], text="/help")
    own = Event({"post_type": "message"}, [Plain("hello")], sender="9", self_id="9")

    assert normalize_group_message(command) is None
    assert normalize_group_message(astrbot_command) is None
    assert normalize_group_message(own) is None


def test_excludes_command_after_astrbot_strips_wake_prefix():
    event = Event(
        {
            "post_type": "message",
            "raw_message": "/help",
            "message": [{"type": "text", "data": {"text": "/help"}}],
        },
        [Plain("help")],
        text="help",
    )

    assert normalize_group_message(event) is None


def test_parses_group_recall_notice():
    event = Event(
        {"post_type": "notice", "notice_type": "group_recall", "group_id": 10001, "message_id": 88}
    )

    result = parse_recall_notice(event)

    assert result is not None
    assert result.group_id == "10001"
    assert result.message_id == "88"


def test_reply_key_ignores_bot_mention_and_quote():
    event = Event(
        {"post_type": "message"},
        [
            Plain(" 你好 "),
            type(
                "At",
                (),
                {
                    "type": "At",
                    "dict": lambda self: {"type": "At", "qq": "9", "name": "bot"},
                },
            )(),
            type(
                "Reply",
                (),
                {
                    "type": "Reply",
                    "dict": lambda self: {"type": "Reply", "id": "1"},
                },
            )(),
        ],
    )
    plain_event = Event({"post_type": "message"}, [Plain("你好")])

    message = normalize_group_message(event)
    plain = normalize_group_message(plain_event)

    assert message is not None and plain is not None
    assert reply_matching_key(message, "9") == plain.normalized_key
