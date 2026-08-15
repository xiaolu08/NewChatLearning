import asyncio
from dataclasses import dataclass

from new_chat_learning.platform.napcat.normalizer import (
    enrich_long_tail_components,
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


class Bot:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def call_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return self.payload


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


def test_resolves_image_file_id_through_napcat_cache(tmp_path):
    cached = tmp_path / "cached-image.jpg"
    cached.write_bytes(b"image")
    event = Event(
        {
            "post_type": "message",
            "message": [
                {
                    "type": "image",
                    "data": {
                        "file": "09AD9B554FB83AB4CDAFEA9135AC309B.jpeg",
                        "url": "https://gchat.qpic.cn/download?expired=1",
                    },
                }
            ],
        },
        [
            Image(
                "09AD9B554FB83AB4CDAFEA9135AC309B.jpeg",
                "https://gchat.qpic.cn/download?expired=1",
            )
        ],
    )
    event.bot = Bot({"status": "ok", "data": {"file": str(cached)}})

    message = normalize_group_message(event)
    enriched = asyncio.run(enrich_long_tail_components(event, message))

    assert enriched.components[0]["data"]["path"] == str(cached)
    assert event.bot.calls == [
        ("get_image", {"file": "09AD9B554FB83AB4CDAFEA9135AC309B.jpeg"})
    ]
    assert str(cached) not in repr(enriched.matching_components)
    assert "gchat.qpic.cn" not in repr(enriched.matching_components)


def test_uses_raw_onebot_media_when_astrbot_drops_component():
    event = Event(
        {
            "post_type": "message",
            "message": [
                {
                    "type": "image",
                    "data": {"file": "image-id", "url": "https://gchat.qpic.cn/download?expired=1"},
                }
            ],
        },
        [],
    )

    message = normalize_group_message(event)

    assert message is not None
    assert message.components[0]["type"] == "Image"
    assert message.components[0]["data"]["file"] == "image-id"


def test_resolves_video_through_get_video(tmp_path):
    cached = tmp_path / "cached-video.mp4"
    cached.write_bytes(b"video")
    event = Event(
        {
            "post_type": "message",
            "message": [{"type": "video", "data": {"file": "video-id"}}],
        },
        [],
    )
    event.bot = Bot({"status": "ok", "data": {"file": str(cached)}})

    message = normalize_group_message(event)
    enriched = asyncio.run(enrich_long_tail_components(event, message))

    assert enriched.components[0]["data"]["path"] == str(cached)
    assert event.bot.calls[0] == ("get_video", {"file": "video-id"})


def test_image_lookup_failure_keeps_original_component():
    class FailingBot:
        async def call_action(self, _action, **_kwargs):
            raise RuntimeError("cache miss")

    event = Event(
        {"post_type": "message"},
        [Image("image-id", "https://gchat.qpic.cn/download?expired=1")],
    )
    event.bot = FailingBot()
    message = normalize_group_message(event)

    enriched = asyncio.run(enrich_long_tail_components(event, message))

    assert enriched is message
    assert enriched.components[0]["data"]["file"] == "image-id"


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


def test_enriches_market_face_and_xml_from_raw_segments():
    event = Event(
        {
            "post_type": "message",
            "message": [
                {
                    "type": "mface",
                    "data": {
                        "emoji_id": "e1",
                        "emoji_package_id": "p1",
                        "key": "temporary-key",
                        "summary": "开心",
                        "url": "https://example.test/mface.png",
                    },
                },
                {"type": "xml", "data": {"data": "<msg>card</msg>\x00"}},
            ],
        },
        [Plain("fallback")],
    )
    message = normalize_group_message(event)

    enriched = asyncio.run(enrich_long_tail_components(event, message))

    assert [item["type"] for item in enriched.components] == [
        "Plain",
        "MarketFace",
        "Xml",
    ]
    assert enriched.components[1]["data"]["summary"] == "开心"
    assert enriched.components[2]["data"]["data"] == "<msg>card</msg>"
    assert "temporary-key" not in repr(enriched.matching_components)
    assert "https://example.test" not in repr(enriched.matching_components)


def test_resolves_forward_nodes_and_nested_components():
    event = Event(
        {
            "post_type": "message",
            "message": [{"type": "forward", "data": {"id": "forward-1"}}],
        },
        [
            type(
                "Forward",
                (),
                {"type": "Forward", "dict": lambda self: {"type": "Forward", "id": "forward-1"}},
            )()
        ],
    )
    event.bot = Bot(
        {
            "data": {
                "messages": [
                    {
                        "sender": {"user_id": 42, "nickname": "群友"},
                        "message": [
                            {"type": "text", "data": {"text": "转发内容"}},
                            {"type": "image", "data": {"url": "https://example.test/a.png"}},
                        ],
                    }
                ]
            }
        }
    )
    message = normalize_group_message(event)

    enriched = asyncio.run(enrich_long_tail_components(event, message))

    assert enriched.components[0]["type"] == "Nodes"
    node = enriched.components[0]["data"]["nodes"][0]
    assert node["uin"] == "42"
    assert node["name"] == "群友"
    assert [item["type"] for item in node["content"]] == ["Plain", "Image"]
    assert event.bot.calls[0] == ("get_forward_msg", {"message_id": "forward-1"})
    assert "https://example.test" not in repr(enriched.matching_components)
