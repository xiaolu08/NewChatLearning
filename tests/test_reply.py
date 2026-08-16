import asyncio
import json
import random
import sqlite3

from new_chat_learning.application.learning import LearningService
from new_chat_learning.application.library import LibraryService, plain_normalized_key
from new_chat_learning.application.reply import ReplyService, cosine_similarity
from new_chat_learning.domain.message import NormalizedMessage
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore


def message(message_id: str, timestamp: int, text: str, group_id: str = "10001"):
    component = {"type": "Plain", "data": {"text": text}}
    return NormalizedMessage(
        platform="aiocqhttp",
        group_id=group_id,
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


async def seed_group_pair(
    store: SQLiteStore,
    group_id: str,
    question_text: str,
    answer_text: str,
    timestamp: int,
):
    learning = LearningService(store, interval_seconds=900)
    question = message(f"q-{group_id}-{timestamp}", timestamp, question_text, group_id)
    await learning.observe(question)
    await learning.observe(
        message(f"a-{group_id}-{timestamp}", timestamp + 1, answer_text, group_id)
    )
    return question


def quoted_message(message_id: str, timestamp: int, text: str, group_id: str = "10001"):
    components = (
        {"type": "Reply", "data": {"id": "quoted-message", "text": "quoted"}},
        {"type": "At", "data": {"qq": "12345", "name": "member"}},
        {"type": "Plain", "data": {"text": text}},
    )
    return NormalizedMessage(
        platform="aiocqhttp",
        group_id=group_id,
        sender_id="42",
        message_id=message_id,
        timestamp=timestamp,
        components=components,
        matching_components=components,
    )


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


def test_share_group_reply_cooldown_is_independent_for_each_member_group(tmp_path):
    now = [10.0]

    async def scenario():
        store = SQLiteStore(tmp_path / "share-cooldown.sqlite3")
        await store.open()
        question = await seed_group_pair(store, "10001", "你好", "你好呀", 1000)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001", "10002"],
                    "probability_percent": 100,
                    "cooldown_seconds": 0,
                },
                "library": {
                    "share_groups": [
                        {
                            "name": "牛牛联动组",
                            "group_ids": ["10001", "10002"],
                            "reply_cooldown_minutes": 50,
                        }
                    ]
                },
            }
        )
        reply = ReplyService(store, config, clock=lambda: now[0])
        try:
            first = await reply.decide("10001", question.normalized_key)
            reply.mark_sent("10001")
            other_group = await reply.decide("10002", question.normalized_key)
            reply.mark_sent("10002")
            blocked = await reply.decide("10002", question.normalized_key)
            now[0] = 3010.0
            after_cooldown = await reply.decide("10002", question.normalized_key)
        finally:
            await store.close()
        return first, other_group, blocked, after_cooldown

    first, other_group, blocked, after_cooldown = asyncio.run(scenario())

    assert first.should_reply is True
    assert other_group.should_reply is True
    assert blocked.reason == "share_cooldown"
    assert after_cooldown.should_reply is True


def test_all_share_group_cooldowns_must_expire(tmp_path):
    now = [10.0]

    async def scenario():
        store = SQLiteStore(tmp_path / "multiple-share-cooldowns.sqlite3")
        await store.open()
        question = await seed_pair(store)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                    "cooldown_seconds": 0,
                },
                "library": {
                    "share_groups": [
                        {
                            "name": "短冷却组",
                            "group_ids": ["10001"],
                            "reply_cooldown_minutes": 50,
                        },
                        {
                            "name": "长冷却组",
                            "group_ids": ["10001"],
                            "reply_cooldown_minutes": 60,
                        },
                    ]
                },
            }
        )
        reply = ReplyService(store, config, clock=lambda: now[0])
        try:
            first = await reply.decide("10001", question.normalized_key)
            reply.mark_sent("10001")
            now[0] = 3010.0
            still_blocked = await reply.decide("10001", question.normalized_key)
            now[0] = 3610.0
            allowed = await reply.decide("10001", question.normalized_key)
        finally:
            await store.close()
        return first, still_blocked, allowed

    first, still_blocked, allowed = asyncio.run(scenario())

    assert first.should_reply is True
    assert still_blocked.reason == "share_cooldown"
    assert allowed.should_reply is True


def test_message_type_probability_controls_trigger_independently(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "type-probability.sqlite3")
        await store.open()
        question = await seed_pair(store)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 0,
                    "cooldown_seconds": 0,
                    "group_type_probability_overrides": [
                        {
                            "group_id": "10001",
                            "message_type": "text",
                            "probability_percent": 100,
                        }
                    ],
                }
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            text = await reply.decide(
                "10001",
                question.normalized_key,
                trigger_components=({"type": "Plain", "data": {"text": "你好"}},),
            )
            image = await reply.decide(
                "10001",
                question.normalized_key,
                trigger_components=({"type": "Image", "data": {"file": "x"}},),
            )
        finally:
            await store.close()
        return text, image

    text, image = asyncio.run(scenario())

    assert text.should_reply is True
    assert image.reason == "probability"


def test_repeat_replies_are_limited_to_two_per_rolling_hour(tmp_path):
    now = [10_000.0]

    async def scenario():
        store = SQLiteStore(tmp_path / "repeat-limit.sqlite3")
        await store.open()
        library = LibraryService(store)
        await library.add_text_pair(
            group_id="10001",
            actor_id="test",
            question="咕咕嘎嘎",
            answer="咕咕嘎嘎",
        )
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                    "cooldown_seconds": 0,
                }
            }
        )
        reply = ReplyService(
            store,
            config,
            random_source=random.Random(1),
            wall_clock=lambda: now[0],
        )
        key = plain_normalized_key("咕咕嘎嘎")
        components = ({"type": "Plain", "data": {"text": "咕咕嘎嘎"}},)
        try:
            first = await reply.decide("10001", key, trigger_components=components)
            await reply.mark_repeat_sent("10001")
            now[0] += 120
            second = await reply.decide("10001", key, trigger_components=components)
            await reply.mark_repeat_sent("10001")
            now[0] += 600
            blocked = await reply.decide("10001", key, trigger_components=components)
            now[0] += 3300
            after_window = await reply.decide("10001", key, trigger_components=components)
        finally:
            await store.close()
        return first, second, blocked, after_window

    first, second, blocked, after_window = asyncio.run(scenario())

    assert first.is_repeat is True
    assert second.is_repeat is True
    assert blocked.reason == "repeat_limit"
    assert after_window.is_repeat is True


def test_plain_exact_reply_matches_question_learned_with_reply_and_at(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "plain-exact.sqlite3")
        await store.open()
        learning = LearningService(store, interval_seconds=900)
        question = quoted_message("q1", 1000, "那麻爪了")
        await learning.observe(question)
        await learning.observe(message("a1", 1001, "图片答案"))
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
            reply = ReplyService(store, config, random_source=random.Random(1))
            matched = await reply.decide(
                "10001",
                message("incoming", 2000, "那麻爪了").normalized_key,
                plain_text="那麻爪了",
            )
            typo = await reply.decide(
                "10001",
                message("typo", 2001, "那麻瓜了").normalized_key,
                plain_text="那麻瓜了",
            )
            return question, matched, typo
        finally:
            await store.close()

    question, matched, typo = asyncio.run(scenario())

    assert question.normalized_key != message("plain", 1, "那麻爪了").normalized_key
    assert matched.reason == "plain_exact"
    assert matched.should_reply is True
    assert typo.reason == "no_match"


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


def test_reply_filters_candidates_before_weighted_selection(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "filtered-weight.sqlite3")
        await store.open()
        question = await seed_group_pair(
            store, "10001", "filter question", "blocked answer", 1000
        )
        connection = store._require_connection()
        question_id = connection.execute(
            "SELECT id FROM questions WHERE normalized_key = ?",
            (question.normalized_key,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE answers SET weight = 100 WHERE question_id = ?",
            (question_id,),
        )
        connection.execute(
            "INSERT INTO answers(question_id, normalized_key, components_json, weight) "
            "VALUES(?, 'safe-answer', ?, 1)",
            (
                question_id,
                json.dumps(
                    {
                        "schema_version": 1,
                        "components": [{"type": "Plain", "data": {"text": "safe answer"}}],
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        connection.commit()
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                },
                "filters": {"contains": ["blocked answer"]},
            }
        )
        try:
            decision = await ReplyService(store, config, random_source=random.Random(1)).decide(
                "10001", question.normalized_key
            )
            stats = await store.filter_hit_statistics()
            return decision, stats
        finally:
            await store.close()

    decision, stats = asyncio.run(scenario())
    assert decision.candidate.components[0]["data"]["text"] == "safe answer"
    assert stats == {"reply:contains": 1}


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


def test_similarity_matches_upstream_punctuation_and_zero_score_behavior(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "similarity-zero.sqlite3")
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
                }
            }
        )
        try:
            decision = await ReplyService(store, config).decide(
                "10001",
                "not-an-exact-key",
                plain_text="再见",
            )
            return question, decision
        finally:
            await store.close()

    question, decision = asyncio.run(scenario())

    assert question.plain_text == "你好"
    assert cosine_similarity("你好「」『』〔〕〈〉", "你好") == 1.0
    assert decision.reason == "no_match"


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


def test_default_group_library_does_not_leak_other_groups(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "isolated.sqlite3")
        await store.open()
        question = await seed_group_pair(store, "10002", "跨群问题", "跨群答案", 1000)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                }
            }
        )
        try:
            return await ReplyService(store, config).decide("10001", question.normalized_key)
        finally:
            await store.close()

    decision = asyncio.run(scenario())

    assert decision.reason == "no_match"


def test_global_library_combines_untagged_groups_and_honors_exclusions(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "global.sqlite3")
        await store.open()
        included = await seed_group_pair(store, "10002", "公共问题", "公共答案", 1000)
        excluded = await seed_group_pair(store, "10003", "排除问题", "排除答案", 2000)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                },
                "library": {
                    "mode": "global",
                    "excluded_group_ids": ["10003"],
                },
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            found = await reply.decide("10001", included.normalized_key)
            hidden = await reply.decide("10001", excluded.normalized_key)
        finally:
            await store.close()
        return found, hidden

    found, hidden = asyncio.run(scenario())

    assert found.candidate.components[0]["data"]["text"] == "公共答案"
    assert hidden.reason == "no_match"


def test_local_only_reply_group_ignores_global_library(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "local-only.sqlite3")
        await store.open()
        local = await seed_group_pair(store, "10001", "本群问题", "本群答案", 1000)
        shared = await seed_group_pair(store, "10002", "共享问题", "共享答案", 2000)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                },
                "library": {
                    "mode": "global",
                    "local_only_group_ids": ["10001"],
                },
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            local_result = await reply.decide("10001", local.normalized_key)
            shared_result = await reply.decide("10001", shared.normalized_key)
        finally:
            await store.close()
        return local_result, shared_result

    local_result, shared_result = asyncio.run(scenario())

    assert local_result.candidate.components[0]["data"]["text"] == "本群答案"
    assert shared_result.reason == "no_match"


def test_explicit_global_reply_group_uses_shared_library_in_group_mode(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "explicit-global.sqlite3")
        await store.open()
        shared = await seed_group_pair(store, "10002", "共享问题", "共享答案", 1000)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001", "10003"],
                    "probability_percent": 100,
                },
                "library": {
                    "mode": "group",
                    "global_group_ids": ["10001"],
                },
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            enabled = await reply.decide("10001", shared.normalized_key)
            isolated = await reply.decide("10003", shared.normalized_key)
        finally:
            await store.close()
        return enabled, isolated

    enabled, isolated = asyncio.run(scenario())

    assert enabled.candidate.components[0]["data"]["text"] == "共享答案"
    assert isolated.reason == "no_match"


def test_tagged_group_queries_only_shared_tag_members(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "tags.sqlite3")
        await store.open()
        shared = await seed_group_pair(store, "10002", "标签问题", "标签答案", 1000)
        outsider = await seed_group_pair(store, "10003", "外部问题", "外部答案", 2000)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                },
                "library": {
                    "mode": "global",
                    "group_tags": [
                        {"group_id": "10001", "tags": ["friends"]},
                        {"group_id": "10002", "tags": ["friends"]},
                        {"group_id": "10003", "tags": ["games"]},
                    ],
                },
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            found = await reply.decide("10001", shared.normalized_key)
            hidden = await reply.decide("10001", outsider.normalized_key)
        finally:
            await store.close()
        return found, hidden

    found, hidden = asyncio.run(scenario())

    assert found.candidate.components[0]["data"]["text"] == "标签答案"
    assert hidden.reason == "no_match"


def test_share_group_exposes_only_direct_member_group_libraries(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "share-groups.sqlite3")
        await store.open()
        shared = await seed_group_pair(store, "10002", "联动问题", "联动答案", 1000)
        indirect = await seed_group_pair(store, "10003", "递归问题", "递归答案", 2000)
        library_id = "b" * 24
        external = await seed_group_pair(
            store,
            f"external:{library_id}",
            "外部联动问题",
            "外部联动答案",
            3000,
        )
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO external_libraries(library_id, name, source_name, staging_sha256, "
            "question_count, answer_count, actor_id) VALUES(?, ?, ?, ?, 1, 1, ?)",
            (library_id, "成员群外部词库", "member.cl", "1" * 64, "test"),
        )
        connection.execute(
            "INSERT INTO external_library_bindings(library_id, group_id) VALUES(?, ?)",
            (library_id, "10002"),
        )
        connection.commit()
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001", "10004"],
                    "probability_percent": 100,
                },
                "library": {
                    "mode": "group",
                    "global_group_ids": ["10002"],
                    "share_groups": [
                        {"name": "联动词库1", "group_ids": ["10001", "10002"]},
                        {"name": "联动词库2", "group_ids": ["10002", "10003"]},
                    ],
                },
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            direct = await reply.decide("10001", shared.normalized_key)
            recursive = await reply.decide("10001", indirect.normalized_key)
            inherited_external = await reply.decide("10001", external.normalized_key)
            outsider = await reply.decide("10004", shared.normalized_key)
        finally:
            await store.close()
        return direct, recursive, inherited_external, outsider

    direct, recursive, inherited_external, outsider = asyncio.run(scenario())

    assert direct.candidate.components[0]["data"]["text"] == "联动答案"
    assert recursive.reason == "no_match"
    assert inherited_external.reason == "no_match"
    assert outsider.reason == "no_match"


def test_multiple_tags_repeat_candidates_like_upstream_tag_libraries(tmp_path):
    class CapturingRandom(random.Random):
        def __init__(self):
            super().__init__(1)
            self.population = None
            self.weights = None

        def choices(self, population, weights=None, *, cum_weights=None, k=1):
            self.population = list(population)
            self.weights = list(weights or [])
            return [self.population[0]]

    async def scenario():
        store = SQLiteStore(tmp_path / "multi-tag.sqlite3")
        await store.open()
        question = await seed_group_pair(store, "10001", "多标签问题", "多标签答案", 1000)
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                },
                "library": {
                    "mode": "global",
                    "group_tags": [
                        {"group_id": "10001", "tags": ["friends", "games"]},
                    ],
                },
            }
        )
        random_source = CapturingRandom()
        try:
            decision = await ReplyService(store, config, random_source=random_source).decide(
                "10001", question.normalized_key
            )
        finally:
            await store.close()
        return decision, random_source

    decision, random_source = asyncio.run(scenario())

    assert decision.should_reply is True
    assert len(random_source.population) == 2
    assert random_source.weights == [1, 1]


def test_global_scope_supports_cross_group_regex_and_similarity(tmp_path):
    path = tmp_path / "fallback-scope.sqlite3"

    async def scenario():
        store = SQLiteStore(path)
        await store.open()
        await seed_group_pair(store, "10002", "你.*好", "正则答案", 1000)
        await seed_group_pair(store, "10003", "今天天气不错", "相似答案", 2000)
        await store.close()
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "UPDATE questions SET is_regex = 1 WHERE group_id = '10002' AND plain_text = '你.*好'"
            )
            connection.commit()
        finally:
            connection.close()
        await store.open()
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001"],
                    "probability_percent": 100,
                    "similarity_enabled": True,
                },
                "library": {"mode": "global"},
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            regex_result = await reply.decide("10001", "missing-regex", plain_text="你今天好")
            similarity_result = await reply.decide(
                "10001", "missing-similarity", plain_text="今天天气不错啊"
            )
        finally:
            await store.close()
        return regex_result, similarity_result

    regex_result, similarity_result = asyncio.run(scenario())

    assert regex_result.reason == "regex"
    assert regex_result.candidate.components[0]["data"]["text"] == "正则答案"
    assert similarity_result.reason == "similarity"
    assert similarity_result.candidate.components[0]["data"]["text"] == "相似答案"


def test_bound_external_library_can_be_toggled_without_changing_group_library(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "external-library.sqlite3")
        await store.open()
        library_id = "a" * 24
        external = await seed_group_pair(
            store,
            f"external:{library_id}",
            "外部问题",
            "外部答案",
            1000,
        )
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO external_libraries(library_id, name, source_name, staging_sha256, "
            "question_count, answer_count, actor_id) VALUES(?, ?, ?, ?, 1, 1, ?)",
            (library_id, "测试外部词库", "test.cl", "0" * 64, "test"),
        )
        connection.execute(
            "INSERT INTO external_library_bindings(library_id, group_id) VALUES(?, ?)",
            (library_id, "10001"),
        )
        connection.commit()
        config = ConfigService(
            {
                "reply": {
                    "enabled": True,
                    "group_ids": ["10001", "10002"],
                    "probability_percent": 100,
                }
            }
        )
        reply = ReplyService(store, config, random_source=random.Random(1))
        try:
            bound = await reply.decide("10001", external.normalized_key)
            unbound = await reply.decide("10002", external.normalized_key)
            await store.set_external_library_enabled(
                library_id=library_id, enabled=False, actor_id="test"
            )
            disabled = await reply.decide("10001", external.normalized_key)
        finally:
            await store.close()
        return bound, unbound, disabled

    bound, unbound, disabled = asyncio.run(scenario())
    assert bound.candidate.components[0]["data"]["text"] == "外部答案"
    assert unbound.reason == "no_match"
    assert disabled.reason == "no_match"
