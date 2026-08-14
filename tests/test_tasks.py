from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from new_chat_learning.application.tasks import ScheduledTaskService
from new_chat_learning.infrastructure.database import SQLiteStore


class FakeMedia:
    def __init__(self) -> None:
        self.calls = 0
        self.fail_once = False

    async def scan_group(self, group_id: str):
        self.calls += 1
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("scan failed")
        return {
            "group_id": group_id,
            "scanned_answers": 3,
            "scanned_components": 2,
            "preview": {"media_components": 1, "affected_answers": 1},
        }


class FakeFilterCleanup:
    async def run_scheduled(self, **_kwargs):
        return {"applied": False, "reason": "preview_only", "affected_answers": 2}


class FakeExport:
    def cleanup_expired(self) -> int:
        return 0


class FakeMigration:
    def cleanup_expired(self) -> int:
        return 0


def _service(tmp_path, store, media=None, *, poll_seconds=30.0):
    return ScheduledTaskService(
        tmp_path,
        store,
        media or FakeMedia(),
        FakeFilterCleanup(),
        FakeExport(),
        FakeMigration(),
        poll_seconds=poll_seconds,
    )


async def _save_media_task(service, *, enabled: bool):
    return await service.save_task(
        name="媒体扫描",
        task_type="media_scan",
        group_id="10001",
        enabled=enabled,
        interval_minutes=15,
        actor_id="webui:test",
    )


def test_scheduler_starts_and_stops_cleanly(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "database.sqlite3")
        await store.open()
        service = _service(tmp_path, store, poll_seconds=3600)
        await service.start()
        loop_task = service._loop_task
        assert loop_task is not None and not loop_task.done()
        await service.stop()
        assert loop_task.done()
        await store.close()

    asyncio.run(scenario())


def test_disabled_task_never_executes(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "database.sqlite3")
        await store.open()
        media = FakeMedia()
        service = _service(tmp_path, store, media)
        await _save_media_task(service, enabled=False)
        executed = await service.run_due_once()
        tasks = await service.list_tasks()
        await store.close()
        return executed, media.calls, tasks

    executed, calls, tasks = asyncio.run(scenario())
    assert executed == 0
    assert calls == 0
    assert tasks[0]["last_status"] == "never"


def test_due_task_executes_once_and_advances_schedule(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "database.sqlite3")
        await store.open()
        media = FakeMedia()
        service = _service(tmp_path, store, media)
        task = await _save_media_task(service, enabled=True)
        connection = store._require_connection()
        connection.execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE task_id = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), task["task_id"]),
        )
        connection.commit()
        first = await service.run_due_once()
        second = await service.run_due_once()
        tasks = await service.list_tasks()
        await store.close()
        return first, second, media.calls, tasks[0]

    first, second, calls, task = asyncio.run(scenario())
    assert (first, second, calls) == (1, 0, 1)
    assert task["last_status"] == "success"
    assert len(task["history"]) == 1
    assert task["history"][0]["summary"]["scanned_answers"] == 3


def test_task_failure_is_recorded_and_next_run_can_succeed(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "database.sqlite3")
        await store.open()
        media = FakeMedia()
        media.fail_once = True
        service = _service(tmp_path, store, media)
        task = await _save_media_task(service, enabled=True)
        connection = store._require_connection()
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        connection.execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE task_id = ?",
            (past, task["task_id"]),
        )
        connection.commit()
        await service.run_due_once()
        connection.execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE task_id = ?",
            (past, task["task_id"]),
        )
        connection.commit()
        await service.run_due_once()
        result = (await service.list_tasks())[0]
        await store.close()
        return result

    task = asyncio.run(scenario())
    assert [entry["status"] for entry in task["history"]] == ["success", "failed"]
    assert task["last_status"] == "success"


def test_destructive_task_requires_confirmation_for_save_and_run(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "database.sqlite3")
        await store.open()
        service = _service(tmp_path, store)
        with pytest.raises(ValueError, match="destructive_confirmation_required"):
            await service.save_task(
                name="自动清理",
                task_type="filter_cleanup",
                group_id="10001",
                enabled=True,
                interval_minutes=1440,
                cleanup_mode="apply",
                actor_id="webui:test",
            )
        task = await service.save_task(
            name="自动清理",
            task_type="filter_cleanup",
            group_id="10001",
            enabled=True,
            interval_minutes=1440,
            cleanup_mode="apply",
            confirmed=True,
            actor_id="webui:test",
        )
        with pytest.raises(ValueError, match="destructive_confirmation_required"):
            await service.run_now(
                task_id=task["task_id"], confirmed=False, actor_id="webui:test"
            )
        result = await service.run_now(
            task_id=task["task_id"], confirmed=True, actor_id="webui:test"
        )
        await store.close()
        return result

    result = asyncio.run(scenario())
    assert result["status"] == "success"


def test_restart_marks_claimed_run_interrupted_without_making_it_due(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "database.sqlite3")
        await store.open()
        service = _service(tmp_path, store)
        task = await _save_media_task(service, enabled=True)
        started = datetime.now(timezone.utc)
        claimed = await store.claim_scheduled_task(
            task_id=task["task_id"],
            trigger_type="scheduled",
            started_at=started.isoformat(),
            next_run_at=(started + timedelta(minutes=15)).isoformat(),
            require_due=False,
        )
        assert claimed is not None
        await store.close()

        reopened = SQLiteStore(tmp_path / "database.sqlite3")
        await reopened.open()
        restarted = _service(tmp_path, reopened, poll_seconds=3600)
        await restarted.start()
        try:
            tasks = await restarted.list_tasks()
            due = await restarted.run_due_once()
        finally:
            await restarted.stop()
            await reopened.close()
        return tasks[0], due

    task, due = asyncio.run(scenario())
    assert task["last_status"] == "interrupted"
    assert task["history"][0]["status"] == "interrupted"
    assert due == 0
