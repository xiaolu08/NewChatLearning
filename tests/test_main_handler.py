import asyncio
import importlib
import sys
import types
from types import SimpleNamespace

from new_chat_learning.domain.reply import ReplyDecision


def load_main(monkeypatch):
    class Star:
        pass

    class CommandGroup:
        def __call__(self, function):
            function.command = lambda _name: lambda command: command
            return function

    class Filter:
        class PlatformAdapterType:
            AIOCQHTTP = "aiocqhttp"

        class EventMessageType:
            GROUP_MESSAGE = "group"

        platform_adapter_type = staticmethod(lambda _kind: lambda function: function)
        event_message_type = staticmethod(lambda _kind, **_kwargs: lambda function: function)
        command_group = staticmethod(lambda _name: CommandGroup())

    star_module = types.ModuleType("astrbot.api.star")
    star_module.Star = Star
    star_module.Context = object
    star_module.StarTools = SimpleNamespace(get_data_dir=lambda _name: None)
    event_module = types.ModuleType("astrbot.api.event")
    event_module.AstrMessageEvent = object
    event_module.MessageEventResult = object
    event_module.filter = Filter
    web_module = types.ModuleType("astrbot.api.web")
    web_module.json_response = lambda value, **_kwargs: value
    api_module = types.ModuleType("astrbot.api")
    api_module.star = star_module
    astrbot_module = types.ModuleType("astrbot")
    astrbot_module.api = api_module
    renderer_module = types.ModuleType("new_chat_learning.platform.astrbot.renderer")
    renderer_module.render_message_chain = lambda *_args, **_kwargs: None

    monkeypatch.setitem(sys.modules, "astrbot", astrbot_module)
    monkeypatch.setitem(sys.modules, "astrbot.api", api_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.web", web_module)
    monkeypatch.setitem(
        sys.modules,
        "new_chat_learning.platform.astrbot.renderer",
        renderer_module,
    )
    sys.modules.pop("main", None)
    return importlib.import_module("main")


class Config:
    def learning_enabled_for(self, _group_id):
        return False

    def reply_enabled_for(self, _group_id):
        return True

    def reply_settings(self):
        return {"max_plain_length": 100}


class Reply:
    def __init__(self, decision):
        self.decision = decision
        self.marked = []
        self.mentioned_bot = None

    async def decide(self, _group_id, _key, *, mentioned_bot=False):
        self.mentioned_bot = mentioned_bot
        return self.decision

    def mark_sent(self, group_id):
        self.marked.append(group_id)


class History:
    def __init__(self):
        self.calls = []

    async def insert_message_chain(self, **kwargs):
        self.calls.append(kwargs)


class Event:
    unified_msg_origin = "aiocqhttp:group:10001"

    def __init__(self):
        self.sent = []
        self.stopped = False

    def get_group_id(self):
        return "10001"

    def get_self_id(self):
        return "9"

    def get_platform_id(self):
        return "aiocqhttp"

    async def send(self, chain):
        self.sent.append(chain)

    def stop_event(self):
        self.stopped = True


def plugin_with(main_module, decision):
    plugin = object.__new__(main_module.NewChatLearningPlugin)
    reply = Reply(decision)
    plugin.app = SimpleNamespace(config=Config(), reply=reply)
    history = History()
    plugin.context = SimpleNamespace(message_history_manager=history)
    plugin.logger = SimpleNamespace(exception=lambda *_args: None)
    return plugin, reply, history


def test_successful_local_reply_stops_llm_and_persists_history(monkeypatch):
    main_module = load_main(monkeypatch)
    candidate = SimpleNamespace(
        components=(
            {"type": "At", "data": {"qq": "all"}},
            {"type": "At", "data": {"qq": "9"}},
            {"type": "Plain", "data": {"text": "hello"}},
        )
    )
    plugin, reply, history = plugin_with(main_module, ReplyDecision(candidate, "exact"))
    event = Event()
    chain = SimpleNamespace(chain=["hello"])
    monkeypatch.setattr(main_module, "parse_recall_notice", lambda _event: None)
    monkeypatch.setattr(main_module, "normalize_group_message", lambda _event: candidate)
    monkeypatch.setattr(main_module, "reply_matching_key", lambda *_args: "key")
    monkeypatch.setattr(main_module, "render_message_chain", lambda *_args, **_kwargs: chain)

    asyncio.run(plugin.capture_group_message(event))

    assert event.sent == [chain]
    assert reply.marked == ["10001"]
    assert reply.mentioned_bot is True
    assert len(history.calls) == 1
    assert history.calls[0]["role"] == "bot"
    assert event.stopped is True


def test_render_failure_leaves_llm_flow_untouched(monkeypatch):
    main_module = load_main(monkeypatch)
    candidate = SimpleNamespace(components=({"type": "Plain", "data": {"text": "hello"}},))
    plugin, reply, history = plugin_with(main_module, ReplyDecision(candidate, "exact"))
    event = Event()
    monkeypatch.setattr(main_module, "parse_recall_notice", lambda _event: None)
    monkeypatch.setattr(main_module, "normalize_group_message", lambda _event: candidate)
    monkeypatch.setattr(main_module, "reply_matching_key", lambda *_args: "key")
    monkeypatch.setattr(main_module, "render_message_chain", lambda *_args, **_kwargs: None)

    asyncio.run(plugin.capture_group_message(event))

    assert event.sent == []
    assert reply.marked == []
    assert history.calls == []
    assert event.stopped is False
