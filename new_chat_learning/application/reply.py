from __future__ import annotations

import random
import time
from collections.abc import Callable

from new_chat_learning.domain.reply import ReplyDecision
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore


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

        candidates = await self.store.find_exact_answers(group_id, normalized_key)
        if not candidates:
            return ReplyDecision(None, "no_match")

        force_reply = mentioned_bot and bool(settings["at_force_reply"])
        probability = float(settings["probability_percent"]) / 100.0
        if not force_reply and self.random.random() > probability:
            return ReplyDecision(None, "probability")

        candidate = self.random.choices(
            candidates,
            weights=[max(1, item.weight) for item in candidates],
            k=1,
        )[0]
        base_wait = float(settings["wait_seconds"])
        jitter = float(settings["wait_jitter_seconds"])
        wait_seconds = max(0.0, base_wait + self.random.uniform(-jitter, jitter))
        return ReplyDecision(candidate, "exact", wait_seconds)

    def mark_sent(self, group_id: str) -> None:
        self._last_reply_at[str(group_id)] = self.clock()
