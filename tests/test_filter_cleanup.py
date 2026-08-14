import asyncio
import json
import sqlite3
from pathlib import Path

from new_chat_learning.application.content_filter import ContentFilterService
from new_chat_learning.application.filter_cleanup import FilterCleanupService
from new_chat_learning.application.learning import LearningService
from new_chat_learning.domain.message import NormalizedMessage
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore


def message(message_id: str, timestamp: int, text: str):
    component = {"type": "Plain", "data": {"text": text}}
    return NormalizedMessage(
        platform="aiocqhttp",
        group_id="10001",
        sender_id="42",
        message_id=message_id,
        timestamp=timestamp,
        components=(component,),
        matching_components=(component,),
    )


async def seeded_service(tmp_path, config_source=None):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "runtime.sqlite3")
    await store.open()
    learning = LearningService(store, 900)
    await learning.observe(message("q1", 1000, "first question"))
    await learning.observe(message("a1", 1001, "blocked answer"))
    await learning.observe(message("q2", 2000, "second question"))
    await learning.observe(message("a2", 2001, "safe answer"))
    config = ConfigService(
        config_source
        or {"filters": {"enabled": True, "contains": ["blocked"], "sensitive": []}}
    )
    service = FilterCleanupService(
        data_dir,
        store,
        config,
        ContentFilterService(config),
    )
    return data_dir, store, config, service


def test_filter_cleanup_previews_without_message_content_and_applies_after_backup(tmp_path):
    async def scenario():
        data_dir, store, _config, service = await seeded_service(tmp_path)
        try:
            prepared = await service.prepare_cleanup(group_id="10001", actor_id="webui:test")
            answer_id = int(prepared["operations"][0]["answer_id"])
            question_id = int(prepared["operations"][0]["question_id"])
            await store.register_reply(
                platform="aiocqhttp",
                group_id="10001",
                sent_message_id="reply-1",
                answer_id=answer_id,
                question_id=question_id,
            )
            manifest_text = (data_dir / "temp" / "filter-cleanups" / f"{prepared['plan_id']}.json").read_text(
                encoding="utf-8"
            )
            applied = await service.apply_cleanup(
                plan_id=str(prepared["plan_id"]),
                group_id="10001",
                actor_id="webui:test",
            )
            remaining = [
                row[0]
                for row in store._require_connection().execute(
                    "SELECT components_json FROM answers ORDER BY id"
                )
            ]
            audit = store._require_connection().execute(
                "SELECT action, details_json FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            reply_state = store._require_connection().execute(
                "SELECT state FROM reply_records WHERE sent_message_id = 'reply-1'"
            ).fetchone()[0]
            return prepared, manifest_text, applied, remaining, tuple(audit), reply_state
        finally:
            await store.close()

    prepared, manifest_text, applied, remaining, audit, reply_state = asyncio.run(scenario())

    assert prepared["affected_answers"] == 1
    assert prepared["affected_questions"] == 1
    assert prepared["questions_becoming_empty"] == 1
    assert prepared["rule_type_counts"] == {"contains": 1}
    assert "blocked answer" not in manifest_text
    assert applied["deleted_answers"] == 1
    assert applied["orphan_questions"] == 1
    assert all("blocked answer" not in value for value in remaining)
    assert audit[0] == "cleanup_filtered_answers"
    assert "blocked answer" not in audit[1]
    assert reply_state == "deleted"
    backup = Path(applied["backup_path"])
    connection = sqlite3.connect(backup)
    try:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT COUNT(*) FROM answers WHERE components_json LIKE '%blocked answer%'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_filter_cleanup_rejects_answer_change_without_mutation(tmp_path):
    async def scenario():
        _data_dir, store, _config, service = await seeded_service(tmp_path)
        try:
            prepared = await service.prepare_cleanup(group_id="10001", actor_id="7")
            store._require_connection().execute(
                "UPDATE answers SET components_json = ? WHERE id = ?",
                (
                    json.dumps(
                        {
                            "schema_version": 1,
                            "components": [
                                {"type": "Plain", "data": {"text": "changed safe"}}
                            ],
                        }
                    ),
                    prepared["operations"][0]["answer_id"],
                ),
            )
            store._require_connection().commit()
            result = await service.apply_cleanup(
                plan_id=str(prepared["plan_id"]), group_id="10001", actor_id="7"
            )
            return result, await store.statistics()
        finally:
            await store.close()

    result, statistics = asyncio.run(scenario())
    assert result == {"applied": False, "reason": "plan_stale"}
    assert statistics["answers"] == 2
    assert list((tmp_path / "data" / "backups").glob("before-filter-cleanup-*.sqlite3")) == []


def test_filter_cleanup_rejects_config_revision_change(tmp_path):
    class Source(dict):
        async def save_config_async(self):
            return True

    async def scenario():
        source = Source(
            {"filters": {"enabled": True, "contains": ["blocked"], "sensitive": []}}
        )
        _data_dir, store, config, service = await seeded_service(tmp_path, source)
        try:
            prepared = await service.prepare_cleanup(group_id="10001", actor_id="7")
            source["filters"]["contains"] = ["safe"]
            result = await service.apply_cleanup(
                plan_id=str(prepared["plan_id"]), group_id="10001", actor_id="7"
            )
            return result, config.revision
        finally:
            await store.close()

    result, revision = asyncio.run(scenario())
    assert result == {"applied": False, "reason": "plan_stale"}
    assert revision


def test_filter_cleanup_plan_is_bound_to_group_and_actor(tmp_path):
    async def scenario():
        _data_dir, store, _config, service = await seeded_service(tmp_path)
        try:
            prepared = await service.prepare_cleanup(group_id="10001", actor_id="7")
            wrong_group = await service.apply_cleanup(
                plan_id=str(prepared["plan_id"]), group_id="10002", actor_id="7"
            )
            wrong_actor = await service.apply_cleanup(
                plan_id=str(prepared["plan_id"]), group_id="10001", actor_id="8"
            )
            return wrong_group, wrong_actor
        finally:
            await store.close()

    wrong_group, wrong_actor = asyncio.run(scenario())
    assert wrong_group == {"applied": False, "reason": "wrong_group"}
    assert wrong_actor == {"applied": False, "reason": "wrong_actor"}


def test_filter_cleanup_does_not_prepare_when_filters_are_disabled(tmp_path):
    async def scenario():
        _data_dir, store, _config, service = await seeded_service(
            tmp_path,
            {"filters": {"enabled": False, "contains": ["blocked"]}},
        )
        try:
            return await service.prepare_cleanup(group_id="10001", actor_id="7")
        finally:
            await store.close()

    assert asyncio.run(scenario()) == {
        "prepared": False,
        "reason": "no_filtered_answers",
    }


def test_scheduled_filter_cleanup_previews_or_backs_up_before_deletion(tmp_path):
    async def scenario():
        data_dir, store, _config, service = await seeded_service(tmp_path)
        try:
            preview = await service.run_scheduled(
                group_id="10001", actor_id="task:preview", apply=False
            )
            before_count = store._require_connection().execute(
                "SELECT COUNT(*) FROM answers"
            ).fetchone()[0]
            applied = await service.run_scheduled(
                group_id="10001", actor_id="task:apply", apply=True
            )
            after_count = store._require_connection().execute(
                "SELECT COUNT(*) FROM answers"
            ).fetchone()[0]
            audit = store._require_connection().execute(
                "SELECT actor_id, action, details_json FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return data_dir, preview, applied, before_count, after_count, tuple(audit)
        finally:
            await store.close()

    data_dir, preview, applied, before_count, after_count, audit = asyncio.run(scenario())
    assert preview["reason"] == "preview_only"
    assert preview["affected_answers"] == 1
    assert before_count == 2
    assert after_count == 1
    assert applied["deleted_answers"] == 1
    assert (data_dir / "backups" / applied["backup_name"]).is_file()
    assert audit[0:2] == ("task:apply", "cleanup_filtered_answers")
    assert "blocked answer" not in audit[2]
