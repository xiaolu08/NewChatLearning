import asyncio
import base64
import json
import sqlite3
from pathlib import Path

import pytest

from new_chat_learning.application.media import MediaService, _validate_public_url
from new_chat_learning.domain.message import NormalizedMessage, normalized_components_key
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore


def media_message(source: str, message_id: str = "m1") -> NormalizedMessage:
    component = {"type": "Image", "data": {"file": source, "url": source}}
    matching = {"type": "Image", "data": {}}
    return NormalizedMessage(
        platform="aiocqhttp",
        group_id="10001",
        sender_id="42",
        message_id=message_id,
        timestamp=1000,
        components=(component,),
        matching_components=(matching,),
    )


def test_base64_media_is_persisted_by_hash_and_deduplicated(tmp_path):
    payload = b"same image payload"
    source = "base64://" + base64.b64encode(payload).decode()

    async def scenario():
        store = SQLiteStore(tmp_path / "media.sqlite3")
        await store.open()
        service = MediaService(tmp_path, store, ConfigService({}))
        try:
            first = await service.localize_message(media_message(source, "m1"))
            second = await service.localize_message(media_message(source, "m2"))
            stats = await store.statistics()
        finally:
            await store.close()
        return first, second, stats

    first, second, stats = asyncio.run(scenario())
    first_data = first.components[0]["data"]
    second_data = second.components[0]["data"]
    stored = tmp_path / first_data["media_path"]

    assert stored.read_bytes() == payload
    assert first_data["content_hash"] == second_data["content_hash"]
    assert first_data["media_path"] == second_data["media_path"]
    assert "base64://" not in str(first_data)
    assert stats["media_assets"] == 1
    assert stats["media_bytes"] == len(payload)


def test_forward_node_media_is_localized_recursively(tmp_path):
    source = "base64://" + base64.b64encode(b"forward image").decode()
    message = NormalizedMessage(
        platform="aiocqhttp",
        group_id="10001",
        sender_id="42",
        message_id="forward-1",
        timestamp=1000,
        components=(
            {
                "type": "Nodes",
                "data": {
                    "nodes": [
                        {
                            "uin": "42",
                            "name": "群友",
                            "content": [{"type": "Image", "data": {"file": source}}],
                        }
                    ]
                },
            },
        ),
        matching_components=(),
    )

    async def scenario():
        store = SQLiteStore(tmp_path / "forward.sqlite3")
        await store.open()
        service = MediaService(tmp_path / "data", store, ConfigService({}))
        try:
            return await service.localize_message(message)
        finally:
            await store.close()

    result = asyncio.run(scenario())
    nested = result.components[0]["data"]["nodes"][0]["content"][0]["data"]

    assert nested["media_path"].startswith("media/")
    assert "file" not in nested
    assert (tmp_path / "data" / nested["media_path"]).read_bytes() == b"forward image"


def test_local_file_persists_and_quota_failure_keeps_original_component(tmp_path):
    source_file = tmp_path / "source.bin"
    source_file.write_bytes(b"123456")

    async def scenario():
        store = SQLiteStore(tmp_path / "quota.sqlite3")
        await store.open()
        config = ConfigService(
            {
                "storage": {
                    "media_quota_gb": 0,
                    "media_max_file_mb": 1,
                }
            }
        )
        service = MediaService(tmp_path / "data", store, config)
        message = media_message(str(source_file))
        try:
            result = await service.localize_message(message)
            stats = await store.statistics()
        finally:
            await store.close()
        return message, result, stats

    original, result, stats = asyncio.run(scenario())

    assert result is original
    assert "media_path" not in result.components[0]["data"]
    assert stats["media_assets"] == 0


def test_database_records_relative_media_metadata(tmp_path):
    payload = base64.b64encode(b"voice").decode()

    async def scenario():
        store = SQLiteStore(tmp_path / "metadata.sqlite3")
        await store.open()
        service = MediaService(tmp_path, store, ConfigService({}))
        message = media_message(f"base64://{payload}")
        try:
            await service.localize_message(message)
        finally:
            await store.close()

    asyncio.run(scenario())

    connection = sqlite3.connect(tmp_path / "metadata.sqlite3")
    try:
        relative_path, state, size_bytes = connection.execute(
            "SELECT relative_path, state, size_bytes FROM media_assets"
        ).fetchone()
    finally:
        connection.close()
    assert not relative_path.startswith(("/", "\\"))
    assert state == "healthy"
    assert size_bytes == 5


def test_private_media_urls_are_rejected(monkeypatch):
    monkeypatch.setattr(
        "new_chat_learning.application.media.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 80))],
    )

    with pytest.raises(ValueError, match="private"):
        _validate_public_url("http://example.test/media.png")


def test_existing_hash_can_be_reused_after_quota_is_full(tmp_path):
    payload = b"already stored"
    source = "base64://" + base64.b64encode(payload).decode()

    async def scenario():
        store = SQLiteStore(tmp_path / "reuse.sqlite3")
        await store.open()
        service = MediaService(tmp_path, store, ConfigService({}))
        first = await service.localize_message(media_message(source, "m1"))
        full_config = ConfigService({"storage": {"media_quota_gb": 0}})
        full_service = MediaService(tmp_path, store, full_config)
        try:
            second = await full_service.localize_message(media_message(source, "m2"))
        finally:
            await store.close()
        return first, second

    first, second = asyncio.run(scenario())

    assert second.components[0]["data"]["media_path"] == first.components[0]["data"]["media_path"]


def test_same_content_with_different_extensions_reuses_one_disk_file(tmp_path):
    first_source = tmp_path / "first.jpg"
    second_source = tmp_path / "second.png"
    first_source.write_bytes(b"same bytes")
    second_source.write_bytes(b"same bytes")

    async def scenario():
        store = SQLiteStore(tmp_path / "extension.sqlite3")
        await store.open()
        service = MediaService(tmp_path / "data", store, ConfigService({}))
        try:
            first = await service.localize_message(media_message(str(first_source), "m1"))
            second = await service.localize_message(media_message(str(second_source), "m2"))
        finally:
            await store.close()
        return first, second

    first, second = asyncio.run(scenario())
    files = list((tmp_path / "data" / "media").rglob("*.*"))

    assert first.components[0]["data"]["media_path"] == second.components[0]["data"]["media_path"]
    assert len(files) == 1


def test_local_absolute_path_is_removed_after_persistence(tmp_path):
    source = tmp_path / "private-location.png"
    source.write_bytes(b"image")

    async def scenario():
        store = SQLiteStore(tmp_path / "portable.sqlite3")
        await store.open()
        service = MediaService(tmp_path / "data", store, ConfigService({}))
        try:
            return await service.localize_message(media_message(str(source)))
        finally:
            await store.close()

    result = asyncio.run(scenario())
    data = result.components[0]["data"]

    assert "file" not in data
    assert "url" not in data
    assert data["media_path"].startswith("media/")


def test_missing_but_already_counted_asset_can_be_restored_at_full_quota(tmp_path):
    payload = b"restore me"
    source = "base64://" + base64.b64encode(payload).decode()

    async def scenario():
        store = SQLiteStore(tmp_path / "restore.sqlite3")
        await store.open()
        service = MediaService(tmp_path, store, ConfigService({}))
        first = await service.localize_message(media_message(source, "m1"))
        stored = tmp_path / first.components[0]["data"]["media_path"]
        stored.unlink()
        full_service = MediaService(
            tmp_path,
            store,
            ConfigService({"storage": {"media_quota_gb": 0}}),
        )
        try:
            restored = await full_service.localize_message(media_message(source, "m2"))
        finally:
            await store.close()
        return restored, stored

    restored, stored = asyncio.run(scenario())

    assert restored.components[0]["data"]["media_path"]
    assert stored.read_bytes() == payload


def test_health_scan_marks_missing_media_without_deleting_answer(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "scan.sqlite3")
        await store.open()
        service = MediaService(tmp_path / "data", store, ConfigService({}))
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(id, group_id, normalized_key, components_json) "
            "VALUES(1, '10001', 'q', '{}')"
        )
        components = {
            "schema_version": 1,
            "components": [
                {
                    "type": "Image",
                    "data": {
                        "media_path": "media/aa/missing.png",
                        "content_hash": "a" * 64,
                    },
                }
            ],
        }
        connection.execute(
            "INSERT INTO answers(id, question_id, components_json, normalized_key) "
            "VALUES(10, 1, ?, 'a')",
            (json.dumps(components),),
        )
        connection.commit()
        try:
            result = await service.scan_group("10001")
            answer_count = connection.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
            state, reason = connection.execute(
                "SELECT state, reason FROM answer_media WHERE answer_id = 10"
            ).fetchone()
        finally:
            await store.close()
        return result, answer_count, state, reason

    result, answer_count, state, reason = asyncio.run(scenario())

    assert answer_count == 1
    assert state == "missing"
    assert reason == "local_file_missing"
    assert result["preview"]["answers_becoming_empty"] == 1


def test_health_scan_quarantines_path_traversal_and_hash_mismatch(tmp_path):
    data_dir = tmp_path / "data"
    valid_file = data_dir / "media" / "ok.bin"
    valid_file.parent.mkdir(parents=True)
    valid_file.write_bytes(b"actual")
    answers = [
        {"type": "File", "data": {"media_path": "../outside.bin"}},
        {
            "type": "Image",
            "data": {"media_path": "media/ok.bin", "content_hash": "0" * 64},
        },
    ]

    async def scenario():
        store = SQLiteStore(tmp_path / "unsafe.sqlite3")
        await store.open()
        service = MediaService(data_dir, store, ConfigService({}))
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(id, group_id, normalized_key, components_json) "
            "VALUES(1, '10001', 'q', '{}')"
        )
        for answer_id, component in enumerate(answers, 10):
            payload = json.dumps({"schema_version": 1, "components": [component]})
            connection.execute(
                "INSERT INTO answers(id, question_id, components_json, normalized_key) "
                "VALUES(?, 1, ?, ?)",
                (answer_id, payload, f"a{answer_id}"),
            )
        connection.commit()
        try:
            await service.scan_group("10001")
            return connection.execute(
                "SELECT state, reason FROM answer_media ORDER BY answer_id"
            ).fetchall()
        finally:
            await store.close()

    rows = asyncio.run(scenario())

    assert [tuple(row) for row in rows] == [
        ("quarantined", "path_outside_data_dir"),
        ("quarantined", "hash_mismatch"),
    ]


def test_health_scan_preserves_mixed_answer_and_accepts_safe_legacy_url(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "new_chat_learning.application.media.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )

    async def scenario():
        store = SQLiteStore(tmp_path / "legacy.sqlite3")
        await store.open()
        service = MediaService(tmp_path / "data", store, ConfigService({}))
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(id, group_id, normalized_key, components_json) "
            "VALUES(1, '10001', 'q', '{}')"
        )
        payload = json.dumps(
            {
                "schema_version": 1,
                "components": [
                    {"type": "Plain", "data": {"text": "still sendable"}},
                    {"type": "Image", "data": {"url": "https://example.test/old.jpg"}},
                ],
            }
        )
        connection.execute(
            "INSERT INTO answers(id, question_id, components_json, normalized_key) "
            "VALUES(10, 1, ?, 'a')",
            (payload,),
        )
        connection.commit()
        try:
            result = await service.scan_group("10001")
            row = connection.execute(
                "SELECT state, reason, answer_sendable_without_invalid FROM answer_media"
            ).fetchone()
        finally:
            await store.close()
        return result, row

    result, row = asyncio.run(scenario())

    assert tuple(row) == ("healthy", "remote_url_safe_not_downloaded", 1)
    assert result["preview"]["media_components"] == 0


def _insert_cleanup_answer(connection, answer_id, components, normalized_key=None, weight=1):
    payload = json.dumps({"schema_version": 1, "components": components})
    connection.execute(
        "INSERT INTO answers(id, question_id, components_json, normalized_key, weight) "
        "VALUES(?, 1, ?, ?, ?)",
        (answer_id, payload, normalized_key or f"answer:{answer_id}", weight),
    )


def test_media_cleanup_prunes_invalid_component_after_backup(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "runtime.sqlite3")
        await store.open()
        service = MediaService(data_dir, store, ConfigService({}))
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(id, group_id, normalized_key, components_json) "
            "VALUES(1, '10001', 'q', '{}')"
        )
        _insert_cleanup_answer(
            connection,
            10,
            [
                {"type": "Plain", "data": {"text": "keep me"}},
                {"type": "Image", "data": {"media_path": "media/missing.png"}},
            ],
            weight=3,
        )
        connection.commit()
        prepared = await service.prepare_cleanup(
            group_id="10001", actor_id="7", mode="prune"
        )
        applied = await service.apply_cleanup(
            plan_id=prepared["plan_id"], group_id="10001", actor_id="7"
        )
        row = connection.execute(
            "SELECT components_json, weight FROM answers WHERE id = 10"
        ).fetchone()
        audit = connection.execute(
            "SELECT action FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        backup_path = Path(applied["backup_path"])
        backup_integrity = sqlite3.connect(backup_path).execute("PRAGMA quick_check").fetchone()[0]
        await store.close()
        return prepared, applied, json.loads(row[0]), row[1], audit, backup_integrity

    prepared, applied, payload, weight, audit, backup_integrity = asyncio.run(scenario())

    assert prepared["update_answers"] == 1
    assert prepared["delete_answers"] == 0
    assert applied["removed_components"] == 1
    assert applied["updated_answers"] == 1
    assert payload["components"] == [{"type": "Plain", "data": {"text": "keep me"}}]
    assert weight == 3
    assert audit == "cleanup_invalid_media"
    assert backup_integrity == "ok"


def test_media_cleanup_deletes_empty_answer_and_orphan_question(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "runtime.sqlite3")
        await store.open()
        service = MediaService(data_dir, store, ConfigService({}))
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(id, group_id, normalized_key, components_json) "
            "VALUES(1, '10001', 'q', '{}')"
        )
        _insert_cleanup_answer(
            connection,
            10,
            [{"type": "Image", "data": {"media_path": "media/missing.png"}}],
        )
        connection.commit()
        prepared = await service.prepare_cleanup(group_id="10001", actor_id="7")
        applied = await service.apply_cleanup(
            plan_id=prepared["plan_id"], group_id="10001", actor_id="7"
        )
        counts = tuple(
            connection.execute(
                "SELECT (SELECT COUNT(*) FROM answers), (SELECT COUNT(*) FROM questions)"
            ).fetchone()
        )
        await store.close()
        return prepared, applied, counts

    prepared, applied, counts = asyncio.run(scenario())

    assert prepared["delete_answers"] == 1
    assert applied["deleted_answers"] == 1
    assert applied["orphan_questions"] == 1
    assert counts == (0, 0)


def test_media_cleanup_drop_answer_mode_deletes_mixed_answer(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "runtime.sqlite3")
        await store.open()
        service = MediaService(data_dir, store, ConfigService({}))
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(id, group_id, normalized_key, components_json) "
            "VALUES(1, '10001', 'q', '{}')"
        )
        _insert_cleanup_answer(
            connection,
            10,
            [
                {"type": "Plain", "data": {"text": "also delete"}},
                {"type": "File", "data": {}},
            ],
        )
        connection.commit()
        prepared = await service.prepare_cleanup(
            group_id="10001", actor_id="7", mode="drop-answer"
        )
        applied = await service.apply_cleanup(
            plan_id=prepared["plan_id"], group_id="10001", actor_id="7"
        )
        count = connection.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        await store.close()
        return prepared, applied, count

    prepared, applied, count = asyncio.run(scenario())

    assert prepared["delete_answers"] == 1
    assert applied["deleted_answers"] == 1
    assert count == 0


def test_media_cleanup_rejects_stale_plan_without_mutation(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "runtime.sqlite3")
        await store.open()
        service = MediaService(data_dir, store, ConfigService({}))
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(id, group_id, normalized_key, components_json) "
            "VALUES(1, '10001', 'q', '{}')"
        )
        _insert_cleanup_answer(
            connection,
            10,
            [
                {"type": "Plain", "data": {"text": "original"}},
                {"type": "Image", "data": {"media_path": "media/missing.png"}},
            ],
        )
        connection.commit()
        prepared = await service.prepare_cleanup(group_id="10001", actor_id="7")
        connection.execute(
            "UPDATE answers SET components_json = ? WHERE id = 10",
            (json.dumps({"schema_version": 1, "components": [{"type": "Plain", "data": {"text": "changed"}}]}),),
        )
        connection.commit()
        result = await service.apply_cleanup(
            plan_id=prepared["plan_id"], group_id="10001", actor_id="7"
        )
        remaining = connection.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        audits = connection.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'cleanup_invalid_media'"
        ).fetchone()[0]
        await store.close()
        return result, remaining, audits

    result, remaining, audits = asyncio.run(scenario())

    assert result["applied"] is False
    assert result["reason"] == "plan_stale"
    assert remaining == 1
    assert audits == 0


def test_media_cleanup_merges_duplicate_answer_weight_and_contributions(tmp_path):
    plain = {"type": "Plain", "data": {"text": "same answer"}}

    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "runtime.sqlite3")
        await store.open()
        service = MediaService(data_dir, store, ConfigService({}))
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(id, group_id, normalized_key, components_json) "
            "VALUES(1, '10001', 'q', '{}')"
        )
        survivor_key = normalized_components_key([plain])
        _insert_cleanup_answer(connection, 10, [plain], survivor_key, weight=2)
        _insert_cleanup_answer(
            connection,
            11,
            [plain, {"type": "Image", "data": {"media_path": "media/missing.png"}}],
            weight=3,
        )
        connection.execute(
            "INSERT INTO contributions(answer_id, group_id, user_id, observed_at) "
            "VALUES(11, '10001', '42', CURRENT_TIMESTAMP)"
        )
        connection.commit()
        prepared = await service.prepare_cleanup(group_id="10001", actor_id="7")
        applied = await service.apply_cleanup(
            plan_id=prepared["plan_id"], group_id="10001", actor_id="7"
        )
        answers = connection.execute(
            "SELECT id, weight FROM answers ORDER BY id"
        ).fetchall()
        contribution_answer = connection.execute(
            "SELECT answer_id FROM contributions"
        ).fetchone()[0]
        await store.close()
        return applied, answers, contribution_answer

    applied, answers, contribution_answer = asyncio.run(scenario())

    assert [tuple(row) for row in answers] == [(10, 5)]
    assert contribution_answer == 10
    assert applied["merged_answers"] == 1


def test_media_cleanup_plan_is_bound_to_preparing_actor(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "runtime.sqlite3")
        await store.open()
        service = MediaService(data_dir, store, ConfigService({}))
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(id, group_id, normalized_key, components_json) "
            "VALUES(1, '10001', 'q', '{}')"
        )
        _insert_cleanup_answer(
            connection,
            10,
            [{"type": "File", "data": {}}],
        )
        connection.commit()
        prepared = await service.prepare_cleanup(group_id="10001", actor_id="7")
        result = await service.apply_cleanup(
            plan_id=prepared["plan_id"], group_id="10001", actor_id="8"
        )
        count = connection.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        await store.close()
        return result, count

    result, count = asyncio.run(scenario())

    assert result == {"applied": False, "reason": "wrong_actor"}
    assert count == 1


def test_media_cleanup_does_not_prepare_empty_plan(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "runtime.sqlite3")
        await store.open()
        service = MediaService(data_dir, store, ConfigService({}))
        try:
            return await service.prepare_cleanup(group_id="10001", actor_id="7")
        finally:
            await store.close()

    result = asyncio.run(scenario())

    assert result == {"prepared": False, "reason": "no_invalid_media"}
