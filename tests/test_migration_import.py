import asyncio
import json
import pickle
import sqlite3

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
            prepared = await service.prepare(source)
            applied = await service.apply(
                import_id=prepared["import_id"], group_id="10001", actor_id="7"
            )
            duplicate = await service.apply(
                import_id=prepared["import_id"], group_id="10001", actor_id="7"
            )
            connection = store._require_connection()
            question = connection.execute(
                "SELECT frequency FROM questions WHERE group_id = '10001'"
            ).fetchone()
            weights = [
                row[0]
                for row in connection.execute("SELECT weight FROM answers ORDER BY weight DESC")
            ]
            audits = connection.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'import_legacy_library'"
            ).fetchone()[0]
            return prepared, applied, duplicate, question[0], weights, audits
        finally:
            await store.close()

    prepared, applied, duplicate, frequency, weights, audits = asyncio.run(scenario())
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
