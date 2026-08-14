from __future__ import annotations

from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain


class _RawOneBotComponent:
    def __init__(self, segment_type: str, data: dict[str, Any]) -> None:
        self.segment_type = segment_type
        self.data = data

    def toDict(self) -> dict[str, Any]:
        return {"type": self.segment_type, "data": self.data}


def render_message_chain(
    components: tuple[dict[str, Any], ...],
    *,
    max_plain_length: int,
    data_dir: Path | None = None,
) -> MessageChain | None:
    if any(
        str(component.get("type", "")).lower() == "plain"
        and len(str(component.get("data", {}).get("text", ""))) > max_plain_length
        for component in components
        if isinstance(component.get("data"), dict)
    ):
        return None
    chain = MessageChain()
    for component in components:
        rendered = _render_component(
            component,
            max_plain_length=max_plain_length,
            data_dir=data_dir,
        )
        if rendered is not None:
            chain.chain.append(rendered)
    return chain if chain.chain else None


def _render_component(
    component: dict[str, Any],
    *,
    max_plain_length: int,
    data_dir: Path | None,
) -> Any | None:
    component_type = str(component.get("type", "")).lower()
    data = component.get("data", {})
    if not isinstance(data, dict):
        return None

    if component_type == "plain":
        text = str(data.get("text", ""))
        if not text:
            return None
        return Comp.Plain(text)
    if component_type == "face":
        try:
            return Comp.Face(id=int(data["id"]))
        except (KeyError, TypeError, ValueError):
            return None
    if component_type in {"at", "atall"}:
        qq = str(data.get("qq", ""))
        if not qq:
            return None
        return Comp.At(qq=qq, name=str(data.get("name", "") or ""))
    if component_type in {"image", "flashimage"}:
        source = _media_source(data, data_dir)
        return _media_component(Comp.Image, source)
    if component_type == "marketface":
        source = _media_source(data, data_dir)
        if source:
            return _media_component(Comp.Image, source)
        raw = {
            key: data[key]
            for key in ("emoji_id", "emoji_package_id", "key", "summary")
            if data.get(key) not in (None, "")
        }
        return _RawOneBotComponent("mface", raw) if raw else Comp.Plain("[商城表情]")
    if component_type in {"record", "voice"}:
        source = _media_source(data, data_dir)
        return _media_component(Comp.Record, source)
    if component_type == "video":
        source = _media_source(data, data_dir)
        return _media_component(Comp.Video, source)
    if component_type == "json" and isinstance(data.get("data"), (dict, str)):
        try:
            return Comp.Json(data=data["data"])
        except (TypeError, ValueError):
            return None
    if component_type == "xml":
        xml = str(data.get("data") or "").replace("\x00", "")[:32768]
        return _RawOneBotComponent("xml", {"data": xml}) if xml else None
    if component_type in {"node", "nodes", "forward"}:
        nodes = data.get("nodes") if component_type == "nodes" else [data]
        if not isinstance(nodes, list):
            return None
        rendered_nodes = []
        for node in nodes[:50]:
            rendered = _render_node(node, max_plain_length=max_plain_length, data_dir=data_dir)
            if rendered is not None:
                rendered_nodes.append(rendered)
        return Comp.Nodes(rendered_nodes) if rendered_nodes else None
    if component_type == "share" and data.get("url") and data.get("title"):
        return Comp.Share(
            url=str(data["url"]),
            title=str(data["title"]),
            content=str(data.get("content") or ""),
            image=str(data.get("image") or ""),
        )
    if component_type in {"music", "musicshare"}:
        return Comp.Music(
            _type=str(data.get("_type") or data.get("type") or "custom"),
            id=data.get("id") or 0,
            url=str(data.get("url") or ""),
            audio=str(data.get("audio") or ""),
            title=str(data.get("title") or ""),
            content=str(data.get("content") or ""),
            image=str(data.get("image") or ""),
        )
    if component_type == "dice":
        return Comp.Dice(id=data.get("id") or data.get("value") or 0)
    if component_type == "file":
        file_path = _media_source(data, data_dir, remote_fallback=False)
        url = str(data.get("url") or "")
        if not file_path and not url:
            return None
        return Comp.File(
            name=str(data.get("name") or "file"),
            file=file_path,
            url=url,
        )
    return None


def _render_node(
    node: Any,
    *,
    max_plain_length: int,
    data_dir: Path | None,
) -> Any | None:
    if not isinstance(node, dict) or not isinstance(node.get("content"), list):
        return None
    content = []
    for component in node["content"][:50]:
        if not isinstance(component, dict):
            continue
        rendered = _render_component(
            component,
            max_plain_length=max_plain_length,
            data_dir=data_dir,
        )
        if rendered is not None:
            content.append(rendered)
    if not content:
        return None
    return Comp.Node(
        content=content,
        uin=str(node.get("uin") or "0"),
        name=str(node.get("name") or ""),
    )


def _media_source(
    data: dict[str, Any],
    data_dir: Path | None,
    *,
    remote_fallback: bool = True,
) -> str:
    relative = str(data.get("media_path") or "")
    if relative and data_dir is not None:
        root = Path(data_dir).resolve()
        candidate = (root / relative).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return str(candidate)
    local = str(data.get("path") or data.get("file_") or data.get("file") or "")
    if local and not local.startswith(("http://", "https://")):
        return local
    return str(data.get("url") or local or "") if remote_fallback else ""


def _media_component(component_type: Any, source: str) -> Any | None:
    if not source:
        return None
    try:
        if source.startswith(("http://", "https://")):
            return component_type.fromURL(source)
        if source.startswith("base64://"):
            return component_type.fromBase64(source.removeprefix("base64://"))
        return component_type.fromFileSystem(source)
    except (OSError, TypeError, ValueError):
        return None
