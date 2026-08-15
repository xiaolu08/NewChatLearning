import asyncio
import sys
import types

from new_chat_learning.platform.napcat.actions import send_group_message_with_id


class Plain:
    pass


class Image:
    pass


class Chain:
    def __init__(self, components=None):
        self.chain = list(components or [])


class Bot:
    def __init__(self):
        self.sent = []

    async def send_group_msg(self, **kwargs):
        self.sent.append(kwargs)
        return {"data": {"message_id": 88}}


class FailingMediaBot(Bot):
    async def send_group_msg(self, **kwargs):
        self.sent.append(kwargs)
        if len(self.sent) == 1:
            raise RuntimeError("download failed: Bad Request")
        return {"data": {"message_id": 89}}


class Event:
    parser_func = None

    def __init__(self, parser):
        self.bot = Bot()
        Event.parser_func = parser
        self.sent = []

    @staticmethod
    async def _parse_onebot_json(chain):
        return await Event.parser_func(chain)

    def get_group_id(self):
        return "10001"

    def get_self_id(self):
        return "9"


class FailingMediaEvent(Event):
    def __init__(self, parser):
        super().__init__(parser)
        self.bot = FailingMediaBot()


def _install_astrbot_event(monkeypatch):
    class AstrMessageEvent:
        @staticmethod
        async def send(event, chain):
            event.sent.append(chain)

    event_module = types.ModuleType("astrbot.api.event")
    event_module.AstrMessageEvent = AstrMessageEvent
    api_module = types.ModuleType("astrbot.api")
    api_module.event = event_module
    astrbot_module = types.ModuleType("astrbot")
    astrbot_module.api = api_module
    monkeypatch.setitem(sys.modules, "astrbot", astrbot_module)
    monkeypatch.setitem(sys.modules, "astrbot.api", api_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)


def test_media_parser_failure_retries_text_only(monkeypatch):
    _install_astrbot_event(monkeypatch)

    async def parser(chain):
        if any(isinstance(component, Image) for component in chain.chain):
            raise FileNotFoundError("expired image")
        return [{"type": "text", "data": {"text": "fallback"}}]

    event = Event(parser)
    message_id = asyncio.run(send_group_message_with_id(event, Chain([Plain(), Image()])))

    assert message_id == "88"
    assert event.bot.sent[0]["message"] == [
        {"type": "text", "data": {"text": "fallback"}}
    ]
    assert len(event.sent) == 1


def test_media_only_parser_failure_is_silent():
    async def parser(_chain):
        raise FileNotFoundError("expired image")

    event = Event(parser)

    result = asyncio.run(send_group_message_with_id(event, Chain([Image()])))

    assert result is None
    assert event.bot.sent == []


def test_send_failure_retries_without_media(monkeypatch):
    _install_astrbot_event(monkeypatch)

    async def parser(chain):
        return [{"type": "text", "data": {"text": "fallback"}}]

    event = FailingMediaEvent(parser)
    message_id = asyncio.run(send_group_message_with_id(event, Chain([Plain(), Image()])))

    assert message_id == "89"
    assert len(event.bot.sent) == 2
    assert event.bot.sent[1]["message"] == [
        {"type": "text", "data": {"text": "fallback"}}
    ]


def test_media_only_send_failure_is_silent(monkeypatch):
    _install_astrbot_event(monkeypatch)

    async def parser(chain):
        return [{"type": "image", "data": {"file": "stale"}}]

    event = FailingMediaEvent(parser)
    result = asyncio.run(send_group_message_with_id(event, Chain([Image()])))

    assert result is None
    assert len(event.bot.sent) == 1
