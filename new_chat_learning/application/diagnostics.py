from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore


class RuntimeDiagnostics:
    """In-memory counters that intentionally retain no message or sender data."""

    _COUNTERS = (
        "normalized_messages",
        "accepted_learning_messages",
        "learned_pairs",
        "duplicates",
        "chain_resets",
        "recalls_received",
        "pending_recalls_removed",
        "reply_decisions",
        "successful_sends",
    )

    def __init__(self) -> None:
        self._groups: dict[str, Counter[str]] = defaultdict(Counter)
        self._reply_reasons: dict[str, Counter[str]] = defaultdict(Counter)
        self._last_activity: dict[str, str] = {}
        self.started_at = datetime.now(timezone.utc).isoformat()

    def record(self, group_id: str, event: str, *, reason: str = "") -> None:
        group_id = str(group_id).strip()
        if not group_id or event not in self._COUNTERS:
            return
        self._groups[group_id][event] += 1
        if event == "reply_decisions" and reason:
            self._reply_reasons[group_id][str(reason)] += 1
        self._last_activity[group_id] = datetime.now(timezone.utc).isoformat()

    async def snapshot(
        self,
        store: SQLiteStore,
        config: ConfigService,
    ) -> dict[str, Any]:
        database_groups = await store.diagnostic_group_summaries()
        group_ids = set(database_groups) | set(config.configured_group_ids()) | set(self._groups)
        groups = []
        for group_id in sorted(group_ids):
            counters = self._groups[group_id]
            runtime = {name: int(counters[name]) for name in self._COUNTERS}
            runtime["reply_reasons"] = dict(sorted(self._reply_reasons[group_id].items()))
            runtime["last_activity_at"] = self._last_activity.get(group_id)
            groups.append(
                {
                    "group_id": group_id,
                    "mode": config.group_settings(group_id)["mode"],
                    "runtime": runtime,
                    "database": database_groups.get(
                        group_id,
                        {
                            "questions": 0,
                            "answers": 0,
                            "answer_weight": 0,
                            "pending_messages": 0,
                            "active_replies": 0,
                            "media_assets": 0,
                        },
                    ),
                }
            )
        return {
            "runtime_started_at": self.started_at,
            "runtime_counters_reset_on_reload": True,
            "groups": groups,
        }
