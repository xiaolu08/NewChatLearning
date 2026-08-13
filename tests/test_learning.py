import asyncio
import json
import sqlite3

from new_chat_learning.application.learning import LearningService
from new_chat_learning.domain.message import NormalizedMessage, RecallNotice
from new_chat_learning.infrastructure.database import SQLiteStore


def message(message_id: str, timestamp: int, text: str, sender: str = "42"):
    component = {"type": "Plain", "data": {"text": text}}
    return NormalizedMessage(
        platform="aiocqhttp",
        group_id="10001",
        sender_id=sender,
        message_id=message_id,
        timestamp=timestamp,
        components=(component,),
        matching_components=(component,),
    )


def test_adjacent_messages_learn_and_duplicate_answers_gain_weight(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "learning.sqlite3")
        await store.open()
        learning = LearningService(store, interval_seconds=900)
        try:
            first = await learning.observe(message("m1", 1000, "问题"))
            second = await learning.observe(message("m2", 1001, "答案"))
            await learning.observe(message("m3", 1002, "问题"))
            repeated = await learning.observe(message("m4", 1003, "答案"))
            duplicate = await learning.observe(message("m4", 1003, "答案"))
        finally:
            await store.close()
        return first, second, repeated, duplicate

    first, second, repeated, duplicate = asyncio.run(scenario())

    assert first.learned_pair is False
    assert second.learned_pair is True
    assert repeated.learned_pair is True
    assert duplicate.duplicate is True

    connection = sqlite3.connect(tmp_path / "learning.sqlite3")
    try:
        row = connection.execute(
            "SELECT weight, components_json FROM answers "
            "WHERE normalized_key = (SELECT normalized_key FROM pending_messages)"
        ).fetchone()
        question_count = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    finally:
        connection.close()
    assert row[0] == 2
    assert json.loads(row[1])["components"][0]["data"]["text"] == "答案"
    assert question_count == 2


def test_interval_breaks_chain_and_recall_removes_pending(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "recall.sqlite3")
        await store.open()
        learning = LearningService(store, interval_seconds=10)
        try:
            await learning.observe(message("m1", 1000, "旧消息"))
            reset = await learning.observe(message("m2", 1011, "新消息"))
            recalled = await learning.recall(RecallNotice("aiocqhttp", "10001", "m2"))
            stats = await store.statistics()
        finally:
            await store.close()
        return reset, recalled, stats

    reset, recalled, stats = asyncio.run(scenario())

    assert reset.chain_reset is True
    assert reset.learned_pair is False
    assert recalled.recalled_pending is True
    assert stats["questions"] == 1
    assert stats["answers"] == 0
    assert stats["pending_messages"] == 0


def test_targeted_learning_only_finalizes_target_user_answers(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "targeted.sqlite3")
        await store.open()
        learning = LearningService(store, interval_seconds=900)
        try:
            await learning.observe(message("m1", 1000, "最初问题", sender="6"), ("42",))
            ignored = await learning.observe(
                message("m2", 1001, "非目标用户发言", sender="7"), ("42",)
            )
            learned = await learning.observe(
                message("m3", 1002, "目标用户答案", sender="42"), ("42",)
            )
            detail = await store.search_questions("10001", "非目标用户发言")
            absent = await store.search_questions("10001", "最初问题")
            return ignored, learned, detail, absent
        finally:
            await store.close()

    ignored, learned, detail, absent = asyncio.run(scenario())

    assert ignored.learned_pair is False
    assert ignored.chain_reset is False
    assert learned.learned_pair is True
    assert len(detail) == 1
    assert detail[0]["answer_count"] == 1
    assert absent == []
