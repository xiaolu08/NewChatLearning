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

    class MessageEventResult:
        def __init__(self):
            self.text = None

        def message(self, text):
            self.text = text
            return self

    star_module = types.ModuleType("astrbot.api.star")
    star_module.Star = Star
    star_module.Context = object
    star_module.StarTools = SimpleNamespace(get_data_dir=lambda _name: None)
    event_module = types.ModuleType("astrbot.api.event")
    event_module.AstrMessageEvent = object
    event_module.MessageEventResult = MessageEventResult
    event_module.MessageChain = object
    event_module.filter = Filter
    web_module = types.ModuleType("astrbot.api.web")
    web_module.PluginUploadFile = type("PluginUploadFile", (), {})
    web_module.json_response = lambda value, **_kwargs: value
    web_module.file_response = lambda path, **kwargs: {"path": path, **kwargs}
    web_module.request = SimpleNamespace(
        client_host="127.0.0.1",
        cookies={},
        body=lambda: None,
    )
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

    async def decide(self, _group_id, _key, *, plain_text="", mentioned_bot=False):
        self.plain_text = plain_text
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
        self.sender_id = "7"
        self.message_obj = SimpleNamespace(message_id="600")
        self.result = None
        self.message_str = ""

    def get_group_id(self):
        return "10001"

    def get_self_id(self):
        return "9"

    def get_sender_id(self):
        return self.sender_id

    def get_platform_id(self):
        return "aiocqhttp"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_message_str(self):
        return self.message_str

    async def send(self, chain):
        self.sent.append(chain)

    def stop_event(self):
        self.stopped = True

    def is_admin(self):
        return False

    def set_result(self, result):
        self.result = result

    def plain_result(self, text):
        return text


def plugin_with(main_module, decision):
    plugin = object.__new__(main_module.NewChatLearningPlugin)
    reply = Reply(decision)
    plugin.app = SimpleNamespace(config=Config(), reply=reply, data_dir=None)
    history = History()
    plugin.context = SimpleNamespace(message_history_manager=history)
    plugin.logger = SimpleNamespace(exception=lambda *_args: None)
    return plugin, reply, history


def test_successful_local_reply_stops_llm_and_persists_history(monkeypatch):
    main_module = load_main(monkeypatch)
    candidate = SimpleNamespace(
        plain_text="hello",
        components=(
            {"type": "At", "data": {"qq": "all"}},
            {"type": "At", "data": {"qq": "9"}},
            {"type": "Plain", "data": {"text": "hello"}},
        ),
    )
    plugin, reply, history = plugin_with(main_module, ReplyDecision(candidate, "exact"))
    event = Event()
    chain = SimpleNamespace(chain=["hello"])
    monkeypatch.setattr(main_module, "parse_recall_notice", lambda _event: None)
    monkeypatch.setattr(main_module, "normalize_group_message", lambda _event: candidate)
    monkeypatch.setattr(main_module, "reply_matching_key", lambda *_args: "key")
    monkeypatch.setattr(main_module, "render_message_chain", lambda *_args, **_kwargs: chain)
    monkeypatch.setattr(main_module, "send_group_message_with_id", _send_without_id)

    asyncio.run(plugin.capture_group_message(event))

    assert event.sent == [chain]
    assert reply.marked == ["10001"]
    assert reply.mentioned_bot is True
    assert len(history.calls) == 1
    assert history.calls[0]["role"] == "bot"
    assert event.stopped is True


def test_message_reply_records_privacy_safe_diagnostics(monkeypatch):
    main_module = load_main(monkeypatch)
    candidate = SimpleNamespace(
        answer_id=1,
        question_id=2,
        plain_text="hello",
        components=({"type": "Plain", "data": {"text": "hello"}},),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(candidate, "exact"))

    class Diagnostics:
        def __init__(self):
            self.events = []

        def record(self, group_id, event, *, reason=""):
            self.events.append((group_id, event, reason))

    class Store:
        async def register_reply(self, **_kwargs):
            return None

    plugin.app.diagnostics = Diagnostics()
    plugin.app.store = Store()
    event = Event()
    chain = SimpleNamespace(chain=["hello"])
    monkeypatch.setattr(main_module, "parse_recall_notice", lambda _event: None)
    monkeypatch.setattr(main_module, "normalize_group_message", lambda _event: candidate)
    monkeypatch.setattr(main_module, "reply_matching_key", lambda *_args: "private-key")
    monkeypatch.setattr(main_module, "render_message_chain", lambda *_args, **_kwargs: chain)
    monkeypatch.setattr(main_module, "send_group_message_with_id", _send_without_id)

    asyncio.run(plugin.capture_group_message(event))

    assert plugin.app.diagnostics.events == [
        ("10001", "normalized_messages", ""),
        ("10001", "reply_decisions", "exact"),
        ("10001", "successful_sends", ""),
    ]
    assert "private-key" not in repr(plugin.app.diagnostics.events)


def test_successful_tts_reply_sends_voice_but_persists_text_history(monkeypatch, tmp_path):
    main_module = load_main(monkeypatch)
    candidate = SimpleNamespace(
        answer_id=1,
        question_id=2,
        plain_text="hello",
        components=({"type": "Plain", "data": {"text": "hello"}},),
    )
    plugin, reply, history = plugin_with(main_module, ReplyDecision(candidate, "exact"))
    audio_path = tmp_path / "reply.wav"
    audio_path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEdata")

    class TTS:
        async def synthesize_reply(self, components):
            assert components == candidate.components
            return audio_path

    class Store:
        async def register_reply(self, **_kwargs):
            return None

    plugin.app.tts = TTS()
    plugin.app.store = Store()
    event = Event()
    text_chain = SimpleNamespace(chain=["text"])
    voice_chain = SimpleNamespace(chain=["voice"])
    rendered = []

    def render(components, **_kwargs):
        rendered.append(components)
        return text_chain if len(rendered) == 1 else voice_chain

    async def send(_event, chain):
        assert chain is voice_chain
        return "sent-1"

    monkeypatch.setattr(main_module, "parse_recall_notice", lambda _event: None)
    monkeypatch.setattr(main_module, "normalize_group_message", lambda _event: candidate)
    monkeypatch.setattr(main_module, "reply_matching_key", lambda *_args: "key")
    monkeypatch.setattr(main_module, "render_message_chain", render)
    monkeypatch.setattr(main_module, "send_group_message_with_id", send)

    asyncio.run(plugin.capture_group_message(event))

    assert rendered[1][0]["type"] == "Record"
    assert history.calls[0]["message_chain"] is text_chain
    assert reply.marked == ["10001"]
    assert event.stopped is True


def test_tts_unavailable_falls_back_to_text_reply(monkeypatch):
    main_module = load_main(monkeypatch)
    candidate = SimpleNamespace(
        plain_text="hello",
        components=({"type": "Plain", "data": {"text": "hello"}},),
    )
    plugin, _reply, history = plugin_with(main_module, ReplyDecision(candidate, "exact"))

    class TTS:
        async def synthesize_reply(self, _components):
            return None

    plugin.app.tts = TTS()
    event = Event()
    text_chain = SimpleNamespace(chain=["text"])
    monkeypatch.setattr(main_module, "parse_recall_notice", lambda _event: None)
    monkeypatch.setattr(main_module, "normalize_group_message", lambda _event: candidate)
    monkeypatch.setattr(main_module, "reply_matching_key", lambda *_args: "key")
    monkeypatch.setattr(main_module, "render_message_chain", lambda *_args, **_kwargs: text_chain)
    monkeypatch.setattr(main_module, "send_group_message_with_id", _send_without_id)

    asyncio.run(plugin.capture_group_message(event))

    assert event.sent == [text_chain]
    assert history.calls[0]["message_chain"] is text_chain


def test_render_failure_leaves_llm_flow_untouched(monkeypatch):
    main_module = load_main(monkeypatch)
    candidate = SimpleNamespace(
        plain_text="hello",
        components=({"type": "Plain", "data": {"text": "hello"}},),
    )
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


async def _send_without_id(event, chain):
    await event.send(chain)


def test_unauthorized_fast_delete_is_silent_and_stops_event(monkeypatch):
    main_module = load_main(monkeypatch)
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.config = {}
    event = Event()
    request = SimpleNamespace(quoted_message_id="501", recent_position=None)
    monkeypatch.setattr(main_module, "parse_recall_notice", lambda _event: None)
    monkeypatch.setattr(main_module, "parse_fast_delete", lambda _event: request)

    asyncio.run(plugin.capture_group_message(event))

    assert event.stopped is True
    assert event.sent == []
    assert event.result is None


def test_unauthorized_library_command_is_silent(monkeypatch):
    main_module = load_main(monkeypatch)
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.config = {}
    event = Event()
    event.message_str = "/ncl search hello"

    asyncio.run(plugin.ncl_search(event))

    assert event.stopped is True
    assert event.result is None


def test_authorized_search_formats_stable_question_ids(monkeypatch):
    main_module = load_main(monkeypatch)

    class Library:
        async def search(self, group_id, query):
            assert group_id == "10001"
            assert query == "hello"
            return [
                {
                    "question_id": 12,
                    "plain_text": "hello world",
                    "is_regex": 0,
                    "answer_count": 2,
                    "total_weight": 5,
                }
            ]

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.library = Library()
    plugin.config = {"permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}}
    event = Event()
    event.message_str = "/ncl search hello"

    asyncio.run(plugin.ncl_search(event))

    assert event.stopped is False
    assert "Q12 [文本] hello world" in event.result.text


def test_authorized_group_mode_command_persists_current_group(monkeypatch):
    main_module = load_main(monkeypatch)

    class GroupConfig(Config):
        revision = "revision-1"

        def group_settings(self, group_id):
            assert group_id == "10001"
            return {
                "group_id": group_id,
                "mode": "reply",
                "target_user_ids": [],
                "revision": "revision-1",
            }

    class App:
        def __init__(self):
            self.config = GroupConfig()
            self.calls = []

        async def update_group_settings(self, **kwargs):
            self.calls.append(kwargs)
            return {"group_id": "10001", "mode": "learning_reply", "target_user_ids": []}

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()
    plugin.config = {
        "permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}
    }
    event = Event()
    event.message_str = "/ncl learning on"

    asyncio.run(plugin.ncl_learning(event))

    assert plugin.app.calls == [
        {
            "group_id": "10001",
            "mode": "learning_reply",
            "target_user_ids": [],
            "expected_revision": "revision-1",
            "actor_id": "7",
            "source": "command",
        }
    ]
    assert "本群设置已保存" in event.result.text
    assert "学习并回复" in event.result.text


def test_target_add_requires_learning_and_unauthorized_command_is_silent(monkeypatch):
    main_module = load_main(monkeypatch)

    class GroupConfig(Config):
        revision = "revision-1"

        def group_settings(self, group_id):
            return {
                "group_id": group_id,
                "mode": "reply",
                "target_user_ids": [],
                "revision": "revision-1",
            }

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.config = GroupConfig()
    event = Event()
    event.message_str = "/ncl target add 12345"

    asyncio.run(plugin.ncl_target(event))
    assert event.stopped is True
    assert event.result is None

    plugin.config = {
        "permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}
    }
    event = Event()
    event.message_str = "/ncl target add 12345"
    asyncio.run(plugin.ncl_target(event))
    assert "请先使用 /ncl learning on" in event.result.text


def test_enabled_legacy_alias_stops_learning_and_uses_legacy_audit_source(monkeypatch):
    main_module = load_main(monkeypatch)

    class GroupConfig(Config):
        revision = "revision-1"

        def snapshot(self):
            return {"general": {"legacy_command_aliases": True}}

        def group_settings(self, group_id):
            return {
                "group_id": group_id,
                "mode": "learning",
                "target_user_ids": [],
                "revision": "revision-1",
            }

    class App:
        def __init__(self):
            self.config = GroupConfig()
            self.reply = Reply(ReplyDecision(None, "no_match"))
            self.observed = []
            self.calls = []

        async def update_group_settings(self, **kwargs):
            self.calls.append(kwargs)
            return {"group_id": "10001", "mode": "learning_reply", "target_user_ids": []}

        async def observe(self, message):
            self.observed.append(message)

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()
    plugin.config = {
        "permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}
    }
    event = Event()
    event.message_str = "!reply on"
    monkeypatch.setattr(main_module, "parse_recall_notice", lambda _event: None)

    asyncio.run(plugin.capture_group_message(event))

    assert event.stopped is True
    assert plugin.app.observed == []
    assert plugin.app.calls[0]["source"] == "legacy_command"
    assert plugin.app.calls[0]["mode"] == "learning_reply"


def test_parameterless_legacy_alias_toggles_current_group(monkeypatch):
    main_module = load_main(monkeypatch)

    class GroupConfig(Config):
        revision = "revision-1"

        def group_settings(self, group_id):
            return {
                "group_id": group_id,
                "mode": "learning_reply",
                "target_user_ids": [],
                "revision": "revision-1",
            }

    class App:
        def __init__(self):
            self.config = GroupConfig()
            self.calls = []

        async def update_group_settings(self, **kwargs):
            self.calls.append(kwargs)
            return {"group_id": "10001", "mode": "reply", "target_user_ids": []}

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()
    plugin.config = {
        "permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}
    }
    event = Event()

    asyncio.run(
        plugin._handle_group_settings_command(
            event,
            main_module.GroupSettingsCommand("learning"),
            source="legacy_command",
        )
    )

    assert plugin.app.calls[0]["mode"] == "reply"


def test_cross_group_list_requires_plugin_admin_and_formats_original_sections(monkeypatch):
    main_module = load_main(monkeypatch)

    class CrossGroupConfig(Config):
        def cross_group_settings(self):
            return {
                "learning_group_ids": ["10001"],
                "reply_group_ids": ["10002"],
                "excluded_group_ids": ["10003"],
                "group_sub_admins": [
                    {"group_id": "10004", "admin_ids": ["12345"]}
                ],
                "revision": "revision-1",
            }

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.config = CrossGroupConfig()
    event = Event()
    event.is_admin = lambda: True

    asyncio.run(
        plugin._handle_cross_group_command(
            event, main_module.CrossGroupCommand("list")
        )
    )

    assert "已开启学习的群：10001" in event.result.text
    assert "已开启回复的群：10002" in event.result.text
    assert "允许自主管理的群：10004" in event.result.text
    assert "不进入全局词库的群：10003" in event.result.text

    plugin.config = {
        "permissions": {
            "group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]
        }
    }
    event = Event()
    asyncio.run(
        plugin._handle_cross_group_command(
            event, main_module.CrossGroupCommand("list")
        )
    )
    assert event.stopped is True
    assert event.result is None


def test_cross_group_learning_command_persists_all_target_groups(monkeypatch):
    main_module = load_main(monkeypatch)

    class CrossGroupConfig(Config):
        def cross_group_settings(self):
            return {
                "learning_group_ids": [],
                "reply_group_ids": [],
                "excluded_group_ids": [],
                "group_sub_admins": [],
                "revision": "revision-1",
            }

    class App:
        def __init__(self):
            self.config = CrossGroupConfig()
            self.calls = []

        async def update_cross_group_settings(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "learning_group_ids": ["10001", "10002"],
                "reply_group_ids": [],
                "excluded_group_ids": [],
                "group_sub_admins": [],
            }

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()
    plugin.config = {"permissions": {"plugin_admin_ids": ["7"]}}
    event = Event()

    asyncio.run(
        plugin._handle_cross_group_command(
            event,
            main_module.CrossGroupCommand(
                "add", "learning", ("10001", "10002", "10001")
            ),
        )
    )

    assert plugin.app.calls == [
        {
            "action": "add",
            "category": "learning",
            "group_ids": ["10001", "10002"],
            "tag": None,
            "sub_admins": None,
            "expected_revision": "revision-1",
            "actor_id": "7",
            "source": "legacy_command",
        }
    ]
    assert "共 2 个群" in event.result.text


def test_cross_group_subadmin_reads_target_group_managers_before_save(monkeypatch):
    main_module = load_main(monkeypatch)

    class CrossGroupConfig(Config):
        def cross_group_settings(self):
            return {
                "learning_group_ids": [],
                "reply_group_ids": [],
                "excluded_group_ids": [],
                "group_sub_admins": [],
                "revision": "revision-1",
            }

    class App:
        def __init__(self):
            self.config = CrossGroupConfig()
            self.calls = []

        async def update_cross_group_settings(self, **kwargs):
            self.calls.append(kwargs)
            return self.config.cross_group_settings()

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()
    plugin.config = {"permissions": {"plugin_admin_ids": ["7"]}}
    event = Event()

    async def get_group(group_id):
        assert group_id == "10002"
        return SimpleNamespace(
            group_owner="12345", group_admins=[23456, "12345", "bad"]
        )

    event.get_group = get_group
    asyncio.run(
        plugin._handle_cross_group_command(
            event,
            main_module.CrossGroupCommand("add", "subadmin", ("10002",)),
        )
    )

    assert plugin.app.calls[0]["sub_admins"] == {
        "10002": ["12345", "23456"]
    }


def test_cross_group_subadmin_aborts_when_group_managers_cannot_be_read(monkeypatch):
    main_module = load_main(monkeypatch)

    class CrossGroupConfig(Config):
        def cross_group_settings(self):
            return {
                "learning_group_ids": [],
                "reply_group_ids": [],
                "excluded_group_ids": [],
                "group_sub_admins": [],
                "revision": "revision-1",
            }

    class App:
        def __init__(self):
            self.config = CrossGroupConfig()
            self.calls = []

        async def update_cross_group_settings(self, **kwargs):
            self.calls.append(kwargs)

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()
    plugin.config = {"permissions": {"plugin_admin_ids": ["7"]}}
    event = Event()

    async def get_group(_group_id):
        return None

    event.get_group = get_group
    asyncio.run(
        plugin._handle_cross_group_command(
            event,
            main_module.CrossGroupCommand("add", "subadmin", ("10002",)),
        )
    )

    assert plugin.app.calls == []
    assert "未保存任何修改" in event.result.text


def test_group_command_reports_revision_conflict(monkeypatch):
    main_module = load_main(monkeypatch)

    class GroupConfig(Config):
        def group_settings(self, group_id):
            return {
                "group_id": group_id,
                "mode": "learning",
                "target_user_ids": [],
                "revision": "stale-revision",
            }

    class App:
        config = GroupConfig()

        async def update_group_settings(self, **kwargs):
            assert kwargs["expected_revision"] == "stale-revision"
            raise ValueError("revision_conflict")

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()
    plugin.config = {
        "permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}
    }
    event = Event()
    event.message_str = "/ncl reply on"

    asyncio.run(plugin.ncl_reply(event))

    assert event.result.text == "配置已被其他入口修改，请重试。"


def test_group_command_reports_persistence_failure(monkeypatch):
    main_module = load_main(monkeypatch)

    class GroupConfig(Config):
        def group_settings(self, group_id):
            return {
                "group_id": group_id,
                "mode": "learning",
                "target_user_ids": [],
                "revision": "revision-1",
            }

    class App:
        config = GroupConfig()

        async def update_group_settings(self, **_kwargs):
            raise OSError("disk full")

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()
    plugin.config = {
        "permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}
    }
    event = Event()
    event.message_str = "/ncl reply on"

    asyncio.run(plugin.ncl_reply(event))

    assert event.result.text == "群聊设置保存失败，原配置已保留。"


def test_disabled_legacy_alias_continues_normal_learning(monkeypatch):
    main_module = load_main(monkeypatch)
    message = SimpleNamespace(components=(), plain_text="!learning off")

    class GroupConfig(Config):
        def snapshot(self):
            return {"general": {"legacy_command_aliases": False}}

        def learning_enabled_for(self, _group_id):
            return True

        def reply_enabled_for(self, _group_id):
            return False

    class App:
        def __init__(self):
            self.config = GroupConfig()
            self.observed = []

        async def observe(self, value):
            self.observed.append(value)

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()
    event = Event()
    event.message_str = "!learning off"
    monkeypatch.setattr(main_module, "parse_recall_notice", lambda _event: None)
    monkeypatch.setattr(main_module, "parse_fast_delete", lambda _event: None)
    monkeypatch.setattr(main_module, "normalize_group_message", lambda _event: message)

    asyncio.run(plugin.capture_group_message(event))

    assert plugin.app.observed == [message]
    assert event.stopped is False


def test_migrate_scan_returns_report_without_importing(monkeypatch, tmp_path):
    main_module = load_main(monkeypatch)
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.config = {"permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}}
    event = Event()
    event.message_str = f'/ncl migrate-scan "{tmp_path}"'
    monkeypatch.setattr(
        main_module,
        "scan_directory",
        lambda _path, timeout_seconds=60.0: [
            {
                "status": "compatible",
                "path": str(tmp_path / "sample.cl"),
                "structure": {"question_count": 2, "answer_count": 3, "malformed_questions": 0},
            }
        ],
    )

    asyncio.run(plugin.ncl_migrate_scan(event))

    assert "仅扫描，不导入" in event.result.text
    assert "sample.cl：compatible，问题 2，答案 3" in event.result.text


def test_contribution_cleanup_prepare_is_group_scoped_and_confirmable(monkeypatch):
    main_module = load_main(monkeypatch)

    class Cleanup:
        async def prepare(self, **kwargs):
            assert kwargs == {
                "group_id": "10001",
                "user_id": "12345",
                "actor_id": "7",
            }
            return {
                "prepared": True,
                "plan_id": "a" * 32,
                "contributions": 3,
                "affected_answers": 2,
                "answers_becoming_empty": 1,
                "questions_becoming_empty": 1,
                "pending_messages": 1,
            }

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.contribution_cleanup = Cleanup()
    plugin.config = {
        "permissions": {
            "group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]
        }
    }
    event = Event()
    event.message_str = "/ncl contributions-prepare 12345"

    asyncio.run(plugin.ncl_contributions_prepare(event))

    assert "贡献记录：3" in event.result.text
    assert f"/ncl contributions-apply {'a' * 32} 12345 confirm" in event.result.text


def test_contribution_cleanup_apply_requires_literal_confirmation(monkeypatch):
    main_module = load_main(monkeypatch)

    class Cleanup:
        called = False

        async def apply(self, **_kwargs):
            self.called = True
            return {"applied": True}

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    cleanup = Cleanup()
    plugin.app.contribution_cleanup = cleanup
    plugin.config = {
        "permissions": {
            "group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]
        }
    }
    event = Event()
    event.message_str = f"/ncl contributions-apply {'a' * 32} 12345"

    asyncio.run(plugin.ncl_contributions_apply(event))

    assert cleanup.called is False
    assert "confirm" in event.result.text


def test_migrate_prepare_returns_confirmable_import_id(monkeypatch, tmp_path):
    main_module = load_main(monkeypatch)

    class Migration:
        async def prepare(self, path):
            assert path == tmp_path / "sample.cl"
            return {
                "status": "prepared",
                "import_id": "a" * 32,
                "question_count": 2,
                "answer_count": 3,
                "skipped_questions": 0,
                "skipped_answers": 1,
                "unknown_components": 2,
            }

    source = tmp_path / "sample.cl"
    source.write_bytes(b"placeholder")
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.migration = Migration()
    plugin.config = {"permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}}
    event = Event()
    event.message_str = f'/ncl migrate-prepare "{source}"'

    asyncio.run(plugin.ncl_migrate_prepare(event))

    assert f"导入 ID：{'a' * 32}" in event.result.text
    assert "尚未导入" in event.result.text
    assert "跳过答案：1" in event.result.text


def test_migrate_apply_requires_literal_confirmation(monkeypatch):
    main_module = load_main(monkeypatch)

    class Migration:
        called = False

        async def apply(self, **_kwargs):
            self.called = True
            return {"imported": True}

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    migration = Migration()
    plugin.app.migration = migration
    plugin.config = {"permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}}
    event = Event()
    event.message_str = f"/ncl migrate-apply {'a' * 32}"

    asyncio.run(plugin.ncl_migrate_apply(event))

    assert migration.called is False
    assert "confirm" in event.result.text


def test_migrate_apply_targets_current_group_and_reports_backup(monkeypatch):
    main_module = load_main(monkeypatch)

    class Migration:
        async def apply(self, **kwargs):
            assert kwargs == {
                "import_id": "a" * 32,
                "group_id": "10001",
                "actor_id": "7",
            }
            return {
                "imported": True,
                "question_count": 2,
                "answer_count": 3,
                "backup_path": "C:/backup/before-import.sqlite3",
            }

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.migration = Migration()
    plugin.config = {"permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}}
    event = Event()
    event.message_str = f"/ncl migrate-apply {'a' * 32} confirm"

    asyncio.run(plugin.ncl_migrate_apply(event))

    assert "合并问题记录 2，合并答案记录 3" in event.result.text
    assert "before-import.sqlite3" in event.result.text
    assert "不会自动开启" in event.result.text


def test_media_scan_reports_read_only_impact(monkeypatch):
    main_module = load_main(monkeypatch)

    class Media:
        async def scan_group(self, group_id):
            assert group_id == "10001"
            return {
                "scanned_answers": 12,
                "scanned_components": 5,
                "preview": {
                    "media_components": 3,
                    "affected_answers": 2,
                    "answers_becoming_empty": 1,
                },
            }

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.media = Media()
    plugin.config = {"permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}}
    event = Event()
    event.message_str = "/ncl media-scan"

    asyncio.run(plugin.ncl_media_scan(event))

    assert "只标记，不删除" in event.result.text
    assert "失效组件：3" in event.result.text
    assert "清理后可能为空：1" in event.result.text


def test_media_cleanup_apply_requires_literal_confirmation(monkeypatch):
    main_module = load_main(monkeypatch)

    class Media:
        called = False

        async def apply_cleanup(self, **_kwargs):
            self.called = True
            return {"applied": True}

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    media = Media()
    plugin.app.media = media
    plugin.config = {"permissions": {"group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]}}
    event = Event()
    event.message_str = f"/ncl media-cleanup-apply {'a' * 32}"

    asyncio.run(plugin.ncl_media_cleanup_apply(event))

    assert media.called is False
    assert "confirm" in event.result.text


def test_web_status_requires_independent_session_and_sets_security_headers(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token):
            assert token == ""
            return False

    responses = []

    class Response:
        def __init__(self, data, status_code, headers):
            self.data = data
            self.status_code = status_code
            self.headers = headers

    def make_response(data, *, status_code=200, headers=None):
        response = Response(data, status_code, headers or {})
        responses.append(response)
        return response

    monkeypatch.setattr(main_module, "json_response", make_response)
    monkeypatch.setattr(main_module, "request", SimpleNamespace(cookies={}))
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()

    response = asyncio.run(plugin.web_status())

    assert response.status_code == 401
    assert response.data["message"] == "需要登录"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert responses == [response]


def test_web_login_sets_http_only_strict_cookie(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def login(self, password, client_host):
            assert password == "long-enough-password"
            assert client_host == "127.0.0.1"
            return "ok", SimpleNamespace(token="session-token", csrf_token="csrf-token")

    class Response:
        def __init__(self, data, status_code, headers):
            self.data = data
            self.status_code = status_code
            self.headers = headers
            self.cookie = None

        def set_cookie(self, key, value, **kwargs):
            self.cookie = (key, value, kwargs)

    monkeypatch.setattr(
        main_module,
        "json_response",
        lambda data, *, status_code=200, headers=None: Response(
            data, status_code, headers or {}
        ),
    )

    async def body():
        return b'{"password":"long-enough-password"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(client_host="127.0.0.1", cookies={}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()

    response = asyncio.run(plugin.web_auth_login())

    assert response.status_code == 200
    assert response.data["data"]["csrf_token"] == "csrf-token"
    assert response.cookie[0:2] == ("ncl_admin_session", "session-token")
    assert response.cookie[2]["httponly"] is True
    assert response.cookie[2]["samesite"] == "strict"


def test_web_media_scan_requires_csrf_and_targets_requested_group(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert token == "session"
            assert csrf == "csrf"
            return True

    class Media:
        async def scan_group(self, group_id):
            assert group_id == "10001"
            return {"scanned_answers": 2, "scanned_components": 1, "preview": {}}

    async def body():
        return b'{"group_id":"10001","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.media = Media()

    response = asyncio.run(plugin.web_media_scan())

    assert response["status"] == "ok"
    assert response["data"]["scanned_answers"] == 2


def test_web_media_cleanup_requires_confirmation_and_hides_backup_path(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Media:
        async def apply_cleanup(self, **kwargs):
            expected_actor = "webui:" + __import__("hashlib").sha256(b"session").hexdigest()[:16]
            assert kwargs == {
                "plan_id": "a" * 32,
                "group_id": "10001",
                "actor_id": expected_actor,
            }
            return {
                "applied": True,
                "removed_components": 1,
                "updated_answers": 1,
                "deleted_answers": 0,
                "merged_answers": 0,
                "orphan_questions": 0,
                "backup_path": "C:/private/backups/before-cleanup.sqlite3",
            }

    async def body():
        return (
            b'{"group_id":"10001","plan_id":"'
            + b"a" * 32
            + b'","csrf_token":"csrf","confirmed":true}'
        )

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.media = Media()

    response = asyncio.run(plugin.web_media_cleanup_apply())

    assert response["status"] == "ok"
    assert response["data"]["backup_name"] == "before-cleanup.sqlite3"
    assert "backup_path" not in response["data"]


def test_web_library_add_requires_csrf_and_reports_invalid_regex(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert token == "session"
            assert csrf == "csrf"
            return True

    class Library:
        async def add_text_pair(self, **kwargs):
            assert kwargs["group_id"] == "10001"
            assert kwargs["is_regex"] is True
            raise ValueError("invalid_regex")

    async def body():
        return b'{"group_id":"10001","question":"([","answer":"bad","is_regex":true,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.library = Library()

    response = asyncio.run(plugin.web_library_add())

    assert response["status"] == "error"
    assert response["message"] == "正则表达式无法编译。"


def test_web_library_export_uses_confirmed_one_time_session_ticket(monkeypatch, tmp_path):
    main_module = load_main(monkeypatch)
    export_path = tmp_path / "group.zip"
    export_path.write_bytes(b"PK")

    class Auth:
        async def authorize(self, token, csrf=None):
            assert token == "session"
            if csrf is not None:
                assert csrf == "csrf"
            return True

    class Export:
        async def export_group(self, **kwargs):
            assert kwargs["group_id"] == "10001"
            assert kwargs["source"] == "webui"
            assert kwargs["actor_id"].startswith("webui:")
            return {
                "path": export_path,
                "filename": "group.zip",
                "question_count": 2,
                "answer_count": 3,
            }

    async def body():
        return b'{"group_id":"10001","confirmed":true,"csrf_token":"csrf"}'

    request = SimpleNamespace(
        cookies={"ncl_admin_session": "session"}, body=body, query={}
    )
    monkeypatch.setattr(main_module, "request", request)
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.export = Export()

    prepared = asyncio.run(plugin.web_library_export_prepare())

    assert prepared["status"] == "ok"
    assert prepared["data"]["question_count"] == 2
    ticket = prepared["data"]["ticket"]
    request.query = {"ticket": ticket}
    downloaded = asyncio.run(plugin.web_library_export())
    repeated = asyncio.run(plugin.web_library_export())

    assert downloaded["path"] == export_path
    assert downloaded["filename"] == "group.zip"
    assert downloaded["content_type"] == "application/zip"
    assert repeated["status"] == "error"
    assert "无效或已过期" in repeated["message"]


def test_web_library_export_prepare_requires_confirmation(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Export:
        called = False

        async def export_group(self, **_kwargs):
            self.called = True

    async def body():
        return b'{"group_id":"10001","confirmed":false,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(
            cookies={"ncl_admin_session": "session"}, body=body, query={}
        ),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    exporter = Export()
    plugin.app.export = exporter

    response = asyncio.run(plugin.web_library_export_prepare())

    assert response["status"] == "error"
    assert exporter.called is False


def test_web_library_export_supports_legacy_cl_format(monkeypatch, tmp_path):
    main_module = load_main(monkeypatch)
    export_path = tmp_path / "group.cl"
    export_path.write_bytes(b"legacy")

    class Auth:
        async def authorize(self, _token, _csrf=None):
            return True

    class Export:
        async def export_legacy_group(self, **kwargs):
            assert kwargs["group_id"] == "10001"
            return {
                "path": export_path,
                "filename": "group.cl",
                "question_count": 2,
                "answer_count": 3,
                "degraded_components": 1,
            }

    async def body():
        return (
            b'{"group_id":"10001","format":"legacy_cl","confirmed":true,'
            b'"csrf_token":"csrf"}'
        )

    request = SimpleNamespace(
        cookies={"ncl_admin_session": "session"}, body=body, query={}
    )
    monkeypatch.setattr(main_module, "request", request)
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.export = Export()

    prepared = asyncio.run(plugin.web_library_export_prepare())
    request.query = {"ticket": prepared["data"]["ticket"]}
    downloaded = asyncio.run(plugin.web_library_export())

    assert prepared["data"]["degraded_components"] == 1
    assert downloaded["filename"] == "group.cl"
    assert downloaded["content_type"] == "application/octet-stream"


def test_web_library_export_ticket_is_bound_to_session(monkeypatch, tmp_path):
    main_module = load_main(monkeypatch)
    export_path = tmp_path / "group.zip"
    export_path.write_bytes(b"PK")

    class Auth:
        async def authorize(self, _token, _csrf=None):
            return True

    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin._export_tickets = {
        "ticket": {
            "session": "webui:another-session",
            "path": export_path,
            "filename": "group.zip",
            "expires_at": main_module.time.monotonic() + 60,
        }
    }
    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(
            cookies={"ncl_admin_session": "session"}, query={"ticket": "ticket"}
        ),
    )

    response = asyncio.run(plugin.web_library_export())

    assert response["status"] == "error"
    assert "ticket" in plugin._export_tickets


def test_web_migration_upload_authorization_is_session_bound(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert (token, csrf) == ("session", "csrf")
            return True

    async def body():
        return b'{"file_name":"legacy.cl","size_bytes":2048,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.migration = SimpleNamespace(cleanup_expired=lambda: None)
    plugin._migration_upload_tickets = {
        "old-current-session": {
            "session": plugin._web_actor_id(),
            "file_name": "old.cl",
            "size_bytes": 1,
            "expires_at": main_module.time.monotonic() + 60,
        },
        "other-session": {
            "session": "webui:other",
            "file_name": "other.cl",
            "size_bytes": 1,
            "expires_at": main_module.time.monotonic() + 60,
        },
    }
    plugin._migration_uploads = {}

    response = asyncio.run(plugin.web_migration_upload_authorize())

    assert response["status"] == "ok"
    ticket = response["data"]["ticket"]
    assert plugin._migration_upload_tickets[ticket]["file_name"] == "legacy.cl"
    assert plugin._migration_upload_tickets[ticket]["session"].startswith("webui:")
    assert "old-current-session" not in plugin._migration_upload_tickets
    assert "other-session" in plugin._migration_upload_tickets


def test_web_migration_upload_uses_query_free_session_authorization(monkeypatch, tmp_path):
    main_module = load_main(monkeypatch)
    content = b"legacy-library"

    class Auth:
        async def authorize(self, token, csrf=None):
            assert token == "session"
            assert csrf is None
            return True

    class Upload(main_module.PluginUploadFile):
        filename = "legacy.cl"
        content_length = len(content)

        def __init__(self):
            self.offset = 0
            self.closed = False

        async def seek(self, offset):
            self.offset = offset

        async def read(self, size):
            chunk = content[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

        async def close(self):
            self.closed = True

    upload = Upload()

    async def files():
        return {"file": upload}

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, files=files),
    )
    monkeypatch.setattr(
        main_module,
        "scan_file",
        lambda path, **_kwargs: {
            "status": "compatible",
            "reason": "ok",
            "size_bytes": path.stat().st_size,
            "structure": {
                "question_count": 1,
                "answer_count": 1,
                "malformed_questions": 0,
                "component_types": {"Plain": 1},
            },
        },
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.data_dir = tmp_path
    plugin.app.migration = SimpleNamespace(cleanup_expired=lambda: None)
    plugin._migration_uploads = {}
    plugin._migration_upload_tickets = {
        "one-time-ticket": {
            "session": plugin._web_actor_id(),
            "file_name": "legacy.cl",
            "size_bytes": len(content),
            "expires_at": main_module.time.monotonic() + 60,
        }
    }

    response = asyncio.run(plugin.web_migration_upload())

    assert response["status"] == "ok"
    assert response["data"]["scan"]["question_count"] == 1
    assert "one-time-ticket" not in plugin._migration_upload_tickets
    assert upload.closed is True
    stored = plugin._migration_uploads[response["data"]["upload_id"]]
    assert stored["path"].read_bytes() == content


def test_web_migration_prepare_binds_group_actor_and_hides_path(monkeypatch, tmp_path):
    main_module = load_main(monkeypatch)
    source = tmp_path / "random.cl"
    source.write_bytes(b"legacy")

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Migration:
        def cleanup_expired(self):
            pass

        async def prepare(self, path, **kwargs):
            assert path == source
            assert kwargs["group_id"] == "10001"
            assert kwargs["actor_id"].startswith("webui:")
            assert kwargs["source_name"] == "shared.cl"
            return {
                "status": "prepared",
                "import_id": "a" * 32,
                "source_name": "shared.cl",
                "source_size_bytes": 6,
                "group_id": "10001",
                "question_count": 2,
                "answer_count": 3,
                "skipped_questions": 0,
                "skipped_answers": 0,
                "unknown_components": 0,
            }

        def public_manifest(self, value):
            return value

    async def body():
        return b'{"upload_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","group_id":"10001","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.migration = Migration()
    plugin._migration_upload_tickets = {}
    plugin._migration_uploads = {
        "b" * 32: {
            "session": plugin._web_actor_id(),
            "path": source,
            "file_name": "shared.cl",
            "expires_at": main_module.time.monotonic() + 60,
        }
    }

    response = asyncio.run(plugin.web_migration_prepare())

    assert response["status"] == "ok"
    assert response["data"]["source_name"] == "shared.cl"
    assert "path" not in str(response)
    assert not source.exists()


def test_web_migration_apply_requires_confirmation_and_hides_backup_path(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Migration:
        async def apply(self, **kwargs):
            assert kwargs["group_id"] == "10001"
            assert kwargs["actor_id"].startswith("webui:")
            return {
                "imported": True,
                "question_count": 4,
                "answer_count": 5,
                "backup_path": "C:/private/before-import.sqlite3",
            }

    async def body():
        return b'{"import_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","group_id":"10001","confirmed":true,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.migration = Migration()

    response = asyncio.run(plugin.web_migration_apply())

    assert response["status"] == "ok"
    assert response["data"]["backup_name"] == "before-import.sqlite3"
    assert "backup_path" not in response["data"]


def test_web_library_delete_requires_confirmation_is_group_scoped_and_hides_path(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Store:
        async def answer_detail(self, group_id, answer_id):
            assert (group_id, answer_id) == ("10001", 17)
            return {"answer_id": 17, "question_id": 4, "weight": 2}

    class Library:
        async def delete_answer_with_backup(self, **kwargs):
            expected_actor = "webui:" + __import__("hashlib").sha256(b"session").hexdigest()[:16]
            assert kwargs == {"group_id": "10001", "actor_id": expected_actor, "answer_id": 17}
            return {
                "deleted": True,
                "orphan_question_removed": False,
                "backup_path": "C:/private/backups/before-library-delete-answer.sqlite3",
            }

    async def body():
        return b'{"group_id":"10001","answer_id":17,"confirmed":true,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.store = Store()
    plugin.app.library = Library()

    response = asyncio.run(plugin.web_library_delete_answer())

    assert response["status"] == "ok"
    assert response["data"]["backup_name"] == "before-library-delete-answer.sqlite3"
    assert "backup_path" not in response["data"]


def test_web_library_delete_rejects_cross_group_answer_before_backup(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Store:
        async def answer_detail(self, group_id, answer_id):
            assert (group_id, answer_id) == ("10002", 17)

    class Library:
        async def delete_answer_with_backup(self, **_kwargs):
            raise AssertionError("cross-group deletion must not create a backup")

    async def body():
        return b'{"group_id":"10002","answer_id":17,"confirmed":true,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.store = Store()
    plugin.app.library = Library()

    response = asyncio.run(plugin.web_library_delete_answer())

    assert response["status"] == "error"
    assert response["message"] == "本群不存在该答案。"


def test_web_group_settings_update_requires_csrf_and_binds_actor(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert (token, csrf) == ("session", "csrf")
            return True

    class App:
        web_auth = Auth()

        async def update_group_settings(self, **kwargs):
            expected_actor = "webui:" + __import__("hashlib").sha256(b"session").hexdigest()[:16]
            assert kwargs == {
                "group_id": "10001",
                "mode": "silent",
                "target_user_ids": ["12345", "67890"],
                "expected_revision": "oldrevision",
                "actor_id": expected_actor,
            }
            return {
                "group_id": "10001",
                "mode": "silent",
                "target_user_ids": ["12345", "67890"],
                "revision": "newrevision",
            }

    async def body():
        return b'{"group_id":"10001","mode":"silent","target_user_ids":["12345","67890","12345"],"revision":"oldrevision","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()

    response = asyncio.run(plugin.web_group_settings_update())

    assert response["status"] == "ok"
    assert response["data"]["revision"] == "newrevision"


def test_web_group_settings_update_rejects_stale_revision(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Config:
        revision = "currentrevision"

    class App:
        web_auth = Auth()
        config = Config()

        async def update_group_settings(self, **_kwargs):
            raise ValueError("revision_conflict")

    async def body():
        return b'{"group_id":"10001","mode":"learning","target_user_ids":[],"revision":"oldrevision","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()

    response = asyncio.run(plugin.web_group_settings_update())

    assert response["status"] == "error"
    assert response["data"]["revision"] == "currentrevision"


def test_web_group_settings_update_rejects_invalid_target_user(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    async def body():
        return b'{"group_id":"10001","mode":"learning","target_user_ids":["not-a-qq"],"revision":"revision","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()

    response = asyncio.run(plugin.web_group_settings_update())

    assert response["status"] == "error"
    assert response["message"] == "目标用户 QQ 号无效。"


def test_web_permissions_requires_login(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, _csrf=None):
            assert token == ""
            return False

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={}, query={}),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()

    response = asyncio.run(plugin.web_permissions())

    assert response["status"] == "error"
    assert response["message"] == "需要登录"


def test_web_permissions_update_requires_confirmation_and_binds_actor(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert (token, csrf) == ("session", "csrf")
            return True

    class App:
        web_auth = Auth()

        async def update_permission_settings(self, **kwargs):
            assert kwargs["values"] == {
                "plugin_admin_ids": ["12345"],
                "group_sub_admins": [
                    {"group_id": "10001", "admin_ids": ["23456"]}
                ],
            }
            assert kwargs["expected_revision"] == "old"
            assert kwargs["actor_id"].startswith("webui:")
            return {**kwargs["values"], "revision": "new"}

    async def body():
        return b'{"plugin_admin_ids":["12345"],"group_sub_admins":[{"group_id":"10001","admin_ids":["23456"]}],"revision":"old","confirmed":true,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()

    response = asyncio.run(plugin.web_permissions_update())

    assert response["status"] == "ok"
    assert response["data"]["revision"] == "new"


def test_web_permissions_update_rejects_missing_confirmation(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    async def body():
        return b'{"plugin_admin_ids":[],"group_sub_admins":[],"revision":"old","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()

    response = asyncio.run(plugin.web_permissions_update())

    assert response["status"] == "error"
    assert response["message"] == "请先确认权限变更。"


def test_web_permissions_update_reports_revision_conflict(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Config:
        revision = "current"

    class App:
        web_auth = Auth()
        config = Config()

        async def update_permission_settings(self, **_kwargs):
            raise ValueError("revision_conflict")

    async def body():
        return b'{"plugin_admin_ids":[],"group_sub_admins":[],"revision":"old","confirmed":true,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()

    response = asyncio.run(plugin.web_permissions_update())

    assert response["status"] == "error"
    assert response["data"]["revision"] == "current"


def test_web_tts_settings_requires_login_and_hides_reference_path(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, _csrf=None):
            assert token == "session"
            return True

    class Config:
        def tts_settings(self):
            return {
                "enabled": True,
                "driver": "gpt_sovits",
                "probability_percent": 20,
                "max_text_length": 100,
                "reference_audio_path": "C:/private/voices/reference.wav",
                "revision": "revision",
            }

    class TTS:
        def status(self):
            return {"available": True}

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, query={}),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.config = Config()
    plugin.app.tts = TTS()

    response = asyncio.run(plugin.web_tts_settings())

    assert response["status"] == "ok"
    assert response["data"]["reference_audio_name"] == "reference.wav"
    assert "reference_audio_path" not in response["data"]
    assert "C:/private" not in str(response)


def test_web_tts_settings_update_uses_csrf_revision_and_preserves_reference(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert (token, csrf) == ("session", "csrf")
            return True

    class Config:
        revision = "new"

        def tts_settings(self):
            return {
                "enabled": False,
                "driver": "windows",
                "probability_percent": 0,
                "max_text_length": 100,
                "voice": "",
                "rate": 0,
                "volume": 100,
                "endpoint_url": "http://127.0.0.1:9880/tts",
                "timeout_seconds": 30,
                "text_lang": "zh",
                "reference_audio_path": "C:/private/reference.wav",
                "prompt_text": "",
                "prompt_lang": "zh",
                "revision": "old",
            }

    class TTS:
        def status(self):
            return {"available": True}

    class App:
        web_auth = Auth()
        config = Config()
        tts = TTS()

        async def update_tts_settings(self, **kwargs):
            assert kwargs["expected_revision"] == "old"
            assert kwargs["values"]["reference_audio_path"] == "C:/private/reference.wav"
            assert kwargs["values"]["probability_percent"] == 50
            assert kwargs["actor_id"].startswith("webui:")
            return {**kwargs["values"], "revision": "new"}

    async def body():
        return b'{"enabled":true,"driver":"windows","probability_percent":50,"revision":"old","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()

    response = asyncio.run(plugin.web_tts_settings_update())

    assert response["status"] == "ok"
    assert response["data"]["reference_audio_name"] == "reference.wav"
    assert "reference_audio_path" not in response["data"]


def test_web_tts_test_returns_only_safe_file_metadata(monkeypatch, tmp_path):
    main_module = load_main(monkeypatch)
    output = tmp_path / "private" / "test.wav"
    output.parent.mkdir()
    output.write_bytes(b"RIFF\x24\x00\x00\x00WAVEdata")

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Config:
        def tts_settings(self):
            return {"driver": "windows"}

    class TTS:
        async def synthesize(self, text):
            assert text == "hello"
            return output

    async def body():
        return b'{"text":"hello","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.config = Config()
    plugin.app.tts = TTS()

    response = asyncio.run(plugin.web_tts_test())

    assert response["data"]["file_name"] == "test.wav"
    assert "private" not in str(response)


def test_web_filter_settings_update_requires_csrf_and_binds_actor(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert (token, csrf) == ("session", "csrf")
            return True

    class App:
        web_auth = Auth()

        async def update_filter_settings(self, **kwargs):
            assert kwargs["expected_revision"] == "old"
            assert kwargs["values"]["contains"] == ["bad"]
            assert "csrf_token" not in kwargs["values"]
            assert kwargs["actor_id"].startswith("webui:")
            return {"revision": "new"}

    async def body():
        return b'{"enabled":true,"contains":["bad"],"revision":"old","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()

    response = asyncio.run(plugin.web_filter_settings_update())
    assert response["status"] == "ok"
    assert response["data"]["revision"] == "new"


def test_web_filter_test_requires_csrf_and_does_not_mutate(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, csrf):
            assert csrf == "csrf"
            return True

    class App:
        web_auth = Auth()

        def test_filter_rules(self, **kwargs):
            assert kwargs == {
                "group_id": "10001",
                "text": "candidate",
                "component_type": "Plain",
            }
            return {"reply": {"matched": True}, "sensitive": {"matched": False}}

    async def body():
        return b'{"group_id":"10001","text":"candidate","component_type":"Plain","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()

    response = asyncio.run(plugin.web_filter_test())
    assert response["data"]["reply"]["matched"] is True


def test_web_manual_blacklist_update_is_validated_and_audited_by_runtime(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class App:
        web_auth = Auth()

        async def update_blacklist(self, **kwargs):
            assert kwargs["scope"] == "group"
            assert kwargs["group_id"] == "10001"
            assert kwargs["user_id"] == "12345"
            assert kwargs["blocked"] is True
            assert kwargs["actor_id"].startswith("webui:")
            return {"blocked": True}

    async def body():
        return b'{"scope":"group","group_id":"10001","user_id":"12345","blocked":true,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()

    response = asyncio.run(plugin.web_filter_blacklist_update())
    assert response["data"]["blocked"] is True


def test_web_filter_cleanup_prepare_requires_csrf_and_hides_operations(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert (token, csrf) == ("session", "csrf")
            return True

    class Cleanup:
        async def prepare_cleanup(self, **kwargs):
            assert kwargs["group_id"] == "10001"
            assert kwargs["actor_id"].startswith("webui:")
            return {
                "prepared": True,
                "plan_id": "a" * 32,
                "created_at": "2026-08-13T00:00:00+00:00",
                "expires_at": "2026-08-13T01:00:00+00:00",
                "affected_answers": 2,
                "affected_questions": 1,
                "questions_becoming_empty": 1,
                "rule_type_counts": {"contains": 2},
                "operations": [{"answer_id": 7}],
            }

    async def body():
        return b'{"group_id":"10001","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.filter_cleanup = Cleanup()

    response = asyncio.run(plugin.web_filter_cleanup_prepare())
    assert response["status"] == "ok"
    assert response["data"]["affected_answers"] == 2
    assert "operations" not in response["data"]


def test_web_filter_cleanup_apply_requires_confirmation_and_hides_backup_path(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Cleanup:
        async def apply_cleanup(self, **kwargs):
            assert kwargs["plan_id"] == "a" * 32
            assert kwargs["group_id"] == "10001"
            assert kwargs["actor_id"].startswith("webui:")
            return {
                "applied": True,
                "deleted_answers": 2,
                "orphan_questions": 1,
                "backup_path": "C:/private/backups/before-filter-cleanup.sqlite3",
            }

    async def body():
        return (
            b'{"group_id":"10001","plan_id":"'
            + b"a" * 32
            + b'","confirmed":true,"csrf_token":"csrf"}'
        )

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.filter_cleanup = Cleanup()

    response = asyncio.run(plugin.web_filter_cleanup_apply())
    assert response["status"] == "ok"
    assert response["data"]["backup_name"] == "before-filter-cleanup.sqlite3"
    assert "backup_path" not in response["data"]


def test_web_filter_cleanup_apply_reports_stale_plan(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Cleanup:
        async def apply_cleanup(self, **_kwargs):
            return {"applied": False, "reason": "plan_stale"}

    async def body():
        return (
            b'{"group_id":"10001","plan_id":"'
            + b"a" * 32
            + b'","confirmed":true,"csrf_token":"csrf"}'
        )

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.filter_cleanup = Cleanup()

    response = asyncio.run(plugin.web_filter_cleanup_apply())
    assert response["status"] == "error"
    assert "已变化" in response["message"]


def test_web_contribution_cleanup_prepare_hides_operations(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert (token, csrf) == ("session", "csrf")
            return True

    class Cleanup:
        async def prepare(self, **kwargs):
            assert kwargs["group_id"] == "10001"
            assert kwargs["user_id"] == "12345"
            assert kwargs["actor_id"].startswith("webui:")
            return {
                "prepared": True,
                "plan_id": "a" * 32,
                "group_id": "10001",
                "user_id": "12345",
                "contributions": 3,
                "affected_answers": 2,
                "affected_questions": 2,
                "answers_becoming_empty": 1,
                "questions_becoming_empty": 1,
                "pending_messages": 1,
                "operations": [{"answer_id": 10}],
            }

    async def body():
        return b'{"group_id":"10001","user_id":"12345","csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.contribution_cleanup = Cleanup()

    response = asyncio.run(plugin.web_contribution_cleanup_prepare())
    assert response["status"] == "ok"
    assert response["data"]["contributions"] == 3
    assert "operations" not in response["data"]


def test_web_contribution_cleanup_apply_requires_confirmation_and_hides_path(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Cleanup:
        async def apply(self, **kwargs):
            assert kwargs["plan_id"] == "a" * 32
            assert kwargs["group_id"] == "10001"
            assert kwargs["user_id"] == "12345"
            assert kwargs["actor_id"].startswith("webui:")
            return {
                "applied": True,
                "removed_contributions": 3,
                "deleted_answers": 1,
                "backup_path": "C:/private/backups/before-contribution-delete.sqlite3",
            }

    async def body():
        return (
            b'{"group_id":"10001","user_id":"12345","plan_id":"'
            + b"a" * 32
            + b'","confirmed":true,"csrf_token":"csrf"}'
        )

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.contribution_cleanup = Cleanup()

    response = asyncio.run(plugin.web_contribution_cleanup_apply())
    assert response["status"] == "ok"
    assert response["data"]["backup_name"] == "before-contribution-delete.sqlite3"
    assert "backup_path" not in response["data"]


def test_web_backups_requires_login_and_returns_safe_metadata(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, _csrf=None):
            assert token == "session"
            return True

    class Backup:
        async def list_backups(self):
            return [
                {
                    "name": "before-test.sqlite3",
                    "size_bytes": 1024,
                    "modified_at": "2026-08-13T00:00:00+00:00",
                    "kind": "other",
                }
            ]

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.backup = Backup()

    response = asyncio.run(plugin.web_backups())
    assert response["data"]["backups"][0]["name"] == "before-test.sqlite3"
    assert "path" not in response["data"]["backups"][0]


def test_web_tasks_requires_login_and_returns_safe_history(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, _csrf=None):
            assert token == "session"
            return True

    class Tasks:
        async def list_tasks(self):
            return [
                {
                    "task_id": "a" * 32,
                    "name": "每日扫描",
                    "task_type": "media_scan",
                    "history": [{"status": "success", "summary": {"scanned_answers": 2}}],
                }
            ]

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.tasks = Tasks()

    response = asyncio.run(plugin.web_tasks())
    assert response["status"] == "ok"
    assert response["data"]["tasks"][0]["history"][0]["summary"] == {
        "scanned_answers": 2
    }


def test_web_task_save_passes_csrf_confirmation_and_revision(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, csrf):
            assert (token, csrf) == ("session", "csrf")
            return True

    class Tasks:
        async def save_task(self, **kwargs):
            assert kwargs["task_id"] == "a" * 32
            assert kwargs["expected_revision"] == 4
            assert kwargs["cleanup_mode"] == "apply"
            assert kwargs["confirmed"] is True
            assert kwargs["actor_id"].startswith("webui:")
            return {"task_id": kwargs["task_id"], "revision": 5}

    async def body():
        return (
            b'{"task_id":"'
            + b"a" * 32
            + b'","revision":4,"name":"auto","task_type":"filter_cleanup",'
            b'"group_id":"10001","enabled":true,"interval_minutes":1440,'
            b'"cleanup_mode":"apply","confirmed":true,"csrf_token":"csrf"}'
        )

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.tasks = Tasks()

    response = asyncio.run(plugin.web_task_save())
    assert response["status"] == "ok"
    assert response["data"]["revision"] == 5


def test_web_task_run_does_not_hide_destructive_confirmation(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

    class Tasks:
        async def run_now(self, **kwargs):
            assert kwargs["confirmed"] is False
            raise ValueError("destructive_confirmation_required")

    async def body():
        return b'{"task_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","confirmed":false,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.tasks = Tasks()

    response = asyncio.run(plugin.web_task_run())
    assert response["status"] == "error"
    assert response["message"] == "立即执行自动删除任务前需要明确确认。"


def test_web_audit_requires_login_and_passes_bounded_cursor(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, _csrf=None):
            assert token == "session"
            return True

    class Audit:
        async def list_entries(self, **kwargs):
            assert kwargs == {
                "action": "delete_answer",
                "before_id": 50,
                "limit": 25,
            }
            return {
                "entries": [{"id": 49, "action": "delete_answer"}],
                "has_more": False,
                "next_before_id": None,
                "actions": [],
            }

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(
            cookies={"ncl_admin_session": "session"},
            query={"action": "delete_answer", "before_id": "50", "limit": "25"},
        ),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.audit = Audit()

    response = asyncio.run(plugin.web_audit())
    assert response["status"] == "ok"
    assert response["data"]["entries"][0]["id"] == 49


def test_web_diagnostics_requires_login_and_returns_snapshot(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, token, _csrf=None):
            assert token == "session"
            return True

    class App:
        web_auth = Auth()

        async def diagnostic_snapshot(self):
            return {
                "runtime_counters_reset_on_reload": True,
                "groups": [{"group_id": "10001", "mode": "silent"}],
            }

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app = App()

    response = asyncio.run(plugin.web_diagnostics())

    assert response["status"] == "ok"
    assert response["data"]["groups"] == [{"group_id": "10001", "mode": "silent"}]


def test_web_audit_rejects_invalid_page_size(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf=None):
            return True

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(
            cookies={"ncl_admin_session": "session"},
            query={"action": "", "before_id": "", "limit": "1000"},
        ),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()

    response = asyncio.run(plugin.web_audit())
    assert response["status"] == "error"
    assert response["message"] == "分页大小必须在 1 到 100 之间。"


def test_web_audit_rejects_invalid_cursor(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf=None):
            return True

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(
            cookies={"ncl_admin_session": "session"},
            query={"action": "", "before_id": "not-an-id", "limit": "50"},
        ),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()

    response = asyncio.run(plugin.web_audit())
    assert response["status"] == "error"
    assert response["message"] == "审计分页游标无效。"


def test_web_backup_inspect_rejects_invalid_name(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf=None):
            return True

    class Backup:
        async def inspect(self, _name):
            raise ValueError("invalid_backup_name")

    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(
            cookies={"ncl_admin_session": "session"},
            query={"name": "../outside.sqlite3"},
        ),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    plugin.app.web_auth = Auth()
    plugin.app.backup = Backup()

    response = asyncio.run(plugin.web_backup_inspect())
    assert response["status"] == "error"
    assert response["message"] == "备份文件不存在或名称无效。"


def test_web_backup_restore_requires_confirmation_invalidates_sessions_and_cookie(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        invalidated = False

        async def authorize(self, _token, _csrf):
            return True

        async def invalidate_all_sessions(self):
            self.invalidated = True

    class Backup:
        async def restore(self, **kwargs):
            assert kwargs["name"] == "before-test.sqlite3"
            assert kwargs["actor_id"].startswith("webui:")
            return {
                "restored": True,
                "backup_name": "before-test.sqlite3",
                "safety_backup_name": "before-restore-now.sqlite3",
                "schema_version": 8,
            }

    class Response:
        def __init__(self, data, status_code, headers):
            self.data = data
            self.status_code = status_code
            self.headers = headers
            self.deleted_cookie = None

        def delete_cookie(self, key, **kwargs):
            self.deleted_cookie = (key, kwargs)

    async def body():
        return b'{"name":"before-test.sqlite3","confirmed":true,"csrf_token":"csrf"}'

    monkeypatch.setattr(
        main_module,
        "json_response",
        lambda data, *, status_code=200, headers=None: Response(
            data, status_code, headers or {}
        ),
    )
    monkeypatch.setattr(
        main_module,
        "request",
        SimpleNamespace(cookies={"ncl_admin_session": "session"}, body=body),
    )
    plugin, _reply, _history = plugin_with(main_module, ReplyDecision(None, "no_match"))
    auth = Auth()
    plugin.app.web_auth = auth
    plugin.app.backup = Backup()

    response = asyncio.run(plugin.web_backup_restore())
    assert response.data["status"] == "ok"
    assert auth.invalidated is True
    assert response.deleted_cookie == ("ncl_admin_session", {"path": "/"})
