from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

import regex

DEFAULT_CONFIG: dict[str, Any] = {
    "general": {"enabled": True, "legacy_command_aliases": True},
    "learning": {
        "enabled": False,
        "group_ids": [],
        "target_users": [],
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
    "filters": {
        "enabled": True,
        "contains": [],
        "exact": [],
        "regex": [],
        "component_types": ["At", "AtAll", "Quote", "Poke"],
        "sensitive": [],
        "blacklist_threshold": 5,
        "blacklist_scope": "global",
        "regex_timeout_ms": 50,
        "group_rules": [],
    },
    "permissions": {"plugin_admin_ids": [], "group_sub_admins": []},
    "storage": {
        "media_persistence_enabled": True,
        "media_quota_gb": 10.0,
        "media_max_file_mb": 50.0,
        "media_download_timeout_seconds": 15.0,
    },
    "webui": {"enabled": True},
    "tts": {
        "enabled": False,
        "driver": "windows",
        "probability_percent": 0.0,
        "max_text_length": 100,
        "voice": "",
        "rate": 0,
        "volume": 100,
        "endpoint_url": "http://127.0.0.1:9880/tts",
        "timeout_seconds": 30.0,
        "text_lang": "zh",
        "reference_audio_path": "",
        "prompt_text": "",
        "prompt_lang": "zh",
        "model": "",
        "region": "",
        "app_id": "",
        "app_key": "",
        "response_mode": "audio",
        "custom_headers": {},
        "custom_body": {"text": "${text}", "voice": "${voice}", "model": "${model}"},
        "custom_response_field": "audio_base64",
        "per_minute_requests": 10,
        "daily_requests": 200,
        "daily_characters": 50000,
    },
}


class ConfigService:
    """Read-through facade shared by AstrBot config, commands, and WebUI."""

    def __init__(self, source: dict[str, Any] | None) -> None:
        self._source = source if source is not None else {}
        self._lock = asyncio.Lock()

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

    @asynccontextmanager
    async def revision_guard(self, expected_revision: str) -> AsyncIterator[None]:
        async with self._lock:
            if expected_revision != self.revision:
                raise ValueError("revision_conflict")
            yield

    def learning_enabled_for(self, group_id: str) -> bool:
        snapshot = self.snapshot()
        if not bool(snapshot["general"].get("enabled", True)):
            return False
        learning = snapshot["learning"]
        if not bool(learning.get("enabled", False)):
            return False
        group_ids = {str(item).strip() for item in learning.get("group_ids", [])}
        return str(group_id) in group_ids

    def learning_target_users_for(self, group_id: str) -> tuple[str, ...]:
        learning = self.snapshot()["learning"]
        for entry in learning.get("target_users", []):
            if not isinstance(entry, dict) or str(entry.get("group_id", "")).strip() != str(
                group_id
            ):
                continue
            user_ids = entry.get("user_ids", [])
            if not isinstance(user_ids, list):
                return ()
            return tuple(
                dict.fromkeys(
                    str(user_id).strip()
                    for user_id in user_ids
                    if str(user_id).strip().isdigit()
                )
            )
        return ()

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

    def configured_group_ids(self) -> list[str]:
        snapshot = self.snapshot()
        values = {
            str(group_id).strip()
            for section, key in (
                ("learning", "group_ids"),
                ("reply", "group_ids"),
                ("reply", "silent_group_ids"),
            )
            for group_id in snapshot[section].get(key, [])
            if str(group_id).strip()
        }
        values.update(
            str(entry.get("group_id", "")).strip()
            for entry in snapshot["learning"].get("target_users", [])
            if isinstance(entry, dict) and str(entry.get("group_id", "")).strip()
        )
        values.update(
            str(entry.get("group_id", "")).strip()
            for entry in snapshot["filters"].get("group_rules", [])
            if isinstance(entry, dict) and str(entry.get("group_id", "")).strip()
        )
        return sorted(values)

    def filter_settings(self, group_id: str) -> dict[str, Any]:
        filters = self.snapshot()["filters"]
        result = {
            "enabled": bool(filters.get("enabled", True)),
            "contains": self._normalized_text_rules(filters.get("contains"), 200),
            "exact": self._normalized_text_rules(filters.get("exact"), 200),
            "regex": self._normalized_text_rules(filters.get("regex"), 100),
            "component_types": [
                str(value).strip().lower()
                for value in filters.get("component_types", [])
                if str(value).strip()
            ],
            "sensitive": self._normalized_text_rules(filters.get("sensitive"), 200),
            "blacklist_threshold": self._bounded_int(
                filters.get("blacklist_threshold"), 5, 1, 1000000
            ),
            "blacklist_scope": (
                "group" if str(filters.get("blacklist_scope", "global")) == "group" else "global"
            ),
            "regex_timeout_ms": self._bounded_int(
                filters.get("regex_timeout_ms"), 50, 1, 1000
            ),
            "revision": self.revision,
        }
        for entry in filters.get("group_rules", []):
            if not isinstance(entry, dict) or str(entry.get("group_id", "")).strip() != str(
                group_id
            ):
                continue
            for key, limit in (("contains", 200), ("exact", 200), ("regex", 100), ("sensitive", 200)):
                result[key] = list(
                    dict.fromkeys(result[key] + self._normalized_text_rules(entry.get(key), limit))
                )
            result["component_types"] = list(
                dict.fromkeys(
                    result["component_types"]
                    + [
                        str(value).strip().lower()
                        for value in entry.get("component_types", [])
                        if str(value).strip()
                    ]
                )
            )
            break
        return result

    async def update_filter_settings(
        self,
        *,
        values: dict[str, Any],
        expected_revision: str,
    ) -> dict[str, Any]:
        async with self._lock:
            if expected_revision != self.revision:
                raise ValueError("revision_conflict")
            normalized = self._validated_filter_update(values)
            original = deepcopy(self._source)
            try:
                self._source["filters"] = normalized
                await self._persist_source()
            except Exception:
                self._source.clear()
                self._source.update(original)
                raise
            return self.filter_settings("")

    def permission_settings(self) -> dict[str, Any]:
        permissions = self.snapshot()["permissions"]
        return {
            **self._validated_permission_update(permissions),
            "revision": self.revision,
        }

    async def update_permission_settings(
        self,
        *,
        values: dict[str, Any],
        expected_revision: str,
    ) -> dict[str, Any]:
        async with self._lock:
            if expected_revision != self.revision:
                raise ValueError("revision_conflict")
            normalized = self._validated_permission_update(values)
            original = deepcopy(self._source)
            try:
                self._source["permissions"] = normalized
                await self._persist_source()
            except Exception:
                self._source.clear()
                self._source.update(original)
                raise
            return self.permission_settings()

    def cross_group_settings(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        tags = self._normalized_group_tags(snapshot["library"].get("group_tags"))
        permissions = self._validated_permission_update(snapshot["permissions"])
        return {
            "learning_group_ids": self._normalized_group_ids(
                snapshot["learning"].get("group_ids", [])
            ),
            "reply_group_ids": self._normalized_group_ids(
                snapshot["reply"].get("group_ids", [])
            ),
            "silent_group_ids": self._normalized_group_ids(
                snapshot["reply"].get("silent_group_ids", [])
            ),
            "excluded_group_ids": self._normalized_group_ids(
                snapshot["library"].get("excluded_group_ids", [])
            ),
            "group_tags": [
                {"group_id": group_id, "tags": list(group_tags)}
                for group_id, group_tags in tags.items()
            ],
            "group_sub_admins": permissions["group_sub_admins"],
            "revision": self.revision,
        }

    async def update_cross_group_settings(
        self,
        *,
        action: str,
        category: str,
        group_ids: list[str],
        expected_revision: str,
        tag: str | None = None,
        sub_admins: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        if action not in {"add", "remove"} or category not in {
            "learning", "learnings", "reply", "tag", "subadmin", "unmerge"
        }:
            raise ValueError("invalid_cross_group_settings")
        normalized_groups = self._normalized_group_ids(group_ids)
        if not normalized_groups:
            raise ValueError("invalid_cross_group_settings")
        normalized_tag = self._bounded_text(tag, 64) if tag is not None else None
        if category == "tag" and action == "add" and not normalized_tag:
            raise ValueError("invalid_cross_group_settings")

        async with self._lock:
            if expected_revision != self.revision:
                raise ValueError("revision_conflict")
            original = deepcopy(self._source)
            try:
                learning = self._source.setdefault("learning", {})
                reply = self._source.setdefault("reply", {})
                library = self._source.setdefault("library", {})
                permissions = self._source.setdefault("permissions", {})
                enabled = action == "add"

                if category in {"learning", "learnings"}:
                    learning["group_ids"] = self._replace_group_memberships(
                        learning.get("group_ids", []), normalized_groups, enabled
                    )
                    if enabled:
                        learning["enabled"] = True
                    if not enabled:
                        reply["silent_group_ids"] = self._replace_group_memberships(
                            reply.get("silent_group_ids", []), normalized_groups, False
                        )
                if category in {"reply", "learnings"}:
                    reply["group_ids"] = self._replace_group_memberships(
                        reply.get("group_ids", []), normalized_groups, enabled
                    )
                    reply["silent_group_ids"] = self._replace_group_memberships(
                        reply.get("silent_group_ids", []), normalized_groups, False
                    )
                    if enabled:
                        reply["enabled"] = True
                if category == "unmerge":
                    library["excluded_group_ids"] = self._replace_group_memberships(
                        library.get("excluded_group_ids", []), normalized_groups, enabled
                    )
                if category == "tag":
                    tags = self._normalized_group_tags(library.get("group_tags"))
                    for group_id in normalized_groups:
                        if enabled:
                            tags[group_id] = tuple(
                                dict.fromkeys((*tags.get(group_id, ()), normalized_tag))
                            )
                        else:
                            tags.pop(group_id, None)
                    library["group_tags"] = [
                        {"group_id": group_id, "tags": list(values)}
                        for group_id, values in tags.items()
                    ]
                if category == "subadmin":
                    current = self._validated_permission_update(
                        self.snapshot()["permissions"]
                    )
                    entries = {
                        entry["group_id"]: list(entry["admin_ids"])
                        for entry in current["group_sub_admins"]
                    }
                    if enabled:
                        if not isinstance(sub_admins, dict):
                            raise ValueError("invalid_cross_group_settings")
                        for group_id in normalized_groups:
                            admin_ids = self._normalized_qq_ids(
                                sub_admins.get(group_id), maximum=100
                            )
                            if not admin_ids:
                                raise ValueError("invalid_cross_group_settings")
                            entries[group_id] = admin_ids
                    else:
                        for group_id in normalized_groups:
                            entries.pop(group_id, None)
                    permissions["group_sub_admins"] = [
                        {"group_id": group_id, "admin_ids": admin_ids}
                        for group_id, admin_ids in entries.items()
                    ]
                await self._persist_source()
            except Exception:
                self._source.clear()
                self._source.update(original)
                raise
            return self.cross_group_settings()

    def tts_settings(self) -> dict[str, Any]:
        tts = self.snapshot()["tts"]
        result = self._validated_tts_update(tts, allow_unavailable_driver=True)
        result["revision"] = self.revision
        return result

    async def update_tts_settings(
        self,
        *,
        values: dict[str, Any],
        expected_revision: str,
    ) -> dict[str, Any]:
        async with self._lock:
            if expected_revision != self.revision:
                raise ValueError("revision_conflict")
            normalized = self._validated_tts_update(values)
            original = deepcopy(self._source)
            try:
                self._source["tts"] = normalized
                await self._persist_source()
            except Exception:
                self._source.clear()
                self._source.update(original)
                raise
            return self.tts_settings()

    def _validated_tts_update(
        self, values: dict[str, Any], *, allow_unavailable_driver: bool = False
    ) -> dict[str, Any]:
        if not isinstance(values, dict) or not isinstance(values.get("enabled"), bool):
            raise TypeError("invalid_tts")
        driver = str(values.get("driver", "windows")).strip()
        available_drivers = {
            "windows",
            "gpt_sovits",
            "local_http",
            "volcengine",
            "aliyun",
            "tencent",
            "azure",
            "openai",
            "openai_compatible",
            "custom_http",
        }
        if driver not in available_drivers and not allow_unavailable_driver:
            raise ValueError("tts_driver_unavailable")
        endpoint_url = str(values.get("endpoint_url", "")).strip()
        if driver in {"gpt_sovits", "local_http"}:
            from new_chat_learning.tts.service import _is_loopback_http_url

            if not _is_loopback_http_url(endpoint_url):
                raise ValueError("tts_endpoint_must_be_loopback")
        if (
            driver in {"openai_compatible", "custom_http", "volcengine", "aliyun", "tencent", "azure"}
            and endpoint_url
            and not endpoint_url.startswith("https://")
        ):
            raise ValueError("tts_cloud_endpoint_must_be_https")
        if driver in {"openai_compatible", "custom_http"} and not endpoint_url:
            raise ValueError("tts_cloud_endpoint_required")
        result = {
            "enabled": values["enabled"],
            "driver": driver,
            "probability_percent": self._bounded_float(
                values.get("probability_percent"), 0.0, 0.0, 100.0
            ),
            "max_text_length": self._bounded_int(
                values.get("max_text_length"), 100, 1, 1000
            ),
            "voice": self._bounded_text(values.get("voice"), 200),
            "rate": self._bounded_int(values.get("rate"), 0, -10, 10),
            "volume": self._bounded_int(values.get("volume"), 100, 0, 100),
            "endpoint_url": endpoint_url,
            "timeout_seconds": self._bounded_float(
                values.get("timeout_seconds"), 30.0, 1.0, 120.0
            ),
            "text_lang": self._bounded_text(values.get("text_lang", "zh"), 20) or "zh",
            "reference_audio_path": self._bounded_text(
                values.get("reference_audio_path"), 1000
            ),
            "prompt_text": self._bounded_text(values.get("prompt_text"), 1000),
            "prompt_lang": self._bounded_text(values.get("prompt_lang", "zh"), 20)
            or "zh",
            "model": self._bounded_text(values.get("model"), 200),
            "region": self._bounded_text(values.get("region"), 100),
            "app_id": self._bounded_text(values.get("app_id"), 200),
            "app_key": self._bounded_text(values.get("app_key"), 200),
            "response_mode": self._bounded_text(values.get("response_mode", "audio"), 30)
            or "audio",
            "custom_headers": self._validated_mapping(values.get("custom_headers", {})),
            "custom_body": self._validated_mapping(
                values.get("custom_body", {"text": "${text}"})
            ),
            "custom_response_field": self._bounded_text(
                values.get("custom_response_field", "audio_base64"), 100
            )
            or "audio_base64",
            "per_minute_requests": self._bounded_int(
                values.get("per_minute_requests"), 10, 1, 10000
            ),
            "daily_requests": self._bounded_int(values.get("daily_requests"), 200, 1, 1000000),
            "daily_characters": self._bounded_int(
                values.get("daily_characters"), 50000, 1, 10000000
            ),
        }
        if result["enabled"] and result["probability_percent"] <= 0:
            raise ValueError("invalid_tts_probability")
        if driver == "gpt_sovits" and result["enabled"] and not result["reference_audio_path"]:
            raise ValueError("gpt_sovits_reference_required")
        return result

    @classmethod
    def _validated_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or len(value) > 50:
            raise ValueError("invalid_tts_mapping")
        result = {}
        for key, item in value.items():
            name = str(key).strip()
            if not name or len(name) > 100:
                raise ValueError("invalid_tts_mapping")
            if isinstance(item, dict):
                result[name] = cls._validated_mapping(item)
            elif isinstance(item, list):
                if len(item) > 50:
                    raise ValueError("invalid_tts_mapping")
                result[name] = [cls._bounded_text(entry, 1000) for entry in item]
            elif isinstance(item, (str, int, float, bool)) or item is None:
                result[name] = item
            else:
                raise ValueError("invalid_tts_mapping")
        return result

    def _validated_permission_update(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise TypeError("invalid_permissions")
        plugin_admin_ids = self._normalized_qq_ids(
            values.get("plugin_admin_ids"), maximum=100
        )
        raw_groups = values.get("group_sub_admins", [])
        if not isinstance(raw_groups, list) or len(raw_groups) > 200:
            raise ValueError("invalid_permissions")
        group_sub_admins = []
        seen_groups = set()
        for entry in raw_groups:
            if not isinstance(entry, dict):
                raise TypeError("invalid_permissions")
            group_id = self._validated_qq_id(entry.get("group_id"))
            if group_id in seen_groups:
                raise ValueError("invalid_permissions")
            seen_groups.add(group_id)
            admin_ids = self._normalized_qq_ids(entry.get("admin_ids"), maximum=100)
            if admin_ids:
                group_sub_admins.append({"group_id": group_id, "admin_ids": admin_ids})
        return {
            "plugin_admin_ids": plugin_admin_ids,
            "group_sub_admins": group_sub_admins,
        }

    @classmethod
    def _normalized_qq_ids(cls, value: Any, *, maximum: int) -> list[str]:
        if not isinstance(value, list) or len(value) > maximum:
            raise ValueError("invalid_permissions")
        result = []
        for item in value:
            qq_id = cls._validated_qq_id(item)
            if qq_id not in result:
                result.append(qq_id)
        return result

    @staticmethod
    def _validated_qq_id(value: Any) -> str:
        qq_id = str(value).strip()
        if not qq_id.isdigit() or not 5 <= len(qq_id) <= 20:
            raise ValueError("invalid_permissions")
        return qq_id

    @staticmethod
    def _bounded_text(value: Any, maximum: int) -> str:
        text = str(value or "").strip()
        if len(text) > maximum or "\x00" in text:
            raise ValueError("invalid_text")
        return text

    def _validated_filter_update(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values.get("enabled"), bool):
            raise TypeError("invalid_filters")
        result = {
            "enabled": values["enabled"],
            "contains": self._normalized_text_rules(values.get("contains"), 200),
            "exact": self._normalized_text_rules(values.get("exact"), 200),
            "regex": self._normalized_text_rules(values.get("regex"), 100),
            "component_types": self._normalized_text_rules(values.get("component_types"), 50),
            "sensitive": self._normalized_text_rules(values.get("sensitive"), 200),
            "blacklist_threshold": self._bounded_int(
                values.get("blacklist_threshold"), 5, 1, 1000000
            ),
            "blacklist_scope": (
                "group" if values.get("blacklist_scope") == "group" else "global"
            ),
            "regex_timeout_ms": self._bounded_int(values.get("regex_timeout_ms"), 50, 1, 1000),
            "group_rules": [],
        }
        raw_group_rules = values.get("group_rules", [])
        if not isinstance(raw_group_rules, list) or len(raw_group_rules) > 200:
            raise ValueError("invalid_filters")
        for pattern in result["regex"]:
            try:
                regex.compile(pattern)
            except regex.error as exc:
                raise ValueError("invalid_regex") from exc
        seen_groups = set()
        for entry in raw_group_rules:
            if not isinstance(entry, dict):
                raise TypeError("invalid_filters")
            group_id = str(entry.get("group_id", "")).strip()
            if not group_id.isdigit() or not 5 <= len(group_id) <= 20 or group_id in seen_groups:
                raise ValueError("invalid_filters")
            seen_groups.add(group_id)
            group_rule = {
                "group_id": group_id,
                "contains": self._normalized_text_rules(entry.get("contains"), 200),
                "exact": self._normalized_text_rules(entry.get("exact"), 200),
                "regex": self._normalized_text_rules(entry.get("regex"), 100),
                "component_types": self._normalized_text_rules(
                    entry.get("component_types"), 50
                ),
                "sensitive": self._normalized_text_rules(entry.get("sensitive"), 200),
            }
            for pattern in group_rule["regex"]:
                try:
                    regex.compile(pattern)
                except regex.error as exc:
                    raise ValueError("invalid_regex") from exc
            result["group_rules"].append(group_rule)
        return result

    @staticmethod
    def _normalized_text_rules(value: Any, maximum: int) -> list[str]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            text = str(item).strip()
            if text and len(text) <= 1000 and text not in result:
                result.append(text)
            if len(result) >= maximum:
                break
        return result

    def group_settings(self, group_id: str) -> dict[str, Any]:
        snapshot = self.snapshot()
        group_id = str(group_id)
        learning_groups = {str(item).strip() for item in snapshot["learning"]["group_ids"]}
        reply_groups = {str(item).strip() for item in snapshot["reply"]["group_ids"]}
        silent_groups = {
            str(item).strip() for item in snapshot["reply"]["silent_group_ids"]
        }
        learning = bool(snapshot["learning"]["enabled"]) and group_id in learning_groups
        reply = bool(snapshot["reply"]["enabled"]) and group_id in reply_groups
        if group_id in silent_groups and learning:
            mode = "silent"
        elif learning and reply:
            mode = "learning_reply"
        elif learning:
            mode = "learning"
        elif reply:
            mode = "reply"
        else:
            mode = "disabled"
        return {
            "group_id": group_id,
            "mode": mode,
            "learning_enabled": learning,
            "reply_enabled": reply and group_id not in silent_groups,
            "silent": group_id in silent_groups,
            "target_user_ids": list(self.learning_target_users_for(group_id)),
            "revision": self.revision,
        }

    async def update_group_settings(
        self,
        *,
        group_id: str,
        mode: str,
        target_user_ids: list[str],
        expected_revision: str,
    ) -> dict[str, Any]:
        if mode not in {"disabled", "learning", "reply", "learning_reply", "silent"}:
            raise ValueError("invalid_mode")
        if mode not in {"learning", "learning_reply", "silent"}:
            target_user_ids = []
        async with self._lock:
            if expected_revision != self.revision:
                raise ValueError("revision_conflict")
            original = deepcopy(self._source)
            try:
                learning = self._source.setdefault("learning", {})
                reply = self._source.setdefault("reply", {})
                learning_groups = self._replace_group_membership(
                    learning.get("group_ids", []),
                    group_id,
                    mode in {"learning", "learning_reply", "silent"},
                )
                reply_groups = self._replace_group_membership(
                    reply.get("group_ids", []),
                    group_id,
                    mode in {"reply", "learning_reply"},
                )
                silent_groups = self._replace_group_membership(
                    reply.get("silent_group_ids", []), group_id, mode == "silent"
                )
                learning["group_ids"] = learning_groups
                reply["group_ids"] = reply_groups
                reply["silent_group_ids"] = silent_groups
                if mode in {"learning", "learning_reply", "silent"}:
                    learning["enabled"] = True
                if mode in {"reply", "learning_reply"}:
                    reply["enabled"] = True
                targets = [
                    entry
                    for entry in learning.get("target_users", [])
                    if isinstance(entry, dict)
                    and str(entry.get("group_id", "")).strip() != group_id
                ]
                if target_user_ids:
                    targets.append({"group_id": group_id, "user_ids": target_user_ids})
                learning["target_users"] = targets
                await self._persist_source()
            except Exception:
                self._source.clear()
                self._source.update(original)
                raise
            return self.group_settings(group_id)

    async def _persist_source(self) -> None:
        save_async = getattr(self._source, "save_config_async", None)
        if callable(save_async):
            committed = await save_async()
            if committed is False:
                raise RuntimeError("config_save_superseded")
            return
        save = getattr(self._source, "save_config", None)
        if callable(save):
            await asyncio.to_thread(save)
            return
        raise RuntimeError("config_persistence_unavailable")

    @staticmethod
    def _replace_group_membership(values: Any, group_id: str, enabled: bool) -> list[str]:
        result = [
            str(value).strip()
            for value in values
            if str(value).strip() and str(value).strip() != group_id
        ] if isinstance(values, list) else []
        if enabled:
            result.append(group_id)
        return list(dict.fromkeys(result))

    @classmethod
    def _replace_group_memberships(
        cls, values: Any, group_ids: list[str], enabled: bool
    ) -> list[str]:
        result = cls._normalized_group_ids(values)
        requested = set(group_ids)
        result = [group_id for group_id in result if group_id not in requested]
        if enabled:
            result.extend(group_ids)
        return result

    @classmethod
    def _normalized_group_ids(cls, value: Any, *, maximum: int = 200) -> list[str]:
        if not isinstance(value, (list, tuple)) or len(value) > maximum:
            raise ValueError("invalid_cross_group_settings")
        result = []
        for item in value:
            try:
                group_id = cls._validated_qq_id(item)
            except ValueError as exc:
                raise ValueError("invalid_cross_group_settings") from exc
            if group_id not in result:
                result.append(group_id)
        return result

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
