from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from enum import Enum
from typing import Any

from new_chat_learning.domain.message import (
    NormalizedMessage,
    RecallNotice,
    normalized_components_key,
)

TRANSIENT_FIELDS = {"url", "path", "message_id", "time", "seq", "key"}
COMMAND_PREFIXES = ("/", "ncl ")
MAX_FORWARD_DEPTH = 2
MAX_FORWARD_NODES = 50
MAX_NODE_COMPONENTS = 50
MAX_XML_LENGTH = 32768

logger = logging.getLogger(__name__)


def _plain_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _component_payload(component: Any) -> dict[str, Any]:
    component_type = getattr(component, "type", component.__class__.__name__)
    if isinstance(component_type, Enum):
        component_type = component_type.value
    model_dump = getattr(component, "model_dump", None)
    if callable(model_dump):
        data = model_dump(mode="python")
    elif hasattr(component, "dict") and callable(component.dict):
        data = component.dict()
    else:
        data = dict(getattr(component, "__dict__", {}))
    data.pop("type", None)
    return {"type": str(component_type), "data": _plain_value(data)}


def _matching_payload(component: dict[str, Any]) -> dict[str, Any]:
    data = _stable_value(component.get("data", {}))
    file_value = data.get("file")
    if isinstance(file_value, str) and file_value.lower().startswith(
        ("http://", "https://", "file:", "base64://", "data:")
    ):
        data.pop("file", None)
    if component["type"].lower() == "reply":
        data.pop("id", None)
    return {"type": component["type"], "data": data}


def _stable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_value(item)
            for key, item in value.items()
            if str(key) not in TRANSIENT_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    return _plain_value(value)


def _raw_event_dict(event: Any) -> dict[str, Any]:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        return dict(raw or {})
    except (TypeError, ValueError):
        return {}


def _is_command_event(event: Any, raw: dict[str, Any]) -> bool:
    candidates = [
        str(getattr(event, "get_message_str", lambda: "")()),
        str(raw.get("raw_message") or ""),
    ]
    raw_segments = raw.get("message")
    if isinstance(raw_segments, list):
        candidates.extend(
            str(segment.get("data", {}).get("text") or "")
            for segment in raw_segments
            if isinstance(segment, Mapping) and segment.get("type") == "text"
        )
    return any(candidate.strip().lower().startswith(COMMAND_PREFIXES) for candidate in candidates)


def parse_recall_notice(event: Any) -> RecallNotice | None:
    raw = _raw_event_dict(event)
    if raw.get("post_type") != "notice" or raw.get("notice_type") != "group_recall":
        return None
    group_id = str(raw.get("group_id") or "")
    message_id = str(raw.get("message_id") or "")
    if not group_id or not message_id:
        return None
    return RecallNotice(
        platform=str(getattr(event, "get_platform_name", lambda: "aiocqhttp")()),
        group_id=group_id,
        message_id=message_id,
    )


def normalize_group_message(event: Any) -> NormalizedMessage | None:
    raw = _raw_event_dict(event)
    if raw.get("post_type") and raw.get("post_type") != "message":
        return None
    group_id = str(getattr(event, "get_group_id", lambda: "")())
    sender_id = str(getattr(event, "get_sender_id", lambda: "")())
    self_id = str(getattr(event, "get_self_id", lambda: "")())
    if not group_id or not sender_id or sender_id == self_id:
        return None
    if _is_command_event(event, raw):
        return None
    message_obj = getattr(event, "message_obj", None)
    message_id = str(getattr(message_obj, "message_id", "") or raw.get("message_id") or "")
    timestamp = int(getattr(message_obj, "timestamp", 0) or raw.get("time") or 0)
    if not message_id or timestamp <= 0:
        return None
    components = tuple(
        _component_payload(component) for component in getattr(event, "get_messages", list)()
    )
    matching = tuple(_matching_payload(component) for component in components)
    message = NormalizedMessage(
        platform=str(getattr(event, "get_platform_name", lambda: "aiocqhttp")()),
        group_id=group_id,
        sender_id=sender_id,
        message_id=message_id,
        timestamp=timestamp,
        components=components,
        matching_components=matching,
    )
    return None if message.is_empty else message


async def enrich_long_tail_components(
    event: Any,
    message: NormalizedMessage,
) -> NormalizedMessage:
    raw_segments = _raw_event_dict(event).get("message")
    additions = []
    if isinstance(raw_segments, list):
        existing_types = {str(item.get("type", "")).lower() for item in message.components}
        for segment in raw_segments:
            if not isinstance(segment, Mapping):
                continue
            segment_type = str(segment.get("type", "")).lower()
            if (
                segment_type == "mface" and "marketface" not in existing_types
            ) or (segment_type == "xml" and "xml" not in existing_types):
                component = _raw_segment_component(segment)
                if component is not None:
                    additions.append(component)

    components = []
    changed = bool(additions)
    for component in message.components:
        if str(component.get("type", "")).lower() != "forward":
            components.append(component)
            continue
        forward_id = str(component.get("data", {}).get("id", "")).strip()
        resolved = await _resolve_forward_component(event, forward_id, depth=0)
        components.append(resolved or _forward_fallback())
        changed = True
    components.extend(additions)
    if not changed:
        return message
    normalized = tuple(components)
    return replace(
        message,
        components=normalized,
        matching_components=tuple(_matching_payload(item) for item in normalized),
    )


async def _resolve_forward_component(
    event: Any,
    forward_id: str,
    *,
    depth: int,
) -> dict[str, Any] | None:
    if not forward_id or depth > MAX_FORWARD_DEPTH:
        return None
    payload = await _fetch_forward_payload(event, forward_id)
    nodes = _extract_forward_nodes(payload)
    if not nodes:
        return None
    converted = []
    for node in nodes[:MAX_FORWARD_NODES]:
        converted_node = await _convert_forward_node(event, node, depth=depth)
        if converted_node is not None:
            converted.append(converted_node)
    return {"type": "Nodes", "data": {"nodes": converted}} if converted else None


async def _fetch_forward_payload(event: Any, forward_id: str) -> Any:
    bot = getattr(event, "bot", None)
    call_action = getattr(bot, "call_action", None)
    if not callable(call_action):
        return None
    values: list[Any] = [forward_id]
    if forward_id.isdigit():
        values.append(int(forward_id))
    for value in values:
        for key in ("message_id", "id"):
            try:
                result = await call_action("get_forward_msg", **{key: value})
            except Exception:  # noqa: BLE001 - adapters differ in accepted ID field/type
                logger.debug("Forward lookup parameter variant was rejected.")
                continue
            if _extract_forward_nodes(result):
                return result
    return None


def _extract_forward_nodes(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return data
    for target in (data, payload):
        if not isinstance(target, Mapping):
            continue
        for key in ("messages", "message", "nodes", "nodeList", "content"):
            value = target.get(key)
            if isinstance(value, list):
                return value
    return []


async def _convert_forward_node(
    event: Any,
    node: Any,
    *,
    depth: int,
) -> dict[str, Any] | None:
    if not isinstance(node, Mapping):
        return None
    data = node.get("data") if isinstance(node.get("data"), Mapping) else node
    sender = node.get("sender") if isinstance(node.get("sender"), Mapping) else {}
    raw_content = data.get("content") or node.get("message") or data.get("message")
    if not isinstance(raw_content, list):
        return None
    content = []
    for segment in raw_content[:MAX_NODE_COMPONENTS]:
        if not isinstance(segment, Mapping):
            continue
        segment_type = str(segment.get("type", "")).lower()
        if segment_type == "forward" and depth < MAX_FORWARD_DEPTH:
            nested_id = str(segment.get("data", {}).get("id", "")).strip()
            nested = await _resolve_forward_component(event, nested_id, depth=depth + 1)
            content.append(nested or _forward_fallback())
            continue
        converted = _raw_segment_component(segment)
        if converted is not None:
            content.append(converted)
    if not content:
        return None
    return {
        "uin": str(data.get("user_id") or data.get("uin") or sender.get("user_id") or "0"),
        "name": str(data.get("nickname") or data.get("name") or sender.get("nickname") or ""),
        "content": content,
    }


def _raw_segment_component(segment: Mapping[str, Any]) -> dict[str, Any] | None:
    segment_type = str(segment.get("type", "")).lower()
    data = segment.get("data")
    if not isinstance(data, Mapping):
        data = {}
    payload = {str(key): _plain_value(value) for key, value in data.items()}
    names = {
        "text": "Plain",
        "image": "Image",
        "record": "Record",
        "video": "Video",
        "file": "File",
        "face": "Face",
        "at": "At",
        "reply": "Reply",
        "json": "Json",
        "share": "Share",
        "music": "Music",
        "dice": "Dice",
        "mface": "MarketFace",
        "xml": "Xml",
    }
    component_type = names.get(segment_type)
    if component_type is None:
        return None
    if segment_type == "text":
        payload = {"text": str(payload.get("text", ""))}
    elif segment_type == "mface":
        payload = {
            key: value
            for key, value in payload.items()
            if key in {"emoji_id", "emoji_package_id", "key", "summary", "url", "file"}
        }
    elif segment_type == "xml":
        xml = str(payload.get("data") or payload.get("xml") or "").replace("\x00", "")
        if not xml:
            return None
        payload = {"data": xml[:MAX_XML_LENGTH]}
    return {"type": component_type, "data": payload}


def _forward_fallback() -> dict[str, Any]:
    return {"type": "Plain", "data": {"text": "[合并转发消息内容暂不可用]"}}


def reply_matching_key(message: NormalizedMessage, self_id: str) -> str:
    components: list[dict[str, Any]] = []
    for component in message.matching_components:
        component_type = str(component.get("type", "")).lower()
        data = dict(component.get("data", {}))
        if component_type in {"reply", "quote"}:
            continue
        if component_type in {"at", "atall"} and str(data.get("qq", "")) in {
            str(self_id),
            "all",
        }:
            continue
        if component_type == "plain" and isinstance(data.get("text"), str):
            text = data["text"].strip()
            if not text:
                continue
            data["text"] = text
        components.append({"type": component.get("type", ""), "data": data})
    return normalized_components_key(components)
