from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from new_chat_learning.application.content_filter import ContentFilterService
from new_chat_learning.domain.message import canonical_json
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore


class FilterCleanupService:
    def __init__(
        self,
        data_dir: Path,
        store: SQLiteStore,
        config: ConfigService,
        content_filter: ContentFilterService,
    ) -> None:
        self.cleanup_dir = Path(data_dir) / "temp" / "filter-cleanups"
        self.backup_dir = Path(data_dir) / "backups"
        self.store = store
        self.config = config
        self.content_filter = content_filter
        self._lock = asyncio.Lock()

    async def prepare_cleanup(self, *, group_id: str, actor_id: str) -> dict[str, object]:
        async with self._lock:
            operations, summary = await self._matched_operations(str(group_id))
            if not operations:
                return {"prepared": False, "reason": "no_filtered_answers"}
            plan_id = secrets.token_hex(16)
            created_at = datetime.now(timezone.utc)
            manifest = {
                "plan_id": plan_id,
                "status": "prepared",
                "group_id": str(group_id),
                "actor_id": str(actor_id),
                "config_revision": self.config.revision,
                "created_at": created_at.isoformat(),
                "expires_at": (created_at + timedelta(hours=1)).isoformat(),
                **summary,
                "operations": operations,
            }
            self.cleanup_dir.mkdir(parents=True, exist_ok=True)
            path = self.cleanup_dir / f"{plan_id}.json"
            await asyncio.to_thread(path.write_text, canonical_json(manifest), "utf-8")
            return {"prepared": True, **manifest}

    async def apply_cleanup(
        self,
        *,
        plan_id: str,
        group_id: str,
        actor_id: str,
    ) -> dict[str, object]:
        if len(plan_id) != 32 or any(value not in "0123456789abcdef" for value in plan_id):
            return {"applied": False, "reason": "plan_not_found"}
        async with self._lock:
            path = self.cleanup_dir / f"{plan_id}.json"
            try:
                manifest = json.loads(await asyncio.to_thread(path.read_text, "utf-8"))
            except (OSError, TypeError, ValueError):
                return {"applied": False, "reason": "plan_not_found"}
            error = self._manifest_error(manifest, group_id, actor_id)
            if error:
                return {"applied": False, "reason": error}
            if str(manifest.get("config_revision")) != self.config.revision:
                return {"applied": False, "reason": "plan_stale"}
            try:
                async with self.config.revision_guard(
                    str(manifest.get("config_revision"))
                ):
                    operations, _summary = await self._matched_operations(str(group_id))
                    if operations != manifest.get("operations"):
                        return {"applied": False, "reason": "plan_stale"}
                    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    backup_path = (
                        self.backup_dir
                        / f"before-filter-cleanup-{timestamp}-{plan_id[:12]}.sqlite3"
                    )
                    await self.store.backup_to(backup_path)
                    result = await self.store.apply_filter_cleanup(
                        plan_id=plan_id,
                        group_id=str(group_id),
                        actor_id=str(actor_id),
                        config_revision=self.config.revision,
                        operations=operations,
                    )
            except ValueError as error:
                if str(error) in {"cleanup_plan_stale", "revision_conflict"}:
                    return {
                        "applied": False,
                        "reason": "plan_stale",
                        **(
                            {"backup_path": str(backup_path)}
                            if "backup_path" in locals()
                            else {}
                        ),
                    }
                raise
            manifest["status"] = "applied"
            manifest["applied_at"] = datetime.now(timezone.utc).isoformat()
            manifest["backup_name"] = backup_path.name
            await asyncio.to_thread(path.write_text, canonical_json(manifest), "utf-8")
            return {**result, "backup_path": str(backup_path)}

    async def run_scheduled(
        self,
        *,
        group_id: str,
        actor_id: str,
        apply: bool,
    ) -> dict[str, object]:
        """Preview or execute a fixed-policy cleanup without a reusable WebUI plan."""
        async with self._lock:
            operations, summary = await self._matched_operations(str(group_id))
            if not operations:
                return {"applied": False, "reason": "no_filtered_answers", **summary}
            if not apply:
                return {"applied": False, "reason": "preview_only", **summary}
            config_revision = self.config.revision
            async with self.config.revision_guard(config_revision):
                current_operations, current_summary = await self._matched_operations(
                    str(group_id)
                )
                if current_operations != operations:
                    return {"applied": False, "reason": "candidates_changed", **current_summary}
                plan_id = secrets.token_hex(16)
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_path = (
                    self.backup_dir
                    / f"before-filter-cleanup-{timestamp}-{plan_id[:12]}.sqlite3"
                )
                await self.store.backup_to(backup_path)
                result = await self.store.apply_filter_cleanup(
                    plan_id=plan_id,
                    group_id=str(group_id),
                    actor_id=str(actor_id),
                    config_revision=config_revision,
                    operations=current_operations,
                )
            return {
                **result,
                "backup_name": backup_path.name,
                "rule_type_counts": current_summary["rule_type_counts"],
            }

    async def _matched_operations(
        self, group_id: str
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        operations = []
        rule_types: Counter[str] = Counter()
        question_totals: dict[int, int] = {}
        matched_questions: Counter[int] = Counter()
        after_answer_id = 0
        while True:
            rows = await self.store.answer_component_batch(
                group_id=group_id,
                after_answer_id=after_answer_id,
            )
            if not rows:
                break
            for row in rows:
                after_answer_id = int(row["answer_id"])
                question_id = int(row["question_id"])
                question_totals[question_id] = int(row["question_answer_count"])
                components_json = str(row["components_json"])
                match = self.content_filter.reply_match(
                    group_id, self._components(components_json)
                )
                if not match.matched:
                    continue
                rule_types[match.rule_type] += 1
                matched_questions[question_id] += 1
                operations.append(
                    {
                        "answer_id": int(row["answer_id"]),
                        "question_id": question_id,
                        "components_sha256": hashlib.sha256(
                            components_json.encode("utf-8")
                        ).hexdigest(),
                        "rule_type": match.rule_type,
                    }
                )
        return operations, {
            "affected_answers": len(operations),
            "affected_questions": len(matched_questions),
            "questions_becoming_empty": sum(
                int(count == question_totals[question_id])
                for question_id, count in matched_questions.items()
            ),
            "rule_type_counts": dict(sorted(rule_types.items())),
        }

    @staticmethod
    def _components(components_json: str) -> tuple[dict, ...]:
        try:
            payload = json.loads(components_json)
        except (TypeError, ValueError):
            return ()
        components = payload.get("components", []) if isinstance(payload, dict) else []
        return tuple(item for item in components if isinstance(item, dict))

    @staticmethod
    def _manifest_error(manifest: dict, group_id: str, actor_id: str) -> str:
        if manifest.get("status") != "prepared":
            return "plan_not_ready"
        if str(manifest.get("group_id")) != str(group_id):
            return "wrong_group"
        if str(manifest.get("actor_id")) != str(actor_id):
            return "wrong_actor"
        try:
            expires_at = datetime.fromisoformat(str(manifest["expires_at"]))
        except (KeyError, TypeError, ValueError):
            return "invalid_plan"
        return "plan_expired" if datetime.now(timezone.utc) >= expires_at else ""
