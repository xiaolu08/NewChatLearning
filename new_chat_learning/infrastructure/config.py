from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "general": {"enabled": True, "legacy_command_aliases": True},
    "permissions": {"plugin_admin_ids": []},
    "storage": {"media_quota_gb": 10.0},
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
