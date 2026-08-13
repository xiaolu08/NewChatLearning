from __future__ import annotations

from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain


def render_message_chain(
    components: tuple[dict[str, Any], ...],
    *,
    max_plain_length: int,
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
        rendered = _render_component(component, max_plain_length=max_plain_length)
        if rendered is not None:
            chain.chain.append(rendered)
    return chain if chain.chain else None


def _render_component(component: dict[str, Any], *, max_plain_length: int) -> Any | None:
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
    if component_type == "image":
        source = str(data.get("path") or data.get("url") or data.get("file") or "")
        return _media_component(Comp.Image, source)
    if component_type in {"record", "voice"}:
        source = str(data.get("path") or data.get("url") or data.get("file") or "")
        return _media_component(Comp.Record, source)
    if component_type == "video":
        source = str(data.get("path") or data.get("url") or data.get("file") or "")
        return _media_component(Comp.Video, source)
    if component_type == "json" and isinstance(data.get("data"), (dict, str)):
        try:
            return Comp.Json(data=data["data"])
        except (TypeError, ValueError):
            return None
    if component_type == "file":
        file_path = str(data.get("file_") or data.get("file") or "")
        url = str(data.get("url") or "")
        if not file_path and not url:
            return None
        return Comp.File(
            name=str(data.get("name") or "file"),
            file=file_path,
            url=url,
        )
    return None


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
