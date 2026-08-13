from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

MESSAGE_SCHEMA_VERSION = 1


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalized_components_key(components: list[dict[str, Any]]) -> str:
    payload = canonical_json({"schema_version": MESSAGE_SCHEMA_VERSION, "components": components})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    platform: str
    group_id: str
    sender_id: str
    message_id: str
    timestamp: int
    components: tuple[dict[str, Any], ...]
    matching_components: tuple[dict[str, Any], ...]

    @property
    def components_json(self) -> str:
        return canonical_json(
            {
                "schema_version": MESSAGE_SCHEMA_VERSION,
                "components": list(self.components),
            }
        )

    @property
    def normalized_key(self) -> str:
        return normalized_components_key(list(self.matching_components))

    @property
    def is_empty(self) -> bool:
        return not self.components


@dataclass(frozen=True, slots=True)
class RecallNotice:
    platform: str
    group_id: str
    message_id: str
