import asyncio
import json
import random
import sqlite3

from new_chat_learning.application.learning import LearningService
from new_chat_learning.application.reply import ReplyService, cosine_similarity
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


async def seed_pair(store: SQLiteStore):
    learning = LearningService(store, interval_seconds=900)
    question = message("q1", 1000, "你好")
    await learning.observe(question)
    await learning.observe(message("a1", 1001, "你好呀"))
    return question


def test_exact_reply_probability_at_override_and_cooldown(tmp_path):
    now = [10.0]

    async def scenario():
        store = SQLiteStore(tmp_path / "reply.sqlite3")
        await store.open()
        question = await seed_pair(store)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 0,
                    "cooldown_seconds": 3,
                    "at_force_reply": True,
                }
            }
        )
        reply = ReplyService(
            store,
            config,
            random_source=random.Random(1),
            clock=lambda: now[0],
        )
        try:
            probability = await reply.decide("10001", question.normalized_key)
            forced = await reply.decide("10001", question.normalized_key, mentioned_bot=True)
            reply.mark_sent("10001")
            cooldown = await reply.decide("10001", question.normalized_key, mentioned_bot=True)
            now[0] = 14.0
            after_cooldown = await reply.decide(
                "10001", question.normalized_key, mentioned_bot=True
            )
        finally:
            await store.close()
        return probability, forced, cooldown, after_cooldown

    probability, forced, cooldown, after_cooldown = asyncio.run(scenario())

    assert probability.reason == "probability"
    assert forced.should_reply is True
    assert forced.candidate.components[0]["data"]["text"] == "你好呀"
    assert cooldown.reason == "cooldown"
    assert after_cooldown.should_reply is True


def test_silent_group_never_queries_or_replies(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "silent.sqlite3")
        await store.open()
        question = await seed_pair(store)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "silent_group_ids": ["10001"],
                    "probability_percent": 100,
                }
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            return await reply.decide("10001", question.normalized_key)
        finally:
            await store.close()

    decision = asyncio.run(scenario())

    assert decision.reason == "disabled"
    assert decision.should_reply is False


def test_weighted_selection_prefers_only_positive_candidates(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "weighted.sqlite3")
        await store.open()
        question = await seed_pair(store)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                }
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(2))
        try:
            decision = await reply.decide("10001", question.normalized_key)
            missing = await reply.decide("10001", "missing")
        finally:
            await store.close()
        return decision, missing

    decision, missing = asyncio.run(scenario())

    assert decision.candidate is not None
    assert decision.candidate.weight == 1
    assert missing.reason == "no_match"


def mark_question_as_regex(path, pattern):
    connection = sqlite3.connect(path)
    try:
        components_json = json.dumps(
            {"schema_version": 1, "components": [{"type": "Plain", "data": {"text": pattern}}]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            "UPDATE questions SET plain_text = ?, is_regex = 1, components_json = ?",
            (pattern, components_json),
        )
        connection.commit()
    finally:
        connection.close()


def test_regex_match_precedes_similarity(tmp_path):
    path = tmp_path / "regex.sqlite3"

    async def scenario():
        store = SQLiteStore(path)
        await store.open()
        await seed_pair(store)
        await store.close()
        mark_question_as_regex(path, r"你.*好")
        await store.open()
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                    "similarity_enabled": True,
                }
            }
        )
        try:
            return await ReplyService(store, config).decide(
                "10001",
                "not-an-exact-key",
                plain_text="你今天好",
            )
        finally:
            await store.close()

    decision = asyncio.run(scenario())

    assert decision.reason == "regex"
    assert decision.should_reply is True


def test_invalid_regex_is_skipped_without_breaking_similarity(tmp_path):
    path = tmp_path / "invalid-regex.sqlite3"

    async def scenario():
        store = SQLiteStore(path)
        await store.open()
        await seed_pair(store)
        await store.close()
        mark_question_as_regex(path, "[")
        await store.open()
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                    "similarity_enabled": False,
                }
            }
        )
        try:
            return await ReplyService(store, config).decide(
                "10001",
                "not-an-exact-key",
                plain_text="你好",
            )
        finally:
            await store.close()

    decision = asyncio.run(scenario())

    assert decision.reason == "no_match"


def test_regex_timeout_is_skipped(monkeypatch, tmp_path):
    path = tmp_path / "regex-timeout.sqlite3"

    async def scenario():
        store = SQLiteStore(path)
        await store.open()
        await seed_pair(store)
        await store.close()
        mark_question_as_regex(path, r"(a+)+$")
        await store.open()
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                }
            }
        )
        monkeypatch.setattr(
            "new_chat_learning.application.reply.regex.search",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError),
        )
        try:
            return await ReplyService(store, config).decide(
                "10001",
                "not-an-exact-key",
                plain_text="a" * 100,
            )
        finally:
            await store.close()

    decision = asyncio.run(scenario())

    assert decision.reason == "no_match"


def test_jieba_cosine_similarity_selects_best_question_and_honors_length(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "similarity.sqlite3")
        await store.open()
        learning = LearningService(store, interval_seconds=900)
        await learning.observe(message("q1", 1000, "今天天气不错"))
        await learning.observe(message("a1", 1001, "适合出去走走"))
        await learning.observe(message("q2", 2000, "电脑开不了机"))
        await learning.observe(message("a2", 2001, "检查一下电源"))
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                    "similarity_enabled": True,
                    "similarity_threshold": 0.5,
                    "similarity_max_length": 35,
                }
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            matched = await reply.decide(
                "10001",
                "not-an-exact-key",
                plain_text="今天天气不错啊",
            )
            too_long = await reply.decide(
                "10001",
                "not-an-exact-key",
                plain_text="今天天气很好" * 10,
            )
        finally:
            await store.close()
        return matched, too_long

    matched, too_long = asyncio.run(scenario())

    assert cosine_similarity("今天天气不错", "今天天气不错啊") >= 0.5
    assert matched.reason == "similarity"
    assert matched.candidate.components[0]["data"]["text"] == "适合出去走走"
    assert too_long.reason == "no_match"


def test_type_frequency_threshold_uses_answer_weight_or_answer_as_question_frequency(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "threshold.sqlite3")
        await store.open()
        question = await seed_pair(store)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                    "type_frequency_thresholds": {"Plain": 2},
                }
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            blocked = await reply.decide("10001", question.normalized_key)
            learning = LearningService(store, interval_seconds=900)
            await learning.observe(message("a2", 2000, "另一个答案"))
            await learning.observe(message("q2", 2001, "你好呀"))
            await learning.observe(message("a3", 2002, "再次回答"))
            allowed = await reply.decide("10001", question.normalized_key)
        finally:
            await store.close()
        return blocked, allowed

    blocked, allowed = asyncio.run(scenario())

    assert blocked.reason == "no_match"
    assert allowed.should_reply is True


def test_filtered_exact_match_does_not_fall_back_to_similarity(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "exact-short-circuit.sqlite3")
        await store.open()
        question = await seed_pair(store)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                    "similarity_enabled": True,
                    "similarity_threshold": 0,
                    "type_frequency_thresholds": {"Plain": 99},
                }
            }
        )
        try:
            return await ReplyService(store, config).decide(
                "10001",
                question.normalized_key,
                plain_text="你好",
            )
        finally:
            await store.close()

    decision = asyncio.run(scenario())

    assert decision.reason == "no_match"
