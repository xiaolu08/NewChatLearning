from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from new_chat_learning.domain.message import NormalizedMessage, RecallNotice

TRANSIENT_FIELDS = {"url", "path", "message_id", "time", "seq"}
COMMAND_PREFIXES = ("/", "ncl ")


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
    data = dict(component.get("data", {}))
    for field in TRANSIENT_FIELDS:
        data.pop(field, None)
    file_value = data.get("file")
    if isinstance(file_value, str) and file_value.lower().startswith(
        ("http://", "https://", "file:", "base64://", "data:")
    ):
        data.pop("file", None)
    if component["type"].lower() == "reply":
        data.pop("id", None)
    return {"type": component["type"], "data": data}


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
