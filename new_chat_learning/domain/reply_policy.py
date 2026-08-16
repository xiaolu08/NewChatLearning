from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

TRIGGER_TYPES = (
    "text",
    "image",
    "face",
    "marketface",
    "xml",
    "json",
    "record",
    "video",
    "file",
    "forward",
    "share",
    "music",
    "dice",
    "mixed",
    "other",
)

TRIGGER_TYPE_LABELS = {
    "text": "文本",
    "image": "图片",
    "face": "QQ 表情",
    "marketface": "表情包",
    "xml": "XML 卡片",
    "json": "JSON 卡片",
    "record": "语音",
    "video": "视频",
    "file": "文件",
    "forward": "合并转发",
    "share": "分享卡片",
    "music": "音乐卡片",
    "dice": "骰子",
    "mixed": "混合消息",
    "other": "其他消息",
}

_TRIGGER_TYPE_ALIASES = {
    "plain": "text",
    "文本": "text",
    "图片": "image",
    "emoji": "face",
    "qqface": "face",
    "表情": "face",
    "mface": "marketface",
    "sticker": "marketface",
    "表情包": "marketface",
    "voice": "record",
    "audio": "record",
    "语音": "record",
    "视频": "video",
    "文件": "file",
    "node": "forward",
    "nodes": "forward",
    "forwardmessage": "forward",
    "转发": "forward",
    "musicshare": "music",
    "混合": "mixed",
    "其他": "other",
}

_COMPONENT_TRIGGER_TYPES = {
    "plain": "text",
    "image": "image",
    "face": "face",
    "marketface": "marketface",
    "mface": "marketface",
    "xml": "xml",
    "json": "json",
    "record": "record",
    "video": "video",
    "file": "file",
    "forward": "forward",
    "node": "forward",
    "nodes": "forward",
    "share": "share",
    "music": "music",
    "musicshare": "music",
    "dice": "dice",
}


def normalize_trigger_type(value: Any) -> str | None:
    normalized = str(value or "").strip().casefold().replace("-", "").replace("_", "")
    normalized = _TRIGGER_TYPE_ALIASES.get(normalized, normalized)
    return normalized if normalized in TRIGGER_TYPES else None


def classify_trigger_components(components: Iterable[Mapping[str, Any]]) -> str:
    categories: list[str] = []
    for component in components:
        component_type = str(component.get("type", "")).strip().casefold()
        if component_type in {"at", "atall", "reply", "quote"}:
            continue
        category = _COMPONENT_TRIGGER_TYPES.get(component_type, "other")
        if category == "text":
            data = component.get("data", {})
            if isinstance(data, Mapping) and not str(data.get("text", "")).strip():
                continue
        if category not in categories:
            categories.append(category)
    if not categories:
        return "other"
    return categories[0] if len(categories) == 1 else "mixed"
