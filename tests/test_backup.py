import asyncio
import sqlite3

import pytest

from new_chat_learning.application.backup import BackupService
from new_chat_learning.constants import SCHEMA_VERSION
from new_chat_learning.infrastructure.database import SQLiteStore


def test_backup_service_lists_and_inspects_only_local_sqlite_backups(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        try:
            connection = store._require_connection()
            connection.execute(
                "INSERT INTO questions(group_id, normalized_key, components_json) "
                "VALUES('10001', 'q', '{}')"
            )
            connection.commit()
            backup = data_dir / "backups" / "before-test.sqlite3"
            await store.backup_to(backup)
            (data_dir / "backups" / "ignored.txt").write_text("ignored", encoding="utf-8")
            service = BackupService(data_dir, store)
            return await service.list_backups(), await service.inspect(backup.name)
        finally:
            await store.close()

    entries, inspection = asyncio.run(scenario())
    assert [entry["name"] for entry in entries] == ["before-test.sqlite3"]
    assert inspection["integrity"] == "ok"
    assert inspection["schema_version"] == SCHEMA_VERSION
    assert inspection["counts"]["questions"] == 1
    assert inspection["restorable"] is True


def test_backup_service_rejects_path_traversal_and_missing_files(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = BackupService(data_dir, store)
        try:
            with pytest.raises(ValueError, match="invalid_backup_name"):
                await service.inspect("../outside.sqlite3")
            with pytest.raises(ValueError, match="backup_not_found"):
                await service.inspect("missing.sqlite3")
        finally:
            await store.close()

    asyncio.run(scenario())


def test_restore_replaces_database_creates_safety_backup_and_audits(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = BackupService(data_dir, store)
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(group_id, normalized_key, components_json) "
            "VALUES('10001', 'before', '{}')"
        )
        connection.commit()
        source = data_dir / "backups" / "chosen.sqlite3"
        await store.backup_to(source)
        connection.execute(
            "INSERT INTO questions(group_id, normalized_key, components_json) "
            "VALUES('10001', 'after', '{}')"
        )
        connection.commit()
        result = await service.restore(name=source.name, actor_id="webui:test")
        restored = store._require_connection()
        keys = [row[0] for row in restored.execute("SELECT normalized_key FROM questions")]
        audit = restored.execute(
            "SELECT action, details_json FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        await store.close()
        return result, keys, tuple(audit)

    result, keys, audit = asyncio.run(scenario())
    assert result["restored"] is True
    assert result["backup_name"] == "chosen.sqlite3"
    assert result["safety_backup_name"].startswith("before-restore-")
    assert keys == ["before"]
    assert audit[0] == "restore_database_backup"
    assert "webui:test" not in audit[1]


def test_restore_rejects_future_schema_without_changing_runtime(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = BackupService(data_dir, store)
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(group_id, normalized_key, components_json) "
            "VALUES('10001', 'keep', '{}')"
        )
        connection.commit()
        future = data_dir / "backups" / "future.sqlite3"
        await store.backup_to(future)
        external = sqlite3.connect(future)
        external.execute(
            "UPDATE schema_meta SET value = '999' WHERE key = 'schema_version'"
        )
        external.commit()
        external.close()
        try:
            with pytest.raises(ValueError, match="backup_not_restorable"):
                await service.restore(name=future.name, actor_id="webui:test")
            keys = [
                row[0]
                for row in store._require_connection().execute(
                    "SELECT normalized_key FROM questions"
                )
            ]
            return keys
        finally:
            await store.close()

    assert asyncio.run(scenario()) == ["keep"]


def test_restore_rejects_non_numeric_schema_without_changing_runtime(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = BackupService(data_dir, store)
        malformed = data_dir / "backups" / "malformed.sqlite3"
        await store.backup_to(malformed)
        external = sqlite3.connect(malformed)
        external.execute(
            "UPDATE schema_meta SET value = 'not-a-number' WHERE key = 'schema_version'"
        )
        external.commit()
        external.close()
        try:
            inspection = await service.inspect(malformed.name)
            with pytest.raises(ValueError, match="backup_not_restorable"):
                await service.restore(name=malformed.name, actor_id="webui:test")
            return inspection, await store.health()
        finally:
            await store.close()

    inspection, health = asyncio.run(scenario())
    assert inspection["restorable"] is False
    assert health["integrity"] == "ok"


def test_restore_reopens_current_database_when_atomic_replace_fails(
    monkeypatch, tmp_path
):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = BackupService(data_dir, store)
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(group_id, normalized_key, components_json) "
            "VALUES('10001', 'keep', '{}')"
        )
        connection.commit()
        source = data_dir / "backups" / "chosen.sqlite3"
        await store.backup_to(source)

        def fail_replace(_source, _destination):
            raise OSError("replace failed")

        monkeypatch.setattr("new_chat_learning.infrastructure.database.os.replace", fail_replace)
        try:
            with pytest.raises(OSError, match="replace failed"):
                await service.restore(name=source.name, actor_id="webui:test")
            keys = [
                row[0]
                for row in store._require_connection().execute(
                    "SELECT normalized_key FROM questions"
                )
            ]
            return keys, await store.health()
        finally:
            await store.close()

    keys, health = asyncio.run(scenario())
    assert keys == ["keep"]
    assert health["integrity"] == "ok"


def test_restore_migrates_older_schema_backup(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = BackupService(data_dir, store)
        old = data_dir / "backups" / "old.sqlite3"
        await store.backup_to(old)
        external = sqlite3.connect(old)
        external.execute("UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'")
        external.commit()
        external.close()
        result = await service.restore(name=old.name, actor_id="webui:test")
        health = await store.health()
        await store.close()
        return result, health

    result, health = asyncio.run(scenario())
    assert result["schema_version"] == SCHEMA_VERSION
    assert health["schema_version"] == SCHEMA_VERSION
    assert health["integrity"] == "ok"


def test_restore_rolls_back_when_post_replace_audit_fails(monkeypatch, tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = BackupService(data_dir, store)
        connection = store._require_connection()
        connection.execute(
            "INSERT INTO questions(group_id, normalized_key, components_json) "
            "VALUES('10001', 'backup-state', '{}')"
        )
        connection.commit()
        source = data_dir / "backups" / "chosen.sqlite3"
        await store.backup_to(source)
        connection.execute(
            "INSERT INTO questions(group_id, normalized_key, components_json) "
            "VALUES('10001', 'current-state', '{}')"
        )
        connection.commit()
        monkeypatch.setattr(
            store,
            "_insert_audit",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("audit failed")),
        )
        try:
            with pytest.raises(OSError, match="audit failed"):
                await service.restore(name=source.name, actor_id="webui:test")
            keys = [
                row[0]
                for row in store._require_connection().execute(
                    "SELECT normalized_key FROM questions ORDER BY id"
                )
            ]
            return keys, await store.health()
        finally:
            await store.close()

    keys, health = asyncio.run(scenario())
    assert keys == ["backup-state", "current-state"]
    assert health["integrity"] == "ok"
