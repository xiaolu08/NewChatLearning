import asyncio
import base64
import json
import sqlite3

import pytest

from new_chat_learning.application.media import MediaService, _validate_public_url
from new_chat_learning.domain.message import NormalizedMessage
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
