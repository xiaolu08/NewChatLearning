import asyncio
import sqlite3

from new_chat_learning.application.content_filter import ContentFilterService
from new_chat_learning.application.runtime import RuntimeApplication
from new_chat_learning.domain.message import NormalizedMessage
from new_chat_learning.infrastructure.config import ConfigService


def message(message_id: str, text: str, *, group_id: str = "10001", sender_id: str = "42"):
    component = {"type": "Plain", "data": {"text": text}}
    return NormalizedMessage(
        platform="aiocqhttp",
        group_id=group_id,
        sender_id=sender_id,
        message_id=message_id,
        timestamp=int(message_id.removeprefix("m")),
        components=(component,),
        matching_components=(component,),
    )


def test_content_filter_supports_additive_group_rules_and_safe_regex(monkeypatch):
    service = ContentFilterService(
        ConfigService(
            {
                "filters": {
                    "contains": ["global"],
                    "exact": ["exact"],
                    "regex": [r"^re.+$"],
                    "component_types": ["At"],
                    "group_rules": [{"group_id": "10001", "contains": ["local"]}],
                }
            }
        )
    )

    assert service.reply_match("10001", message("m1", "local").components).rule_type == "contains"
    assert service.reply_match("10002", message("m1", "local").components).matched is False
    assert service.reply_match("10001", message("m1", "exact").components).rule_type == "exact"
    assert service.reply_match("10001", message("m1", "regex").components).rule_type == "regex"
    monkeypatch.setattr(
        "new_chat_learning.application.content_filter.regex.search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError),
    )
    assert service.reply_match("10001", message("m1", "regex").components).matched is False


def test_ordinary_reply_filter_does_not_block_learning(tmp_path):
    async def scenario():
        app = RuntimeApplication(
            tmp_path,
            {"filters": {"contains": ["blocked reply"], "sensitive": []}},
        )
        await app.start()
        try:
            first = await app.observe(message("m1000", "question"))
            second = await app.observe(message("m1001", "blocked reply"))
            return first, second, await app.store.statistics()
        finally:
            await app.stop()

    first, second, stats = asyncio.run(scenario())
    assert first.accepted is True
    assert second.learned_pair is True
    assert stats["answers"] == 1


def test_sensitive_threshold_message_and_later_messages_are_not_learned(tmp_path):
    async def scenario():
        app = RuntimeApplication(
            tmp_path,
            {
                "filters": {
                    "sensitive": ["secret"],
                    "blacklist_threshold": 2,
                    "blacklist_scope": "global",
                }
            },
        )
        await app.start()
        try:
            first = await app.observe(message("m1000", "secret one"))
            threshold = await app.observe(message("m1001", "secret two"))
            later = await app.observe(message("m1002", "ordinary"))
            return first, threshold, later, await app.blacklist_entries()
        finally:
            await app.stop()

    first, threshold, later, entries = asyncio.run(scenario())
    assert first.accepted is True
    assert threshold.accepted is False
    assert later.accepted is False
    assert entries[0]["hit_count"] == 2
    assert entries[0]["blocked"] == 1

    connection = sqlite3.connect(tmp_path / "new_chat_learning.sqlite3")
    try:
        pending = connection.execute(
            "SELECT message_id FROM pending_messages ORDER BY message_id"
        ).fetchall()
        stored_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(filter_hits)")
        }
    finally:
        connection.close()
    assert pending == [("m1000",)]
    assert "message_content" not in stored_columns


def test_group_blacklist_scope_is_isolated(tmp_path):
    async def scenario():
        app = RuntimeApplication(
            tmp_path,
            {
                "filters": {
                    "sensitive": ["secret"],
                    "blacklist_threshold": 1,
                    "blacklist_scope": "group",
                }
            },
        )
        await app.start()
        try:
            blocked = await app.observe(message("m1000", "secret", group_id="10001"))
            other = await app.observe(message("m1001", "ordinary", group_id="10002"))
            return blocked, other
        finally:
            await app.stop()

    blocked, other = asyncio.run(scenario())
    assert blocked.accepted is False
    assert other.accepted is True
