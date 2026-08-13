from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "general": {"enabled": True, "legacy_command_aliases": True},
    "learning": {
        "enabled": False,
        "group_ids": [],
        "interval_seconds": 900,
    },
    "reply": {
        "enabled": False,
        "group_ids": [],
        "silent_group_ids": [],
        "probability_percent": 50.0,
        "cooldown_seconds": 3.0,
        "wait_seconds": 0.0,
        "wait_jitter_seconds": 0.0,
        "max_plain_length": 100,
        "at_force_reply": True,
        "regex_enabled": True,
        "regex_timeout_ms": 50,
        "similarity_enabled": False,
        "similarity_threshold": 0.5,
        "similarity_max_length": 35,
        "type_frequency_thresholds": {},
    },
    "library": {
        "mode": "group",
        "excluded_group_ids": [],
        "group_tags": [],
    },
    "permissions": {"plugin_admin_ids": []},
    "storage": {
        "media_persistence_enabled": True,
        "media_quota_gb": 10.0,
        "media_max_file_mb": 50.0,
        "media_download_timeout_seconds": 15.0,
    },
    "webui": {"enabled": True},
    "tts": {"enabled": False, "driver": "windows"},
}


class ConfigService:
    """Read-through facade shared by AstrBot config, commands, and WebUI."""

    def __init__(self, source: dict[str, Any] | None) -> None:
        self._source = source if source is not None else {}

    def snapshot(self) -> dict[str, Any]:
        merged = deepcopy(DEFAULT_CONFIG)
        for section, value in self._source.items():
            if isinstance(value, dict) and isinstance(merged.get(section), dict):
                merged[section].update(deepcopy(value))
            else:
                merged[section] = deepcopy(value)
        return merged

    @property
    def revision(self) -> str:
        payload = json.dumps(
            self.snapshot(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def learning_enabled_for(self, group_id: str) -> bool:
        snapshot = self.snapshot()
        if not bool(snapshot["general"].get("enabled", True)):
            return False
        learning = snapshot["learning"]
        if not bool(learning.get("enabled", False)):
            return False
        group_ids = {str(item).strip() for item in learning.get("group_ids", [])}
        return str(group_id) in group_ids

    @property
    def learning_interval_seconds(self) -> int:
        raw = self.snapshot()["learning"].get("interval_seconds", 900)
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 900

    def reply_enabled_for(self, group_id: str) -> bool:
        snapshot = self.snapshot()
        if not bool(snapshot["general"].get("enabled", True)):
            return False
        reply = snapshot["reply"]
        group_id = str(group_id)
        silent_groups = {str(item).strip() for item in reply.get("silent_group_ids", [])}
        if group_id in silent_groups or not bool(reply.get("enabled", False)):
            return False
        return group_id in {str(item).strip() for item in reply.get("group_ids", [])}

    def reply_settings(self) -> dict[str, Any]:
        reply = self.snapshot()["reply"]
        return {
            "probability_percent": self._bounded_float(
                reply.get("probability_percent"), 50.0, 0.0, 100.0
            ),
            "cooldown_seconds": self._bounded_float(
                reply.get("cooldown_seconds"), 3.0, 0.0, 86400.0
            ),
            "wait_seconds": self._bounded_float(reply.get("wait_seconds"), 0.0, 0.0, 3600.0),
            "wait_jitter_seconds": self._bounded_float(
                reply.get("wait_jitter_seconds"), 0.0, 0.0, 3600.0
            ),
            "max_plain_length": self._bounded_int(reply.get("max_plain_length"), 100, 1, 100000),
            "at_force_reply": bool(reply.get("at_force_reply", True)),
            "regex_enabled": bool(reply.get("regex_enabled", True)),
            "regex_timeout_ms": self._bounded_int(reply.get("regex_timeout_ms"), 50, 1, 1000),
            "similarity_enabled": bool(reply.get("similarity_enabled", False)),
            "similarity_threshold": self._bounded_float(
                reply.get("similarity_threshold"), 0.5, 0.0, 1.0
            ),
            "similarity_max_length": self._bounded_int(
                reply.get("similarity_max_length"), 35, 1, 1000
            ),
            "type_frequency_thresholds": self._type_frequency_thresholds(
                reply.get("type_frequency_thresholds")
            ),
        }

    def media_settings(self) -> dict[str, Any]:
        storage = self.snapshot()["storage"]
        return {
            "enabled": bool(storage.get("media_persistence_enabled", True)),
            "quota_bytes": int(
                self._bounded_float(storage.get("media_quota_gb"), 10.0, 0.0, 1024.0) * 1024**3
            ),
            "max_file_bytes": int(
                self._bounded_float(storage.get("media_max_file_mb"), 50.0, 0.1, 4096.0) * 1024**2
            ),
            "timeout_seconds": self._bounded_float(
                storage.get("media_download_timeout_seconds"), 15.0, 1.0, 300.0
            ),
        }

    def reply_library_scopes(
        self,
        group_id: str,
        available_group_ids: list[str],
    ) -> tuple[tuple[str, ...], ...]:
        library = self.snapshot()["library"]
        group_id = str(group_id)
        if str(library.get("mode", "group")).lower() != "global":
            return ((group_id,),)

        group_tags = self._normalized_group_tags(library.get("group_tags"))
        requested_tags = group_tags.get(group_id, ())
        if requested_tags:
            scopes = []
            for tag in requested_tags:
                members = tuple(
                    member
                    for member, tags in group_tags.items()
                    if tag in tags and member in available_group_ids
                )
                if members:
                    scopes.append(members)
            return tuple(scopes)

        excluded = {str(item).strip() for item in library.get("excluded_group_ids", [])}
        tagged_groups = set(group_tags)
        members = tuple(
            candidate
            for candidate in available_group_ids
            if candidate not in excluded and candidate not in tagged_groups
        )
        return (members,) if members else ()

    def library_status(self) -> dict[str, Any]:
        library = self.snapshot()["library"]
        group_tags = self._normalized_group_tags(library.get("group_tags"))
        return {
            "mode": (
                "global" if str(library.get("mode", "group")).lower() == "global" else "group"
            ),
            "excluded_group_ids": sorted(
                {
                    str(item).strip()
                    for item in library.get("excluded_group_ids", [])
                    if str(item).strip()
                }
            ),
            "tagged_groups": len(group_tags),
            "tags": sorted({tag for tags in group_tags.values() for tag in tags}),
        }

    @staticmethod
    def _normalized_group_tags(value: Any) -> dict[str, tuple[str, ...]]:
        if isinstance(value, list):
            entries = (
                (item.get("group_id"), item.get("tags")) for item in value if isinstance(item, dict)
            )
        elif isinstance(value, dict):
            entries = value.items()
        else:
            return {}
        result = {}
        for raw_group_id, raw_tags in entries:
            if not isinstance(raw_tags, (list, tuple)):
                continue
            group_id = str(raw_group_id).strip()
            tags = tuple(dict.fromkeys(str(tag).strip() for tag in raw_tags if str(tag).strip()))
            if group_id and tags:
                result[group_id] = tags
        return result

    @classmethod
    def _type_frequency_thresholds(cls, value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        return {
            str(component_type).lower(): cls._bounded_int(threshold, 0, 0, 1000000)
            for component_type, threshold in value.items()
            if str(component_type).strip()
        }

    @staticmethod
    def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            return min(maximum, max(minimum, float(value)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            return min(maximum, max(minimum, int(value)))
        except (TypeError, ValueError):
            return default
