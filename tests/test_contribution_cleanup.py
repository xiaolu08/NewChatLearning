import asyncio
import json
import sqlite3

from new_chat_learning.application.contribution_cleanup import ContributionCleanupService
from new_chat_learning.infrastructure.database import SQLiteStore


def _seed_contributions(store):
    connection = store._require_connection()
    connection.execute(
        "INSERT INTO questions(id, group_id, normalized_key, components_json) "
        "VALUES(1, '10001', 'shared-question', '{}')"
    )
    connection.execute(
        "INSERT INTO answers(id, question_id, normalized_key, components_json, weight) "
        "VALUES(10, 1, 'shared-answer', '{}', 3)"
    )
    connection.execute(
        "INSERT INTO questions(id, group_id, normalized_key, components_json) "
        "VALUES(2, '10001', 'owned-question', '{}')"
    )
    connection.execute(
        "INSERT INTO answers(id, question_id, normalized_key, components_json, weight) "
        "VALUES(20, 2, 'owned-answer', '{}', 2)"
    )
    for answer_id, message_id in ((10, 'm1'), (20, 'm2'), (20, 'm3')):
        connection.execute(
            "INSERT INTO contributions(answer_id, group_id, user_id, message_id, "
            "observed_at, finalized_at) VALUES(?, '10001', '12345', ?, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (answer_id, message_id),
        )
    connection.execute(
        "INSERT INTO pending_messages(platform, group_id, sender_id, message_id, "
        "timestamp, normalized_key, components_json) "
        "VALUES('aiocqhttp', '10001', '12345', 'pending', 1, 'pending', '{}')"
    )
    connection.commit()


def test_member_contribution_cleanup_reduces_shared_and_deletes_owned(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "data" / "new_chat_learning.sqlite3")
        await store.open()
        service = ContributionCleanupService(tmp_path / "data", store)
        _seed_contributions(store)
        try:
            prepared = await service.prepare(
                group_id="10001", user_id="12345", actor_id="webui:test"
            )
            result = await service.apply(
                plan_id=str(prepared["plan_id"]),
                group_id="10001",
                user_id="12345",
                actor_id="webui:test",
            )
            connection = store._require_connection()
            answers = [
                tuple(row)
                for row in connection.execute(
                    "SELECT id, weight FROM answers ORDER BY id"
                )
            ]
            questions = [row[0] for row in connection.execute("SELECT id FROM questions")]
            contributions = connection.execute(
                "SELECT COUNT(*) FROM contributions WHERE user_id = '12345'"
            ).fetchone()[0]
            pending = connection.execute(
                "SELECT COUNT(*) FROM pending_messages WHERE sender_id = '12345'"
            ).fetchone()[0]
            audit = connection.execute(
                "SELECT action, details_json FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return prepared, result, answers, questions, contributions, pending, tuple(audit)
        finally:
            await store.close()

    prepared, result, answers, questions, contributions, pending, audit = asyncio.run(
        scenario()
    )
    assert prepared["contributions"] == 3
    assert prepared["affected_answers"] == 2
    assert prepared["answers_becoming_empty"] == 1
    assert prepared["questions_becoming_empty"] == 1
    assert prepared["pending_messages"] == 1
    assert result["removed_contributions"] == 3
    assert result["reduced_answers"] == 1
    assert result["deleted_answers"] == 1
    assert result["orphan_questions"] == 1
    assert result["removed_pending_messages"] == 1
    assert answers == [(10, 2)]
    assert questions == [1]
    assert contributions == 0
    assert pending == 0
    assert audit[0] == "delete_member_contributions"
    assert "components" not in audit[1]


def test_member_contribution_cleanup_creates_pre_delete_backup(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = ContributionCleanupService(data_dir, store)
        _seed_contributions(store)
        try:
            prepared = await service.prepare(
                group_id="10001", user_id="12345", actor_id="7"
            )
            result = await service.apply(
                plan_id=str(prepared["plan_id"]),
                group_id="10001",
                user_id="12345",
                actor_id="7",
            )
            return result
        finally:
            await store.close()

    result = asyncio.run(scenario())
    backup_path = __import__("pathlib").Path(result["backup_path"])
    assert backup_path.name.startswith("before-contribution-delete-")
    backup = sqlite3.connect(backup_path)
    try:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute(
            "SELECT COUNT(*) FROM contributions WHERE user_id = '12345'"
        ).fetchone()[0] == 3
    finally:
        backup.close()


def test_member_contribution_cleanup_rejects_stale_plan(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = ContributionCleanupService(data_dir, store)
        _seed_contributions(store)
        try:
            prepared = await service.prepare(
                group_id="10001", user_id="12345", actor_id="7"
            )
            connection = store._require_connection()
            connection.execute("UPDATE answers SET weight = 4 WHERE id = 10")
            connection.commit()
            result = await service.apply(
                plan_id=str(prepared["plan_id"]),
                group_id="10001",
                user_id="12345",
                actor_id="7",
            )
            return result, connection.execute(
                "SELECT COUNT(*) FROM contributions WHERE user_id = '12345'"
            ).fetchone()[0]
        finally:
            await store.close()

    result, contribution_count = asyncio.run(scenario())
    assert result == {"applied": False, "reason": "plan_stale"}
    assert contribution_count == 3


def test_member_contribution_plan_is_actor_group_and_user_bound(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = ContributionCleanupService(data_dir, store)
        _seed_contributions(store)
        try:
            prepared = await service.prepare(
                group_id="10001", user_id="12345", actor_id="7"
            )
            plan_id = str(prepared["plan_id"])
            wrong_actor = await service.apply(
                plan_id=plan_id, group_id="10001", user_id="12345", actor_id="8"
            )
            wrong_group = await service.apply(
                plan_id=plan_id, group_id="10002", user_id="12345", actor_id="7"
            )
            wrong_user = await service.apply(
                plan_id=plan_id, group_id="10001", user_id="67890", actor_id="7"
            )
            return wrong_actor, wrong_group, wrong_user
        finally:
            await store.close()

    wrong_actor, wrong_group, wrong_user = asyncio.run(scenario())
    assert wrong_actor["reason"] == "wrong_actor"
    assert wrong_group["reason"] == "wrong_group"
    assert wrong_user["reason"] == "wrong_user"


def test_member_contribution_manifest_contains_no_message_content(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = ContributionCleanupService(data_dir, store)
        _seed_contributions(store)
        try:
            prepared = await service.prepare(
                group_id="10001", user_id="12345", actor_id="7"
            )
            path = service.plan_dir / f"{prepared['plan_id']}.json"
            return json.loads(path.read_text(encoding="utf-8"))
        finally:
            await store.close()

    manifest = asyncio.run(scenario())
    assert "components_json" not in json.dumps(manifest)
    assert set(manifest["operations"][0]) == {
        "answer_id",
        "question_id",
        "contribution_count",
        "total_contribution_count",
        "current_weight",
    }


def test_member_cleanup_preserves_answer_with_other_contributors_after_weight_change(
    tmp_path,
):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = ContributionCleanupService(data_dir, store)
        _seed_contributions(store)
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO contributions(answer_id, group_id, user_id, message_id, "
            "observed_at, finalized_at) VALUES(10, '10001', '67890', 'other', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute("UPDATE answers SET weight = 1 WHERE id = 10")
        connection.commit()
        try:
            prepared = await service.prepare(
                group_id="10001", user_id="12345", actor_id="7"
            )
            result = await service.apply(
                plan_id=str(prepared["plan_id"]),
                group_id="10001",
                user_id="12345",
                actor_id="7",
            )
            answer = connection.execute(
                "SELECT weight FROM answers WHERE id = 10"
            ).fetchone()
            other = connection.execute(
                "SELECT COUNT(*) FROM contributions "
                "WHERE answer_id = 10 AND user_id = '67890'"
            ).fetchone()[0]
            return prepared, result, answer[0], other
        finally:
            await store.close()

    prepared, result, weight, other = asyncio.run(scenario())
    shared = next(item for item in prepared["operations"] if item["answer_id"] == 10)
    assert shared["total_contribution_count"] == 2
    assert result["reduced_answers"] == 1
    assert weight == 1
    assert other == 1
