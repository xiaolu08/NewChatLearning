from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from new_chat_learning.application.export import LibraryExportService
from new_chat_learning.application.filter_cleanup import FilterCleanupService
from new_chat_learning.application.media import MediaService
from new_chat_learning.application.migration import MigrationService
from new_chat_learning.domain.message import canonical_json
from new_chat_learning.infrastructure.database import SQLiteStore

logger = logging.getLogger(__name__)

TASK_TYPES = {
    "media_scan",
    "database_backup",
    "artifact_cleanup",
    "filter_cleanup",
}
GROUP_TASK_TYPES = {"media_scan", "filter_cleanup"}
MIN_INTERVAL_MINUTES = 15
MAX_INTERVAL_MINUTES = 43_200


class ScheduledTaskService:
    def __init__(
        self,
        data_dir: Path,
        store: SQLiteStore,
        media: MediaService,
        filter_cleanup: FilterCleanupService,
        export: LibraryExportService,
        migration: MigrationService,
        *,
        poll_seconds: float = 30.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.store = store
        self.media = media
        self.filter_cleanup = filter_cleanup
        self.export = export
        self.migration = migration
        self.poll_seconds = max(0.05, float(poll_seconds))
        self._loop_task: asyncio.Task[None] | None = None
        self._execution_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        await self.store.recover_interrupted_scheduled_tasks(_now_iso())
        self._loop_task = asyncio.create_task(
            self._scheduler_loop(), name="new-chat-learning-scheduler"
        )

    async def stop(self) -> None:
        task = self._loop_task
        self._loop_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def list_tasks(self) -> list[dict[str, Any]]:
        tasks = await self.store.scheduled_tasks(history_limit=20)
        return [self._public_task(task) for task in tasks]

    async def save_task(
        self,
        *,
        task_id: str = "",
        name: str,
        task_type: str,
        group_id: str,
        enabled: bool,
        interval_minutes: int,
        cleanup_mode: str = "preview",
        expected_revision: int | None = None,
        confirmed: bool = False,
        actor_id: str,
    ) -> dict[str, Any]:
        values = self._validated_values(
            name=name,
            task_type=task_type,
            group_id=group_id,
            enabled=enabled,
            interval_minutes=interval_minutes,
            cleanup_mode=cleanup_mode,
        )
        if values["destructive"] and not confirmed:
            raise ValueError("destructive_confirmation_required")
        if task_id:
            if not _valid_task_id(task_id):
                raise ValueError("invalid_task_id")
            existing = await self.store.scheduled_task(task_id)
            if existing is None:
                raise ValueError("task_not_found")
            if expected_revision is None:
                raise ValueError("task_revision_required")
        else:
            task_id = secrets.token_hex(16)
            expected_revision = None
        now = datetime.now(timezone.utc)
        next_run = _iso(now + timedelta(minutes=values["interval_minutes"])) if enabled else None
        result = await self.store.save_scheduled_task(
            task_id=task_id,
            name=values["name"],
            task_type=values["task_type"],
            group_id=values["group_id"],
            enabled=enabled,
            interval_minutes=values["interval_minutes"],
            cleanup_mode=values["cleanup_mode"],
            expected_revision=expected_revision,
            now=_iso(now),
            next_run_at=next_run,
        )
        await self.store.record_audit(
            actor_id=actor_id,
            action="update_scheduled_task",
            target=f"task:{task_id}",
            details={
                "task_type": values["task_type"],
                "group_id": values["group_id"],
                "enabled": enabled,
                "interval_minutes": values["interval_minutes"],
                "cleanup_mode": values["cleanup_mode"],
                "destructive": values["destructive"],
            },
        )
        return self._public_task(result)

    async def delete_task(
        self,
        *,
        task_id: str,
        expected_revision: int,
        confirmed: bool,
        actor_id: str,
    ) -> None:
        if not confirmed:
            raise ValueError("confirmation_required")
        if not _valid_task_id(task_id):
            raise ValueError("invalid_task_id")
        await self.store.delete_scheduled_task(
            task_id=task_id, expected_revision=expected_revision
        )
        await self.store.record_audit(
            actor_id=actor_id,
            action="delete_scheduled_task",
            target=f"task:{task_id}",
            details={"result": "deleted"},
        )

    async def run_now(
        self,
        *,
        task_id: str,
        confirmed: bool,
        actor_id: str,
    ) -> dict[str, Any]:
        task = await self.store.scheduled_task(task_id)
        if task is None or not _valid_task_id(task_id):
            raise ValueError("task_not_found")
        destructive = task["task_type"] == "filter_cleanup" and task["cleanup_mode"] == "apply"
        if destructive and not confirmed:
            raise ValueError("destructive_confirmation_required")
        result = await self._claim_and_execute(
            task_id=task_id,
            trigger_type="manual",
            require_due=False,
            actor_id=actor_id,
        )
        if result is None:
            raise ValueError("task_running")
        return result

    async def run_due_once(self) -> int:
        now = _now_iso()
        task_ids = await self.store.due_scheduled_task_ids(now)
        executed = 0
        for task_id in task_ids:
            result = await self._claim_and_execute(
                task_id=task_id,
                trigger_type="scheduled",
                require_due=True,
                actor_id=f"task:{task_id}",
            )
            executed += int(result is not None)
        return executed

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self.run_due_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NewChatLearning scheduled task loop failed.")
            await asyncio.sleep(self.poll_seconds)

    async def _claim_and_execute(
        self,
        *,
        task_id: str,
        trigger_type: str,
        require_due: bool,
        actor_id: str,
    ) -> dict[str, Any] | None:
        async with self._execution_lock:
            current = await self.store.scheduled_task(task_id)
            if current is None:
                return None
            started = datetime.now(timezone.utc)
            next_run = (
                _iso(started + timedelta(minutes=int(current["interval_minutes"])))
                if require_due
                else None
            )
            claimed = await self.store.claim_scheduled_task(
                task_id=task_id,
                trigger_type=trigger_type,
                started_at=_iso(started),
                next_run_at=next_run,
                require_due=require_due,
            )
            if claimed is None:
                return None
            task, run_id = claimed
            status = "success"
            try:
                summary = await self._execute(task, actor_id=actor_id)
            except asyncio.CancelledError:
                status = "interrupted"
                summary = {"reason": "plugin_stopped"}
                raise
            except Exception as error:
                logger.exception("Scheduled task %s failed.", task_id)
                status = "failed"
                summary = {"reason": type(error).__name__}
            finally:
                finished = _now_iso()
                await self.store.finish_scheduled_task_run(
                    task_id=task_id,
                    run_id=run_id,
                    status=status,
                    finished_at=finished,
                    summary_json=canonical_json(_safe_summary(summary)),
                )
            await self.store.record_audit(
                actor_id=actor_id,
                action="run_scheduled_task",
                target=f"task:{task_id}",
                details={
                    "task_type": str(task["task_type"]),
                    "group_id": str(task["group_id"]),
                    "trigger_type": trigger_type,
                    "result": status,
                },
            )
            return {"task_id": task_id, "status": status, "summary": _safe_summary(summary)}

    async def _execute(self, task: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        task_type = str(task["task_type"])
        group_id = str(task["group_id"])
        if task_type == "media_scan":
            result = await self.media.scan_group(group_id)
            preview = result.get("preview", {})
            return {
                "scanned_answers": int(result.get("scanned_answers", 0)),
                "scanned_components": int(result.get("scanned_components", 0)),
                "invalid_components": int(preview.get("media_components", 0)),
                "affected_answers": int(preview.get("affected_answers", 0)),
            }
        if task_type == "database_backup":
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.data_dir / "backups" / f"scheduled-backup-{timestamp}-{task['task_id'][:12]}.sqlite3"
            await self.store.backup_to(backup)
            return {"backup_name": backup.name}
        if task_type == "artifact_cleanup":
            exports_removed, migrations_removed = await asyncio.gather(
                asyncio.to_thread(self.export.cleanup_expired),
                asyncio.to_thread(self.migration.cleanup_expired),
            )
            return {
                "exports_removed": int(exports_removed),
                "migration_records_removed": int(migrations_removed),
            }
        if task_type == "filter_cleanup":
            result = await self.filter_cleanup.run_scheduled(
                group_id=group_id,
                actor_id=actor_id,
                apply=str(task["cleanup_mode"]) == "apply",
            )
            return _safe_summary(result)
        raise ValueError("unsupported_task_type")

    @staticmethod
    def _validated_values(
        *,
        name: str,
        task_type: str,
        group_id: str,
        enabled: bool,
        interval_minutes: int,
        cleanup_mode: str,
    ) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise TypeError("invalid_task")
        name = str(name).strip()
        if not name or len(name) > 80 or any(value in name for value in "\r\n\x00"):
            raise ValueError("invalid_task_name")
        task_type = str(task_type).strip()
        if task_type not in TASK_TYPES:
            raise ValueError("invalid_task_type")
        try:
            interval_minutes = int(interval_minutes)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid_task_interval") from error
        if not MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES:
            raise ValueError("invalid_task_interval")
        group_id = str(group_id).strip()
        if task_type in GROUP_TASK_TYPES:
            if not group_id.isdigit() or not 5 <= len(group_id) <= 20:
                raise ValueError("invalid_task_group")
        else:
            group_id = ""
        cleanup_mode = str(cleanup_mode).strip()
        if task_type == "filter_cleanup":
            if cleanup_mode not in {"preview", "apply"}:
                raise ValueError("invalid_cleanup_mode")
        else:
            cleanup_mode = "preview"
        return {
            "name": name,
            "task_type": task_type,
            "group_id": group_id,
            "enabled": enabled,
            "interval_minutes": interval_minutes,
            "cleanup_mode": cleanup_mode,
            "destructive": task_type == "filter_cleanup" and cleanup_mode == "apply",
        }

    @staticmethod
    def _public_task(task: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: task.get(key)
            for key in (
                "task_id",
                "name",
                "task_type",
                "group_id",
                "enabled",
                "interval_minutes",
                "cleanup_mode",
                "revision",
                "next_run_at",
                "last_started_at",
                "last_completed_at",
                "last_status",
                "created_at",
                "updated_at",
            )
        }
        result["destructive"] = (
            result["task_type"] == "filter_cleanup" and result["cleanup_mode"] == "apply"
        )
        history = []
        for entry in task.get("history", []):
            try:
                summary = json.loads(str(entry.get("summary_json", "{}")))
            except (TypeError, ValueError):
                summary = {}
            history.append(
                {
                    "id": int(entry["id"]),
                    "trigger_type": str(entry["trigger_type"]),
                    "status": str(entry["status"]),
                    "started_at": str(entry["started_at"]),
                    "finished_at": entry.get("finished_at"),
                    "summary": _safe_summary(summary),
                }
            )
        result["history"] = history
        return result


def _safe_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "affected_answers",
        "affected_questions",
        "backup_name",
        "deleted_answers",
        "exports_removed",
        "invalid_components",
        "migration_records_removed",
        "orphan_questions",
        "questions_becoming_empty",
        "reason",
        "removed_components",
        "rule_type_counts",
        "scanned_answers",
        "scanned_components",
        "updated_answers",
    }
    result = {}
    for key, item in value.items():
        if key not in allowed:
            continue
        if isinstance(item, bool | int | float) or (
            isinstance(item, str)
            and len(item) <= 160
            and "/" not in item
            and "\\" not in item
        ):
            result[key] = item
        elif isinstance(item, dict) and len(item) <= 20:
            result[key] = {
                str(name): int(count)
                for name, count in item.items()
                if len(str(name)) <= 40 and isinstance(count, int)
            }
    return result


def _valid_task_id(value: str) -> bool:
    return len(value) == 32 and all(character in "0123456789abcdef" for character in value)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))
