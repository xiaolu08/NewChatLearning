import asyncio
import sqlite3

import pytest

from new_chat_learning.application.library import (
    LibraryService,
    component_preview,
    parse_add_pair,
)
from new_chat_learning.infrastructure.database import SQLiteStore


def test_pair_parser_and_component_preview():
    pair = parse_add_pair(" 你好 => 世界 => 仍属于答案 ")
    assert pair is not None
    assert pair.question == "你好"
    assert pair.answer == "世界 => 仍属于答案"
    assert parse_add_pair("没有分隔符") is None
    assert component_preview('{"components":[{"type":"Image","data":{}}]}') == "[Image]"


def test_library_management_is_group_scoped_and_audited(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "library.sqlite3")
        await store.open()
        library = LibraryService(store)
        try:
            first = await library.add_text_pair(
                group_id="10001",
                actor_id="7",
                question="你好",
                answer="世界",
            )
            duplicate = await library.add_text_pair(
                group_id="10001",
                actor_id="7",
                question="你好",
                answer="世界",
            )
            other_group = await library.add_text_pair(
                group_id="10002",
                actor_id="8",
                question="你好",
                answer="另一个群",
            )
            search = await library.search("10001", "你")
            hidden = await library.show("10002", first["question_id"])
            detail = await library.show("10001", first["question_id"])
            changed = await library.set_weight(
                group_id="10001",
                actor_id="7",
                answer_id=first["answer_id"],
                weight=9,
            )
            forbidden = await library.set_weight(
                group_id="10002",
                actor_id="8",
                answer_id=first["answer_id"],
                weight=2,
            )
            deleted = await library.delete_answer(
                group_id="10001", actor_id="7", answer_id=first["answer_id"]
            )
            other_deleted = await library.delete_question(
                group_id="10002",
                actor_id="8",
                question_id=other_group["question_id"],
            )
            connection = store._require_connection()
            audit_actions = [
                row[0]
                for row in connection.execute("SELECT action FROM audit_log ORDER BY id")
            ]
            return {
                "first": first,
                "duplicate": duplicate,
                "search": search,
                "hidden": hidden,
                "detail": detail,
                "changed": changed,
                "forbidden": forbidden,
                "deleted": deleted,
                "other_deleted": other_deleted,
                "audit_actions": audit_actions,
            }
        finally:
            await store.close()

    result = asyncio.run(scenario())
    assert result["first"]["created"] is True
    assert result["duplicate"]["created"] is False
    assert result["duplicate"]["weight"] == 2
    assert len(result["search"]) == 1
    assert result["search"][0]["question_id"] == result["first"]["question_id"]
    assert result["hidden"] is None
    assert result["detail"]["answers"][0]["weight"] == 2
    assert result["changed"] is True
    assert result["forbidden"] is False
    assert result["deleted"] == {"deleted": True, "orphan_question_removed": True}
    assert result["other_deleted"] is True
    assert result["audit_actions"] == [
        "add_custom_pair",
        "add_custom_pair",
        "add_custom_pair",
        "set_answer_weight",
        "delete_answer",
        "delete_question",
    ]


def test_invalid_regex_is_rejected_before_database_write(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "regex.sqlite3")
        await store.open()
        library = LibraryService(store)
        try:
            with pytest.raises(ValueError, match="invalid_regex"):
                await library.add_text_pair(
                    group_id="10001",
                    actor_id="7",
                    question="([",
                    answer="bad",
                    is_regex=True,
                )
            return await store.statistics()
        finally:
            await store.close()

    statistics = asyncio.run(scenario())
    assert statistics["questions"] == 0
    assert statistics["answers"] == 0


def test_library_deletion_creates_integrity_checked_backup(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "library-backup.sqlite3")
        await store.open()
        library = LibraryService(store, tmp_path)
        try:
            pair = await library.add_text_pair(
                group_id="10001",
                actor_id="7",
                question="需要备份的问题",
                answer="需要备份的答案",
            )
            result = await library.delete_answer_with_backup(
                group_id="10001",
                actor_id="webui:test",
                answer_id=pair["answer_id"],
            )
            audits = [
                row[0]
                for row in store._require_connection().execute(
                    "SELECT action FROM audit_log ORDER BY id"
                )
            ]
            return pair, result, audits
        finally:
            await store.close()

    pair, result, audits = asyncio.run(scenario())
    backup_path = __import__("pathlib").Path(result["backup_path"])
    assert result["deleted"] is True
    assert backup_path.name.endswith(f"-A{pair['answer_id']}.sqlite3")
    backup = sqlite3.connect(backup_path)
    try:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute(
            "SELECT COUNT(*) FROM answers WHERE id = ?", (pair["answer_id"],)
        ).fetchone()[0] == 1
    finally:
        backup.close()
    assert audits[-1] == "delete_answer"


def test_global_exact_text_answer_delete_covers_all_scopes_with_one_backup(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "global-answer-delete.sqlite3")
        await store.open()
        library = LibraryService(store, tmp_path)
        try:
            pairs = []
            for index, group_id in enumerate(("10001", "10002", "external:legacy"), 1):
                pairs.append(
                    await library.add_text_pair(
                        group_id=group_id,
                        actor_id="7",
                        question=f"问题 {index}",
                        answer="米家出了绝区零这个游戏真是帮大忙了",
                    )
                )
            kept = await library.add_text_pair(
                group_id="10001",
                actor_id="7",
                question="保留问题",
                answer="只差一个标点。",
            )
            result = await library.delete_answer_text_globally(
                actor_id="7",
                answer_text="米家出了绝区零这个游戏真是帮大忙了",
            )
            connection = store._require_connection()
            remaining_answers = int(
                connection.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
            )
            remaining_questions = int(
                connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            )
            audit = connection.execute(
                "SELECT action, details_json FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return pairs, kept, result, remaining_answers, remaining_questions, audit
        finally:
            await store.close()

    pairs, kept, result, remaining_answers, remaining_questions, audit = asyncio.run(
        scenario()
    )

    assert result["deleted_answers"] == 3
    assert result["orphan_questions"] == 3
    assert result["group_count"] == 3
    assert remaining_answers == 1
    assert remaining_questions == 1
    backup_path = __import__("pathlib").Path(result["backup_path"])
    backup = sqlite3.connect(backup_path)
    try:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 4
    finally:
        backup.close()
    assert audit["action"] == "delete_answers_by_text_globally"
    assert "米家出了" not in str(audit["details_json"])
    assert kept["answer_id"] not in {pair["answer_id"] for pair in pairs}


def test_global_answer_delete_does_not_backup_when_no_exact_match(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "global-answer-delete-miss.sqlite3")
        await store.open()
        library = LibraryService(store, tmp_path)
        try:
            await library.add_text_pair(
                group_id="10001",
                actor_id="7",
                question="问题",
                answer="完整答案",
            )
            return await library.delete_answer_text_globally(
                actor_id="7",
                answer_text="完整答案。",
            )
        finally:
            await store.close()

    result = asyncio.run(scenario())
    assert result == {
        "deleted_answers": 0,
        "orphan_questions": 0,
        "group_count": 0,
        "backup_path": None,
    }
    assert list((tmp_path / "backups").glob("*.sqlite3")) == []
