from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from new_chat_learning.constants import SCHEMA_VERSION
from new_chat_learning.domain.message import NormalizedMessage
from new_chat_learning.domain.reply import QuestionCandidate, ReplyCandidate

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    group_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'learning_reply',
    retention_days INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    normalized_key TEXT NOT NULL,
    components_json TEXT NOT NULL,
    plain_text TEXT NOT NULL DEFAULT '',
    is_regex INTEGER NOT NULL DEFAULT 0 CHECK(is_regex IN (0, 1)),
    frequency INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_id, normalized_key)
);

CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    components_json TEXT NOT NULL,
    normalized_key TEXT NOT NULL DEFAULT '',
    weight INTEGER NOT NULL DEFAULT 1 CHECK(weight > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_messages (
    platform TEXT NOT NULL,
    group_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    normalized_key TEXT NOT NULL,
    components_json TEXT NOT NULL,
    PRIMARY KEY(platform, group_id),
    UNIQUE(platform, message_id)
);

CREATE TABLE IF NOT EXISTS contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    answer_id INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    message_id TEXT,
    observed_at TEXT NOT NULL,
    finalized_at TEXT
);

CREATE TABLE IF NOT EXISTS media_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    relative_path TEXT,
    media_type TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'available',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checked_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_questions_group ON questions(group_id);
CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question_id);
CREATE INDEX IF NOT EXISTS idx_contributions_user ON contributions(group_id, user_id);
CREATE INDEX IF NOT EXISTS idx_media_state ON media_assets(state);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
"""


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA_SQL)
            self._migrate_schema(connection)
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()
            self._connection = connection

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    async def health(self) -> dict[str, Any]:
        async with self._lock:
            connection = self._require_connection()
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            return {
                "connected": True,
                "schema_version": int(row[0]) if row else 0,
                "integrity": integrity,
                "path": str(self.path),
            }

    async def statistics(self) -> dict[str, int]:
        async with self._lock:
            connection = self._require_connection()
            tables = (
                "groups",
                "questions",
                "answers",
                "pending_messages",
                "media_assets",
                "audit_log",
            )
            result: dict[str, int] = {}
            for table in tables:
                row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                result[table] = int(row[0])
            media_bytes = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM media_assets WHERE state = 'healthy'"
            ).fetchone()
            result["media_bytes"] = int(media_bytes[0])
            return result

    async def media_usage_bytes(self) -> int:
        async with self._lock:
            connection = self._require_connection()
            row = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM media_assets WHERE state = 'healthy'"
            ).fetchone()
            return int(row[0])

    async def find_media_asset(self, content_hash: str) -> dict[str, Any] | None:
        async with self._lock:
            connection = self._require_connection()
            row = connection.execute(
                "SELECT content_hash, relative_path, media_type, size_bytes, state "
                "FROM media_assets WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            return dict(row) if row is not None else None

    async def register_media_asset(
        self,
        *,
        content_hash: str,
        relative_path: str,
        media_type: str,
        size_bytes: int,
        original_name: str = "",
        source_url: str = "",
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            connection.execute(
                "INSERT INTO media_assets(content_hash, relative_path, media_type, "
                "original_name, source_url, size_bytes, state, checked_at) "
                "VALUES(?, ?, ?, ?, ?, ?, 'healthy', CURRENT_TIMESTAMP) "
                "ON CONFLICT(content_hash) DO UPDATE SET "
                "relative_path=excluded.relative_path, media_type=excluded.media_type, "
                "original_name=CASE WHEN excluded.original_name != '' THEN excluded.original_name "
                "ELSE media_assets.original_name END, "
                "source_url=CASE WHEN excluded.source_url != '' THEN excluded.source_url "
                "ELSE media_assets.source_url END, size_bytes=excluded.size_bytes, "
                "state='healthy', checked_at=CURRENT_TIMESTAMP",
                (
                    content_hash,
                    relative_path,
                    media_type,
                    original_name,
                    source_url,
                    int(size_bytes),
                ),
            )
            connection.commit()

    async def observe_message(
        self, message: NormalizedMessage, interval_seconds: int
    ) -> dict[str, bool]:
        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO groups(group_id) VALUES(?) ON CONFLICT(group_id) DO NOTHING",
                    (message.group_id,),
                )
                previous = connection.execute(
                    "SELECT * FROM pending_messages WHERE platform = ? AND group_id = ?",
                    (message.platform, message.group_id),
                ).fetchone()
                if previous is not None and str(previous["message_id"]) == message.message_id:
                    connection.rollback()
                    return {
                        "learned_pair": False,
                        "chain_reset": False,
                        "duplicate": True,
                    }
                learned_pair = False
                chain_reset = False
                if previous is not None:
                    elapsed = message.timestamp - int(previous["timestamp"])
                    if 0 <= elapsed <= interval_seconds:
                        question_id = self._upsert_question(
                            connection,
                            message.group_id,
                            str(previous["normalized_key"]),
                            str(previous["components_json"]),
                        )
                        answer_id = self._upsert_answer(connection, question_id, message)
                        connection.execute(
                            "INSERT INTO contributions(answer_id, group_id, user_id, message_id, observed_at, finalized_at) "
                            "VALUES(?, ?, ?, ?, datetime(?, 'unixepoch'), CURRENT_TIMESTAMP)",
                            (
                                answer_id,
                                message.group_id,
                                message.sender_id,
                                message.message_id,
                                message.timestamp,
                            ),
                        )
                        learned_pair = True
                    else:
                        self._upsert_question(
                            connection,
                            message.group_id,
                            str(previous["normalized_key"]),
                            str(previous["components_json"]),
                        )
                        chain_reset = True
                connection.execute(
                    "INSERT INTO pending_messages(platform, group_id, sender_id, message_id, timestamp, normalized_key, components_json) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(platform, group_id) DO UPDATE SET "
                    "sender_id=excluded.sender_id, message_id=excluded.message_id, "
                    "timestamp=excluded.timestamp, normalized_key=excluded.normalized_key, "
                    "components_json=excluded.components_json",
                    (
                        message.platform,
                        message.group_id,
                        message.sender_id,
                        message.message_id,
                        message.timestamp,
                        message.normalized_key,
                        message.components_json,
                    ),
                )
                connection.commit()
                return {
                    "learned_pair": learned_pair,
                    "chain_reset": chain_reset,
                    "duplicate": False,
                }
            except Exception:
                connection.rollback()
                raise

    async def remove_pending_message(self, platform: str, group_id: str, message_id: str) -> bool:
        async with self._lock:
            connection = self._require_connection()
            cursor = connection.execute(
                "DELETE FROM pending_messages WHERE platform = ? AND group_id = ? AND message_id = ?",
                (platform, group_id, message_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    async def find_exact_answers(self, group_id: str, normalized_key: str) -> list[ReplyCandidate]:
        async with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                "SELECT a.id AS answer_id, a.question_id, a.weight, "
                "COALESCE(aq.frequency, 0) AS answer_question_frequency, a.components_json "
                "FROM answers AS a JOIN questions AS q ON q.id = a.question_id "
                "LEFT JOIN questions AS aq ON aq.group_id = q.group_id "
                "AND aq.normalized_key = a.normalized_key "
                "WHERE q.group_id = ? AND q.normalized_key = ? AND a.weight > 0 "
                "ORDER BY a.id",
                (str(group_id), normalized_key),
            ).fetchall()
            return [
                ReplyCandidate(
                    answer_id=int(row["answer_id"]),
                    question_id=int(row["question_id"]),
                    weight=int(row["weight"]),
                    answer_question_frequency=int(row["answer_question_frequency"]),
                    components_json=str(row["components_json"]),
                )
                for row in rows
            ]

    async def find_matchable_questions(self, group_id: str) -> list[QuestionCandidate]:
        async with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                "SELECT id, plain_text, is_regex FROM questions "
                "WHERE group_id = ? AND plain_text != '' ORDER BY id",
                (str(group_id),),
            ).fetchall()
            return [
                QuestionCandidate(
                    question_id=int(row["id"]),
                    plain_text=str(row["plain_text"]),
                    is_regex=bool(row["is_regex"]),
                )
                for row in rows
            ]

    async def find_answers_for_question(self, question_id: int) -> list[ReplyCandidate]:
        async with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                "SELECT a.id AS answer_id, a.question_id, a.weight, "
                "COALESCE(aq.frequency, 0) AS answer_question_frequency, "
                "a.components_json FROM answers AS a "
                "JOIN questions AS q ON q.id = a.question_id "
                "LEFT JOIN questions AS aq ON aq.group_id = q.group_id "
                "AND aq.normalized_key = a.normalized_key "
                "WHERE a.question_id = ? AND a.weight > 0 ORDER BY a.id",
                (int(question_id),),
            ).fetchall()
            return [
                ReplyCandidate(
                    answer_id=int(row["answer_id"]),
                    question_id=int(row["question_id"]),
                    weight=int(row["weight"]),
                    answer_question_frequency=int(row["answer_question_frequency"]),
                    components_json=str(row["components_json"]),
                )
                for row in rows
            ]

    @staticmethod
    def _upsert_question(
        connection: sqlite3.Connection,
        group_id: str,
        normalized_key: str,
        components_json: str,
    ) -> int:
        connection.execute(
            "INSERT INTO questions(group_id, normalized_key, components_json, plain_text) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(group_id, normalized_key) DO UPDATE SET "
            "frequency=questions.frequency + 1, "
            "plain_text=CASE WHEN questions.plain_text = '' THEN excluded.plain_text "
            "ELSE questions.plain_text END, updated_at=CURRENT_TIMESTAMP",
            (
                group_id,
                normalized_key,
                components_json,
                _plain_text_from_components(components_json),
            ),
        )
        row = connection.execute(
            "SELECT id FROM questions WHERE group_id = ? AND normalized_key = ?",
            (group_id, normalized_key),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _upsert_answer(
        connection: sqlite3.Connection, question_id: int, message: NormalizedMessage
    ) -> int:
        connection.execute(
            "INSERT INTO answers(question_id, components_json, normalized_key) VALUES(?, ?, ?) "
            "ON CONFLICT(question_id, normalized_key) DO UPDATE SET "
            "weight=answers.weight + 1, updated_at=CURRENT_TIMESTAMP",
            (question_id, message.components_json, message.normalized_key),
        )
        row = connection.execute(
            "SELECT id FROM answers WHERE question_id = ? AND normalized_key = ?",
            (question_id, message.normalized_key),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        question_columns = {row[1] for row in connection.execute("PRAGMA table_info(questions)")}
        if "frequency" not in question_columns:
            connection.execute(
                "ALTER TABLE questions ADD COLUMN frequency INTEGER NOT NULL DEFAULT 1"
            )
        answer_columns = {row[1] for row in connection.execute("PRAGMA table_info(answers)")}
        if "normalized_key" not in answer_columns:
            connection.execute(
                "ALTER TABLE answers ADD COLUMN normalized_key TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            "UPDATE answers SET normalized_key = 'legacy:' || id WHERE normalized_key = ''"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_answers_unique ON answers(question_id, normalized_key)"
        )
        question_columns = {row[1] for row in connection.execute("PRAGMA table_info(questions)")}
        if "plain_text" not in question_columns:
            connection.execute(
                "ALTER TABLE questions ADD COLUMN plain_text TEXT NOT NULL DEFAULT ''"
            )
        if "is_regex" not in question_columns:
            connection.execute(
                "ALTER TABLE questions ADD COLUMN is_regex INTEGER NOT NULL DEFAULT 0"
            )
        media_columns = {row[1] for row in connection.execute("PRAGMA table_info(media_assets)")}
        if "original_name" not in media_columns:
            connection.execute(
                "ALTER TABLE media_assets ADD COLUMN original_name TEXT NOT NULL DEFAULT ''"
            )
        if "source_url" not in media_columns:
            connection.execute(
                "ALTER TABLE media_assets ADD COLUMN source_url TEXT NOT NULL DEFAULT ''"
            )
        connection.execute("UPDATE media_assets SET state = 'healthy' WHERE state = 'available'")
        rows = connection.execute(
            "SELECT id, components_json FROM questions WHERE plain_text = ''"
        ).fetchall()
        connection.executemany(
            "UPDATE questions SET plain_text = ? WHERE id = ?",
            [
                (_plain_text_from_components(str(row["components_json"])), int(row["id"]))
                for row in rows
            ],
        )

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("SQLite store is not open")
        return self._connection


def _plain_text_from_components(components_json: str) -> str:
    import json

    try:
        payload = json.loads(components_json)
    except (TypeError, ValueError):
        return ""
    components = payload.get("components", []) if isinstance(payload, dict) else []
    if not isinstance(components, list):
        return ""
    for component in components:
        if not isinstance(component, dict) or str(component.get("type", "")).lower() != "plain":
            continue
        data = component.get("data", {})
        if isinstance(data, dict):
            return str(data.get("text", "")).strip()
    return ""
