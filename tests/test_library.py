import asyncio

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
