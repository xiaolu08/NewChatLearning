import asyncio
import sqlite3

from new_chat_learning.infrastructure.database import SQLiteStore


def test_database_initializes_schema_and_statistics(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "runtime.sqlite3")
        await store.open()
        try:
            health = await store.health()
            statistics = await store.statistics()
        finally:
            await store.close()
        return health, statistics

    health, statistics = asyncio.run(scenario())

    assert health["connected"] is True
    assert health["schema_version"] == 5
    assert health["integrity"] == "ok"
    assert statistics["questions"] == 0
    assert statistics["answers"] == 0
    assert statistics["pending_messages"] == 0


def test_database_upgrades_skeleton_schema_v1_in_place(tmp_path):
    path = tmp_path / "v1.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta(key, value) VALUES('schema_version', '1');
        CREATE TABLE groups (
            group_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL DEFAULT 'learning_reply',
            retention_days INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            normalized_key TEXT NOT NULL,
            components_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(group_id, normalized_key)
        );
        CREATE TABLE answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            components_json TEXT NOT NULL,
            weight INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO questions(group_id, normalized_key, components_json)
        VALUES(
            '10001',
            'legacy-question',
            '{"schema_version":1,"components":[{"type":"Plain","data":{"text":"旧问题"}}]}'
        );
        INSERT INTO answers(question_id, components_json, weight)
        VALUES(1, '{"components": []}', 1), (1, '{"components": []}', 2);
        CREATE TABLE contributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            answer_id INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            message_id TEXT,
            observed_at TEXT NOT NULL,
            finalized_at TEXT
        );
        CREATE TABLE media_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash TEXT NOT NULL UNIQUE,
            relative_path TEXT,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'available',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            checked_at TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    connection.close()

    async def scenario():
        store = SQLiteStore(path)
        await store.open()
        try:
            return await store.health(), await store.statistics()
        finally:
            await store.close()

    health, statistics = asyncio.run(scenario())

    connection = sqlite3.connect(path)
    try:
        question_columns = {row[1] for row in connection.execute("PRAGMA table_info(questions)")}
        answer_columns = {row[1] for row in connection.execute("PRAGMA table_info(answers)")}
    finally:
        connection.close()
    assert health["schema_version"] == 5
    assert statistics["pending_messages"] == 0
    assert "frequency" in question_columns
    assert "plain_text" in question_columns
    assert "is_regex" in question_columns
    connection = sqlite3.connect(path)
    try:
        media_columns = {row[1] for row in connection.execute("PRAGMA table_info(media_assets)")}
    finally:
        connection.close()
    assert "original_name" in media_columns
    assert "source_url" in media_columns
    assert "normalized_key" in answer_columns
    connection = sqlite3.connect(path)
    try:
        legacy_keys = [
            row[0] for row in connection.execute("SELECT normalized_key FROM answers ORDER BY id")
        ]
    finally:
        connection.close()
    assert legacy_keys == ["legacy:1", "legacy:2"]
    connection = sqlite3.connect(path)
    try:
        plain_text, is_regex = connection.execute(
            "SELECT plain_text, is_regex FROM questions WHERE id = 1"
        ).fetchone()
    finally:
        connection.close()
    assert plain_text == "旧问题"
    assert is_regex == 0


def test_fast_delete_removes_exact_answer_or_orphan_question_and_audits(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "delete.sqlite3")
        await store.open()
        try:
            connection = store._require_connection()
            connection.execute(
                "INSERT INTO questions(id, group_id, normalized_key, components_json) "
                "VALUES(1, '10001', 'q', '{}')"
            )
            connection.execute(
                "INSERT INTO answers(id, question_id, components_json, normalized_key) "
                "VALUES(10, 1, '{}', 'a1'), (11, 1, '{}', 'a2')"
            )
            connection.commit()
            await store.register_reply(
                platform="aiocqhttp",
                group_id="10001",
                sent_message_id="501",
                answer_id=10,
                question_id=1,
            )
            await store.register_reply(
                platform="aiocqhttp",
                group_id="10001",
                sent_message_id="502",
                answer_id=11,
                question_id=1,
            )
            recent = await store.recent_reply_message_id(
                platform="aiocqhttp", group_id="10001", position=2
            )
            first = await store.fast_delete_reply(
                platform="aiocqhttp",
                group_id="10001",
                sent_message_id="501",
                actor_id="7",
            )
            answer_count = connection.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
            question_count = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            second = await store.fast_delete_reply(
                platform="aiocqhttp",
                group_id="10001",
                sent_message_id="502",
                actor_id="7",
            )
            final_question_count = connection.execute(
                "SELECT COUNT(*) FROM questions"
            ).fetchone()[0]
            audit_count = connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            duplicate = await store.fast_delete_reply(
                platform="aiocqhttp",
                group_id="10001",
                sent_message_id="502",
                actor_id="7",
            )
            return (
                recent,
                first,
                answer_count,
                question_count,
                second,
                final_question_count,
                audit_count,
                duplicate,
            )
        finally:
            await store.close()

    result = asyncio.run(scenario())
    assert result[0] == "501"
    assert result[1]["deleted"] is True
    assert result[1]["orphan_question_removed"] is False
    assert result[2:4] == (1, 1)
    assert result[4]["orphan_question_removed"] is True
    assert result[5:7] == (0, 2)
    assert result[7] == {"deleted": False, "reason": "not_found"}
