from __future__ import annotations

import math
import random
import time
from collections import Counter
from collections.abc import Callable

import jieba
import regex

from new_chat_learning.application.content_filter import ContentFilterService
from new_chat_learning.domain.reply import QuestionCandidate, ReplyCandidate, ReplyDecision
from new_chat_learning.domain.reply_policy import classify_trigger_components
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore

jieba.setLogLevel(30)


class ReplyService:
    def __init__(
        self,
        store: SQLiteStore,
        config: ConfigService,
        content_filter: ContentFilterService | None = None,
        *,
        random_source: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.config = config
        self.content_filter = content_filter or ContentFilterService(config)
        self.random = random_source or random.Random()
        self.clock = clock
        self.wall_clock = wall_clock
        self._last_reply_at: dict[str, float] = {}
        self._last_share_reply_at: dict[str, float] = {}

    async def decide(
        self,
        group_id: str,
        normalized_key: str,
        *,
        plain_text: str = "",
        mentioned_bot: bool = False,
        trigger_components: tuple[dict[str, object], ...] = (),
    ) -> ReplyDecision:
        if not self.config.reply_enabled_for(group_id):
            return ReplyDecision(None, "disabled")

        trigger_type = classify_trigger_components(trigger_components)
        settings = self.config.reply_settings(group_id, trigger_type)
        now = self.clock()
        cooldown = float(settings["cooldown_seconds"])
        last_reply = self._last_reply_at.get(str(group_id))
        if last_reply is not None and now - last_reply < cooldown:
            return ReplyDecision(None, "cooldown")
        share_cooldowns = getattr(
            self.config, "share_reply_cooldowns_for", lambda _group_id: ()
        )(group_id)
        for share_name, share_cooldown in share_cooldowns:
            last_share_reply = self._last_share_reply_at.get(str(share_name))
            if (
                last_share_reply is not None
                and now - last_share_reply < float(share_cooldown)
            ):
                return ReplyDecision(None, "share_cooldown")

        available_groups = await self.store.list_question_group_ids()
        group_scopes = self.config.reply_library_scopes(group_id, available_groups)
        external_scopes = await self.store.external_library_scopes_for(group_id)
        scopes = (*group_scopes, *((scope,) for scope in external_scopes))
        selections: list[tuple[ReplyCandidate, str]] = []
        filtered_any = False
        for scope in scopes:
            exact_candidates = await self.store.find_exact_answers(scope, normalized_key)
            if exact_candidates:
                eligible = self._eligible_candidates(
                    exact_candidates, settings["type_frequency_thresholds"]
                )
                allowed, filtered = await self._filter_candidates(group_id, eligible)
                filtered_any = filtered_any or filtered
                selections.extend((candidate, "exact") for candidate in allowed)
                continue
            if not plain_text:
                continue
            plain_candidates = await self.store.find_plain_exact_answers(scope, plain_text)
            if plain_candidates:
                eligible = self._eligible_candidates(
                    plain_candidates, settings["type_frequency_thresholds"]
                )
                allowed, filtered = await self._filter_candidates(group_id, eligible)
                filtered_any = filtered_any or filtered
                selections.extend((candidate, "plain_exact") for candidate in allowed)
                continue
            question, reason = await self._find_fallback_question(
                scope,
                plain_text,
                settings,
            )
            if question is None:
                continue
            eligible = self._eligible_candidates(
                await self.store.find_answers_for_question(
                    scope,
                    question.normalized_key,
                ),
                settings["type_frequency_thresholds"],
            )
            allowed, filtered = await self._filter_candidates(group_id, eligible)
            filtered_any = filtered_any or filtered
            selections.extend((candidate, reason) for candidate in allowed)
        if not selections:
            return ReplyDecision(None, "filtered" if filtered_any else "no_match")

        force_reply = mentioned_bot and bool(settings["at_force_reply"])
        probability = float(settings["probability_percent"]) / 100.0
        if not force_reply and self.random.random() > probability:
            return ReplyDecision(None, "probability")

        repeat_candidates = [
            selection
            for selection in selections
            if selection[0].normalized_key == normalized_key
        ]
        if repeat_candidates:
            since = int(self.wall_clock()) - 3600
            repeat_count = await self.store.recent_repeat_reply_count(
                group_id=str(group_id),
                since=since,
            )
            if repeat_count >= 2:
                selections = [
                    selection
                    for selection in selections
                    if selection[0].normalized_key != normalized_key
                ]
                if not selections:
                    return ReplyDecision(None, "repeat_limit")

        candidate, reason = self.random.choices(
            selections,
            weights=[max(1, item.weight) for item, _reason in selections],
            k=1,
        )[0]
        base_wait = float(settings["wait_seconds"])
        jitter = float(settings["wait_jitter_seconds"])
        wait_seconds = max(0.0, base_wait + self.random.uniform(-jitter, jitter))
        return ReplyDecision(
            candidate,
            reason,
            wait_seconds,
            is_repeat=candidate.normalized_key == normalized_key,
        )

    def mark_sent(self, group_id: str) -> None:
        now = self.clock()
        self._last_reply_at[str(group_id)] = now
        share_cooldowns = getattr(
            self.config, "share_reply_cooldowns_for", lambda _group_id: ()
        )(group_id)
        for share_name, _cooldown in share_cooldowns:
            self._last_share_reply_at[str(share_name)] = now

    async def mark_repeat_sent(self, group_id: str) -> None:
        await self.store.register_repeat_reply(
            group_id=str(group_id),
            triggered_at=int(self.wall_clock()),
        )

    async def _filter_candidates(
        self,
        group_id: str,
        candidates: list[ReplyCandidate],
    ) -> tuple[list[ReplyCandidate], bool]:
        allowed = []
        filtered = False
        for candidate in candidates:
            match = self.content_filter.reply_match(group_id, candidate.components)
            if not match.matched:
                allowed.append(candidate)
                continue
            filtered = True
            await self.store.record_filter_hit(
                group_id=group_id,
                user_id="",
                rule_type=match.rule_type,
                direction="reply",
            )
        return allowed, filtered

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
            if score <= 0.0:
                continue
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
    "「",
    "」",
    "『",
    "』",
    "（",
    "）",
    "[",
    "]",
    "〔",
    "〕",
    "【",
    "】",
    "-",
    "—",
    "——",
    "…",
    "……",
    "～",
    "·",
    "《",
    "》",
    "〈",
    "〉",
    "﹏﹏",
    "___",
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
