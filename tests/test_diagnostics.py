import asyncio

from new_chat_learning.application.diagnostics import RuntimeDiagnostics
from new_chat_learning.domain.message import NormalizedMessage
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore


def message(group_id: str, message_id: str, text: str, timestamp: int) -> NormalizedMessage:
    components = ({"type": "Plain", "data": {"text": text}},)
    return NormalizedMessage(
        platform="aiocqhttp",
        group_id=group_id,
        sender_id="private-user-id",
        message_id=message_id,
        timestamp=timestamp,
        components=components,
        matching_components=components,
    )


def test_diagnostics_aggregate_by_group_without_private_fields(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "diagnostics.sqlite3")
        await store.open()
        try:
            await store.observe_message(message("10001", "m1", "secret question", 1), 900)
            await store.observe_message(message("10001", "m2", "secret answer", 2), 900)
            diagnostics = RuntimeDiagnostics()
            diagnostics.record("10001", "normalized_messages")
            diagnostics.record("10001", "accepted_learning_messages")
            diagnostics.record("10001", "learned_pairs")
            diagnostics.record("10001", "reply_decisions", reason="exact")
            snapshot = await diagnostics.snapshot(
                store,
                ConfigService(
                    {
                        "learning": {"enabled": True, "group_ids": ["10001"]},
                        "reply": {"enabled": True, "group_ids": ["10001"]},
                    }
                ),
            )
            return snapshot
        finally:
            await store.close()

    snapshot = asyncio.run(scenario())
    group = snapshot["groups"][0]
    assert group["group_id"] == "10001"
    assert group["mode"] == "learning_reply"
    assert group["runtime"]["normalized_messages"] == 1
    assert group["runtime"]["reply_reasons"] == {"exact": 1}
    assert group["database"]["questions"] == 1
    assert group["database"]["answers"] == 1
    assert group["database"]["answer_weight"] == 1
    serialized = repr(snapshot)
    for private_value in ("private-user-id", "m1", "m2", "secret question", "secret answer"):
        assert private_value not in serialized


def test_runtime_diagnostics_start_empty_after_recreation():
    first = RuntimeDiagnostics()
    first.record("10001", "normalized_messages")
    second = RuntimeDiagnostics()

    assert first._groups["10001"]["normalized_messages"] == 1
    assert second._groups["10001"]["normalized_messages"] == 0
