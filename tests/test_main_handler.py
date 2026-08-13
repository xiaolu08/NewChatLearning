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
    web_module.json_response = lambda value, **_kwargs: value
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


def test_web_media_cleanup_reauthenticates_and_hides_backup_path(monkeypatch):
    main_module = load_main(monkeypatch)

    class Auth:
        async def authorize(self, _token, _csrf):
            return True

        async def reauthenticate(self, **kwargs):
            assert kwargs == {
                "session_token": "session",
                "csrf_token": "csrf",
                "password": "long-enough-password",
            }
            return "ok"

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
            + b'","csrf_token":"csrf","password":"long-enough-password"}'
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
