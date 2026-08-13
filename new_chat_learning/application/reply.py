from __future__ import annotations

import math
import random
import time
from collections import Counter
from collections.abc import Callable

import jieba
import regex

from new_chat_learning.domain.reply import QuestionCandidate, ReplyCandidate, ReplyDecision
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore

jieba.setLogLevel(30)


class ReplyService:
    def __init__(
        self,
        store: SQLiteStore,
        config: ConfigService,
        *,
        random_source: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.config = config
        self.random = random_source or random.Random()
        self.clock = clock
        self._last_reply_at: dict[str, float] = {}

    async def decide(
        self,
        group_id: str,
        normalized_key: str,
        *,
        plain_text: str = "",
        mentioned_bot: bool = False,
    ) -> ReplyDecision:
        if not self.config.reply_enabled_for(group_id):
            return ReplyDecision(None, "disabled")

        settings = self.config.reply_settings()
        now = self.clock()
        cooldown = float(settings["cooldown_seconds"])
        last_reply = self._last_reply_at.get(str(group_id))
        if last_reply is not None and now - last_reply < cooldown:
            return ReplyDecision(None, "cooldown")

        available_groups = await self.store.list_question_group_ids()
        scopes = self.config.reply_library_scopes(group_id, available_groups)
        selections: list[tuple[ReplyCandidate, str]] = []
        for scope in scopes:
            exact_candidates = await self.store.find_exact_answers(scope, normalized_key)
            if exact_candidates:
                selections.extend(
                    (candidate, "exact")
                    for candidate in self._eligible_candidates(
                        exact_candidates, settings["type_frequency_thresholds"]
                    )
                )
                continue
            if not plain_text:
                continue
            question, reason = await self._find_fallback_question(
                scope,
                plain_text,
                settings,
            )
            if question is None:
                continue
            selections.extend(
                (candidate, reason)
                for candidate in self._eligible_candidates(
                    await self.store.find_answers_for_question(
                        scope,
                        question.normalized_key,
                    ),
                    settings["type_frequency_thresholds"],
                )
            )
        if not selections:
            return ReplyDecision(None, "no_match")

        force_reply = mentioned_bot and bool(settings["at_force_reply"])
        probability = float(settings["probability_percent"]) / 100.0
        if not force_reply and self.random.random() > probability:
            return ReplyDecision(None, "probability")

        candidate, reason = self.random.choices(
            selections,
            weights=[max(1, item.weight) for item, _reason in selections],
            k=1,
        )[0]
        base_wait = float(settings["wait_seconds"])
        jitter = float(settings["wait_jitter_seconds"])
        wait_seconds = max(0.0, base_wait + self.random.uniform(-jitter, jitter))
        return ReplyDecision(candidate, reason, wait_seconds)

    def mark_sent(self, group_id: str) -> None:
        self._last_reply_at[str(group_id)] = self.clock()

    async def _find_fallback_question(
        self,
        group_ids: tuple[str, ...],
        plain_text: str,
        settings: dict[str, object],
    ) -> tuple[QuestionCandidate | None, str]:
        questions = await self.store.find_matchable_questions(group_ids)
        if settings["regex_enabled"]:
            timeout = float(settings["regex_timeout_ms"]) / 1000.0
            for question in questions:
                if not question.is_regex:
                    continue
                try:
                    if regex.search(question.plain_text, plain_text, timeout=timeout) is not None:
                        return question, "regex"
                except (regex.error, TimeoutError):
                    continue

        max_length = int(settings["similarity_max_length"])
        if not settings["similarity_enabled"] or len(plain_text) > max_length:
            return None, "no_match"
        threshold = float(settings["similarity_threshold"])
        best_question = None
        best_score = threshold
        for question in questions:
            if question.is_regex or len(question.plain_text) > max_length:
                continue
            score = cosine_similarity(question.plain_text, plain_text)
            if score >= threshold and (best_question is None or score > best_score):
                best_question = question
                best_score = score
        return best_question, "similarity"

    @staticmethod
    def _eligible_candidates(
        candidates: list[ReplyCandidate],
        thresholds: object,
    ) -> list[ReplyCandidate]:
        if not isinstance(thresholds, dict) or not thresholds:
            return candidates
        eligible = []
        for candidate in candidates:
            component_types = {
                str(component.get("type", "")).lower() for component in candidate.components
            }
            if all(
                candidate.weight >= int(thresholds.get(component_type, 0))
                or candidate.answer_question_frequency >= int(thresholds.get(component_type, 0))
                for component_type in component_types
            ):
                eligible.append(candidate)
        return eligible


PUNCTUATION = {
    "。",
    "，",
    "、",
    "？",
    "?",
    "！",
    "!",
    "；",
    "：",
    "“",
    "”",
    "‘",
    "’",
    "（",
    "）",
    "[",
    "]",
    "【",
    "】",
    "-",
    "—",
    "…",
    "～",
    "·",
    "《",
    "》",
    ".",
}


def cosine_similarity(first: str, second: str) -> float:
    first_counts = Counter(word for word in jieba.cut(first.strip()) if word not in PUNCTUATION)
    second_counts = Counter(word for word in jieba.cut(second.strip()) if word not in PUNCTUATION)
    if not first_counts or not second_counts:
        return 0.0
    common = first_counts.keys() & second_counts.keys()
    numerator = sum(first_counts[word] * second_counts[word] for word in common)
    first_norm = math.sqrt(sum(value * value for value in first_counts.values()))
    second_norm = math.sqrt(sum(value * value for value in second_counts.values()))
    return numerator / (first_norm * second_norm) if first_norm and second_norm else 0.0
