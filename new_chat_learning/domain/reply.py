from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReplyCandidate:
    answer_id: int
    question_id: int
    weight: int
    answer_question_frequency: int
    components_json: str

    @property
    def components(self) -> tuple[dict[str, Any], ...]:
        payload = json.loads(self.components_json)
        components = payload.get("components", [])
        if not isinstance(components, list):
            return ()
        return tuple(item for item in components if isinstance(item, dict))


@dataclass(frozen=True, slots=True)
class ReplyDecision:
    candidate: ReplyCandidate | None
    reason: str
    wait_seconds: float = 0.0

    @property
    def should_reply(self) -> bool:
        return self.candidate is not None


@dataclass(frozen=True, slots=True)
class QuestionCandidate:
    question_id: int
    plain_text: str
    is_regex: bool
