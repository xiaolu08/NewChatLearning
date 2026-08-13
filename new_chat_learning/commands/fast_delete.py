from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

FAST_DELETE_PATTERN = re.compile(r"^[!！](?:d|delete)(?:\s+(\d+))?\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FastDeleteRequest:
    quoted_message_id: str | None
    recent_position: int | None


def parse_fast_delete(event: Any) -> FastDeleteRequest | None:
    text = str(getattr(event, "get_message_str", lambda: "")()).strip()
    match = FAST_DELETE_PATTERN.fullmatch(text)
    if match is None:
        return None
    position = int(match.group(1)) if match.group(1) else None
    if position is not None and position < 1:
        return None
    quoted_id = _quoted_message_id(event)
    return FastDeleteRequest(quoted_id, position)


def _quoted_message_id(event: Any) -> str | None:
    for component in getattr(event, "get_messages", list)():
        if str(getattr(component, "type", component.__class__.__name__)).lower() in {
            "reply",
            "quote",
        }:
            value = str(getattr(component, "id", "") or "").strip()
            if value:
                return value
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    try:
        segments = raw.get("message", [])
    except AttributeError:
        return None
    for segment in segments if isinstance(segments, list) else []:
        if not isinstance(segment, dict) or str(segment.get("type", "")).lower() != "reply":
            continue
        value = str(segment.get("data", {}).get("id", "")).strip()
        if value:
            return value
    return None
