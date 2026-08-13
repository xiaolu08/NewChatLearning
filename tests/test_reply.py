import asyncio
import random

from new_chat_learning.application.learning import LearningService
from new_chat_learning.application.reply import ReplyService
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
