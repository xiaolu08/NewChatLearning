from __future__ import annotations

from dataclasses import dataclass

from new_chat_learning.domain.message import NormalizedMessage, RecallNotice
from new_chat_learning.infrastructure.database import SQLiteStore


@dataclass(frozen=True, slots=True)
class LearningResult:
    accepted: bool
    learned_pair: bool = False
    chain_reset: bool = False
    recalled_pending: bool = False
    duplicate: bool = False


class LearningService:
    def __init__(self, store: SQLiteStore, interval_seconds: int) -> None:
        self.store = store
        self.interval_seconds = interval_seconds

    async def observe(self, message: NormalizedMessage) -> LearningResult:
        result = await self.store.observe_message(message, self.interval_seconds)
        return LearningResult(accepted=not result["duplicate"], **result)

    async def recall(self, notice: RecallNotice) -> LearningResult:
        removed = await self.store.remove_pending_message(
            notice.platform, notice.group_id, notice.message_id
        )
        return LearningResult(accepted=removed, recalled_pending=removed)
