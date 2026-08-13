import importlib
import sys
import types


class Component:
    def __init__(self, *args, **kwargs):
        if args:
            self.text = args[0]
        self.__dict__.update(kwargs)


class Media(Component):
    @classmethod
    def fromURL(cls, value):
        return cls(file=value)

    @classmethod
    def fromBase64(cls, value):
        return cls(file=f"base64://{value}")

    @classmethod
    def fromFileSystem(cls, value):
        return cls(file=value)


class MessageChain:
    def __init__(self):
        self.chain = []


def load_renderer(monkeypatch):
    components = types.ModuleType("astrbot.api.message_components")
    components.Plain = Component
    components.Face = Component
    components.At = Component
    components.Image = Media
    components.Record = Media
    components.Video = Media
    components.Json = Component
    components.File = Component
    event = types.ModuleType("astrbot.api.event")
    event.MessageChain = MessageChain
    api = types.ModuleType("astrbot.api")
    api.message_components = components
    astrbot = types.ModuleType("astrbot")
    astrbot.api = api
    monkeypatch.setitem(sys.modules, "astrbot", astrbot)
    monkeypatch.setitem(sys.modules, "astrbot.api", api)
    monkeypatch.setitem(sys.modules, "astrbot.api.message_components", components)
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event)
    sys.modules.pop("new_chat_learning.platform.astrbot.renderer", None)
    return importlib.import_module("new_chat_learning.platform.astrbot.renderer")


def test_renderer_builds_supported_components(monkeypatch):
    renderer = load_renderer(monkeypatch)

    chain = renderer.render_message_chain(
        (
            {"type": "Plain", "data": {"text": "hello"}},
            {"type": "Image", "data": {"url": "https://example.test/a.png"}},
            {"type": "Unknown", "data": {"value": "ignored"}},
        ),
        max_plain_length=100,
    )

    assert chain is not None
    assert len(chain.chain) == 2
    assert chain.chain[0].text == "hello"
    assert chain.chain[1].file.startswith("https://")


def test_renderer_rejects_whole_answer_when_plain_text_is_too_long(monkeypatch):
    renderer = load_renderer(monkeypatch)

    chain = renderer.render_message_chain(
        (
            {"type": "Plain", "data": {"text": "too long"}},
            {"type": "Image", "data": {"url": "https://example.test/a.png"}},
        ),
        max_plain_length=3,
    )

    assert chain is None


def test_renderer_skips_file_without_path_or_url(monkeypatch):
    renderer = load_renderer(monkeypatch)

    chain = renderer.render_message_chain(
        ({"type": "File", "data": {"name": "missing.bin"}},),
        max_plain_length=100,
    )

    assert chain is None
