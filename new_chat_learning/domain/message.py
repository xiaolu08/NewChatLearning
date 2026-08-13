from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

MESSAGE_SCHEMA_VERSION = 1


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
        payload = canonical_json(
            {
                "schema_version": MESSAGE_SCHEMA_VERSION,
                "components": list(self.matching_components),
            }
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def is_empty(self) -> bool:
        return not self.components


@dataclass(frozen=True, slots=True)
class RecallNotice:
    platform: str
    group_id: str
    message_id: str
