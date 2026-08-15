import asyncio
import json
import pickle
import sqlite3
import time

import pytest

from new_chat_learning.application.migration import MigrationService
from new_chat_learning.infrastructure.database import SQLiteStore
from new_chat_learning.migration.converter import prepare_import


def _legacy_file(path):
    payload = {
        "[{'type': 'Plain', 'text': '你好'}]": {
            "freq": 4,
            "time": 1650000000,
            "regular": False,
            "answer": [
                {
                    "answertext": "[{'type': 'Plain', 'text': '世界'}]",
                    "time": 1650000001,
                    "same": 2,
                },
                {
                    "answertext": "[{'type': 'Face', 'faceId': 182, 'name': '笑哭'}]",
                    "time": 1650000002,
                },
            ],
        }
    }
    path.write_bytes(pickle.dumps(payload, protocol=4))


def test_prepare_and_apply_legacy_library_with_backup(tmp_path):
    source = tmp_path / "legacy.cl"
    _legacy_file(source)

    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = MigrationService(data_dir, store)
        try:
            prepared = await service.prepare(
                source,
                actor_id="7",
                group_id="10001",
                source_name="legacy.cl",
            )
            applied = await service.apply(
                import_id=prepared["import_id"], group_id="10001", actor_id="7"
            )
            duplicate = await service.apply(
                import_id=prepared["import_id"], group_id="10001", actor_id="7"
            )
            connection = store._require_connection()
            question = connection.execute(
                "SELECT frequency FROM questions WHERE group_id = ?",
                (f"external:{prepared['library_id']}",),
            ).fetchone()
            weights = [
                row[0]
                for row in connection.execute("SELECT weight FROM answers ORDER BY weight DESC")
            ]
            audits = connection.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'import_external_library'"
            ).fetchone()[0]
            libraries = await service.list_libraries()
            return prepared, applied, duplicate, question[0], weights, audits, libraries
        finally:
            await store.close()

    prepared, applied, duplicate, frequency, weights, audits, libraries = asyncio.run(scenario())
    assert prepared["status"] == "prepared"
    assert prepared["question_count"] == 1
    assert prepared["answer_count"] == 2
    assert prepared["skip_reasons"] == {
        "invalid_question_shape": 0,
        "invalid_question_components": 0,
        "questions_without_convertible_answers": 0,
        "invalid_answer_shape": 0,
        "invalid_answer_components": 0,
    }
    assert applied["imported"] is True
    assert duplicate == {
        "imported": False,
        "reason": "manifest_not_found",
    }
    assert frequency == 4
    assert weights == [3, 1]
    assert audits == 1
    assert libraries[0]["library_id"] == prepared["library_id"]
    assert libraries[0]["group_ids"] == ["10001"]
    assert libraries[0]["enabled"] is True
    backup = sqlite3.connect(applied["backup_path"])
    try:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        backup.close()


def test_staging_checksum_mismatch_rolls_back(tmp_path):
    source = tmp_path / "legacy.cl"
    _legacy_file(source)
    staging = tmp_path / "staging"
    prepared = prepare_import(source, staging)
    staging_path = staging / prepared["staging_file"]
    staging_path.write_text(
        staging_path.read_text(encoding="utf-8") + json.dumps({"answers": []}) + "\n",
        encoding="utf-8",
    )

    async def scenario():
        store = SQLiteStore(tmp_path / "runtime.sqlite3")
        await store.open()
        try:
            with pytest.raises(ValueError, match="staging_checksum_mismatch"):
                await store.import_legacy_jsonl(
                    import_id=prepared["import_id"],
                    group_id="10001",
                    source_name="legacy.cl",
                    staging_path=staging_path,
                    staging_sha256=prepared["staging_sha256"],
                    actor_id="7",
                )
            return await store.statistics()
        finally:
            await store.close()

    statistics = asyncio.run(scenario())
    assert statistics["questions"] == 0
    assert statistics["answers"] == 0
    assert statistics["legacy_imports"] == 0


def test_web_import_plan_is_bound_to_actor_group_and_expires(tmp_path):
    source = tmp_path / "legacy.cl"
    _legacy_file(source)

    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = MigrationService(data_dir, store)
        try:
            prepared = await service.prepare(
                source,
                actor_id="webui:owner",
                group_id="10001",
                source_name="shared-library.cl",
            )
            wrong_actor = await service.apply(
                import_id=prepared["import_id"],
                group_id="10001",
                actor_id="webui:other",
            )
            wrong_group = await service.apply(
                import_id=prepared["import_id"],
                group_id="10002",
                actor_id="webui:owner",
            )
            manifest = service.staging_dir / f"{prepared['import_id']}.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["expires_at"] = int(time.time()) - 1
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            expired = await service.apply(
                import_id=prepared["import_id"],
                group_id="10001",
                actor_id="webui:owner",
            )
            return prepared, wrong_actor, wrong_group, expired, manifest.exists()
        finally:
            await store.close()

    prepared, wrong_actor, wrong_group, expired, manifest_exists = asyncio.run(scenario())
    assert prepared["source_name"] == "shared-library.cl"
    assert wrong_actor["reason"] == "wrong_actor"
    assert wrong_group["reason"] == "wrong_group"
    assert expired["reason"] == "plan_expired"
    assert manifest_exists is False


def test_external_library_can_be_disabled_rebound_updated_and_deleted(tmp_path):
    source = tmp_path / "legacy.cl"
    _legacy_file(source)

    async def scenario():
        data_dir = tmp_path / "data"
        store = SQLiteStore(data_dir / "new_chat_learning.sqlite3")
        await store.open()
        service = MigrationService(data_dir, store)
        try:
            prepared = await service.prepare(
                source,
                actor_id="owner",
                group_id="10001",
                source_name="shared.cl",
                library_name="共享词库",
            )
            created = await service.apply(
                import_id=prepared["import_id"], group_id="10001", actor_id="owner"
            )
            library_id = created["library_id"]
            disabled = await service.set_enabled(
                library_id=library_id, enabled=False, actor_id="owner"
            )
            hidden_scopes = await store.external_library_scopes_for("10001")
            rebound = await service.set_bindings(
                library_id=library_id, group_ids=["10002", "10003"], actor_id="owner"
            )
            enabled = await service.set_enabled(
                library_id=library_id, enabled=True, actor_id="owner"
            )
            visible_scopes = await store.external_library_scopes_for("10002")

            updated_plan = await service.prepare(
                source,
                actor_id="owner",
                group_ids=rebound["group_ids"],
                source_name="shared-v2.cl",
                library_id=library_id,
                library_name="共享词库",
                operation="update",
            )
            updated = await service.apply(
                import_id=updated_plan["import_id"], group_id="10002", actor_id="owner"
            )
            deleted = await service.delete(library_id=library_id, actor_id="owner")
            remaining = await service.list_libraries()
            scope_questions = store._require_connection().execute(
                "SELECT COUNT(*) FROM questions WHERE group_id = ?",
                (f"external:{library_id}",),
            ).fetchone()[0]
            return (
                disabled,
                hidden_scopes,
                rebound,
                enabled,
                visible_scopes,
                updated,
                deleted,
                remaining,
                scope_questions,
            )
        finally:
            await store.close()

    (
        disabled,
        hidden_scopes,
        rebound,
        enabled,
        visible_scopes,
        updated,
        deleted,
        remaining,
        scope_questions,
    ) = asyncio.run(scenario())
    assert disabled["enabled"] is False
    assert hidden_scopes == ()
    assert rebound["group_ids"] == ["10002", "10003"]
    assert enabled["enabled"] is True
    assert visible_scopes == (f"external:{enabled['library_id']}",)
    assert updated["version"] == 2
    assert deleted["backup_path"].endswith(".sqlite3")
    assert remaining == []
    assert scope_questions == 0
