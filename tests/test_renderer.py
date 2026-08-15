import importlib
import sys
import types
from pathlib import Path


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


class Nodes(Component):
    def __init__(self, nodes):
        self.nodes = nodes


def load_renderer(monkeypatch):
    components = types.ModuleType("astrbot.api.message_components")
    components.Plain = Component
    components.Face = Component
    components.At = Component
    components.Image = Media
    components.Record = Media
    components.Video = Media
    components.Json = Component
    components.Node = Component
    components.Nodes = Nodes
    components.File = Component
    components.Share = Component
    components.Music = Component
    components.Dice = Component
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


def test_renderer_prefers_persisted_media_and_falls_back_to_url(monkeypatch, tmp_path):
    renderer = load_renderer(monkeypatch)
    media = tmp_path / "media" / "aa" / "asset.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"image")
    component = {
        "type": "Image",
        "data": {
            "media_path": "media/aa/asset.png",
            "url": "https://example.test/fallback.png",
        },
    }

    local_chain = renderer.render_message_chain(
        (component,),
        max_plain_length=100,
        data_dir=Path(tmp_path),
    )
    media.unlink()
    remote_chain = renderer.render_message_chain(
        (component,),
        max_plain_length=100,
        data_dir=Path(tmp_path),
    )

    assert local_chain.chain[0].file == str(media)
    assert remote_chain.chain[0].file == "https://example.test/fallback.png"


def test_renderer_does_not_treat_onebot_image_id_as_local_file(monkeypatch):
    renderer = load_renderer(monkeypatch)

    chain = renderer.render_message_chain(
        (
            {
                "type": "Image",
                "data": {
                    "file": "09AD9B554FB83AB4CDAFEA9135AC309B.jpeg",
                    "url": "https://gchat.qpic.cn/download?temporary=1",
                },
            },
        ),
        max_plain_length=100,
    )

    assert chain is not None
    assert chain.chain[0].toDict() == {
        "type": "image",
        "data": {"file": "09AD9B554FB83AB4CDAFEA9135AC309B.jpeg"},
    }
    assert chain.chain[0].type == "image"


def test_raw_onebot_xml_component_exposes_astrbot_type(monkeypatch):
    renderer = load_renderer(monkeypatch)

    chain = renderer.render_message_chain(
        ({"type": "XML", "data": {"data": "<msg/>"}},),
        max_plain_length=100,
    )

    assert chain is not None
    assert chain.chain[0].type == "xml"


def test_renderer_rejects_media_path_outside_data_directory(monkeypatch, tmp_path):
    renderer = load_renderer(monkeypatch)
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"image")

    chain = renderer.render_message_chain(
        (
            {
                "type": "Image",
                "data": {"media_path": "../outside.png"},
            },
        ),
        max_plain_length=100,
        data_dir=Path(tmp_path),
    )

    assert chain is None


def test_renderer_builds_share_music_and_dice(monkeypatch):
    renderer = load_renderer(monkeypatch)

    chain = renderer.render_message_chain(
        (
            {
                "type": "Share",
                "data": {"url": "https://example.test", "title": "Example"},
            },
            {
                "type": "Music",
                "data": {"type": "qq", "id": 123},
            },
            {"type": "Dice", "data": {"value": 6}},
        ),
        max_plain_length=100,
    )

    assert len(chain.chain) == 3
    assert chain.chain[0].title == "Example"
    assert chain.chain[1]._type == "qq"
    assert chain.chain[2].id == 6


def test_renderer_builds_market_face_xml_and_forward_nodes(monkeypatch):
    renderer = load_renderer(monkeypatch)

    chain = renderer.render_message_chain(
        (
            {
                "type": "MarketFace",
                "data": {"emoji_id": "e1", "emoji_package_id": "p1", "summary": "开心"},
            },
            {"type": "Xml", "data": {"data": "<msg>card</msg>"}},
            {
                "type": "Nodes",
                "data": {
                    "nodes": [
                        {
                            "uin": "42",
                            "name": "群友",
                            "content": [{"type": "Plain", "data": {"text": "内容"}}],
                        }
                    ]
                },
            },
        ),
        max_plain_length=100,
    )

    assert chain.chain[0].toDict()["type"] == "mface"
    assert chain.chain[1].toDict() == {"type": "xml", "data": {"data": "<msg>card</msg>"}}
    assert chain.chain[2].nodes[0].uin == "42"
    assert chain.chain[2].nodes[0].content[0].text == "内容"
