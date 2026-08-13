from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from new_chat_learning.constants import SCHEMA_VERSION
from new_chat_learning.domain.message import (
    NormalizedMessage,
    canonical_json,
    normalized_components_key,
)
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

CREATE TABLE IF NOT EXISTS answer_media (
    answer_id INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    component_index INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    answer_sendable_without_invalid INTEGER NOT NULL DEFAULT 0
        CHECK(answer_sendable_without_invalid IN (0, 1)),
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(answer_id, component_index)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reply_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    group_id TEXT NOT NULL,
    sent_message_id TEXT NOT NULL,
    answer_id INTEGER REFERENCES answers(id) ON DELETE SET NULL,
    question_id INTEGER REFERENCES questions(id) ON DELETE SET NULL,
    state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    UNIQUE(platform, group_id, sent_message_id)
);

CREATE TABLE IF NOT EXISTS legacy_imports (
    import_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    staging_sha256 TEXT NOT NULL,
    question_count INTEGER NOT NULL,
    answer_count INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blacklist_state (
    scope TEXT NOT NULL,
    group_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0 CHECK(blocked IN (0, 1)),
    manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(scope, group_id, user_id)
);

CREATE TABLE IF NOT EXISTS filter_hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    rule_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_questions_group ON questions(group_id);
CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question_id);
CREATE INDEX IF NOT EXISTS idx_contributions_user ON contributions(group_id, user_id);
CREATE INDEX IF NOT EXISTS idx_media_state ON media_assets(state);
CREATE INDEX IF NOT EXISTS idx_answer_media_state ON answer_media(state);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_reply_records_recent
ON reply_records(platform, group_id, state, id DESC);
CREATE INDEX IF NOT EXISTS idx_filter_hits_recent ON filter_hits(created_at);
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
            self._connection = self._create_connection()

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
                "answer_media",
                "audit_log",
                "reply_records",
                "legacy_imports",
                "blacklist_state",
                "filter_hits",
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

    async def is_blacklisted(self, *, group_id: str, user_id: str, scope: str) -> bool:
        scope_group = str(group_id) if scope == "group" else ""
        async with self._lock:
            row = self._require_connection().execute(
                "SELECT blocked FROM blacklist_state WHERE scope = ? AND group_id = ? "
                "AND user_id = ?",
                (scope, scope_group, str(user_id)),
            ).fetchone()
            return bool(row and row["blocked"])

    async def record_sensitive_hit(
        self,
        *,
        group_id: str,
        user_id: str,
        scope: str,
        threshold: int,
    ) -> dict[str, Any]:
        scope_group = str(group_id) if scope == "group" else ""
        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO blacklist_state(scope, group_id, user_id, hit_count) "
                    "VALUES(?, ?, ?, 1) ON CONFLICT(scope, group_id, user_id) DO UPDATE SET "
                    "hit_count=blacklist_state.hit_count + 1, updated_at=CURRENT_TIMESTAMP",
                    (scope, scope_group, str(user_id)),
                )
                row = connection.execute(
                    "SELECT hit_count, blocked, manual FROM blacklist_state "
                    "WHERE scope = ? AND group_id = ? AND user_id = ?",
                    (scope, scope_group, str(user_id)),
                ).fetchone()
                blocked = bool(row["blocked"]) or int(row["hit_count"]) >= int(threshold)
                if blocked and not row["blocked"]:
                    connection.execute(
                        "UPDATE blacklist_state SET blocked = 1, updated_at=CURRENT_TIMESTAMP "
                        "WHERE scope = ? AND group_id = ? AND user_id = ?",
                        (scope, scope_group, str(user_id)),
                    )
                connection.execute(
                    "INSERT INTO filter_hits(group_id, user_id, rule_type, direction) "
                    "VALUES(?, ?, 'sensitive', 'learning')",
                    (str(group_id), str(user_id)),
                )
                connection.commit()
                return {
                    "hit_count": int(row["hit_count"]),
                    "blocked": blocked,
                    "manual": bool(row["manual"]),
                }
            except Exception:
                connection.rollback()
                raise

    async def record_filter_hit(
        self, *, group_id: str, user_id: str, rule_type: str, direction: str
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            connection.execute(
                "INSERT INTO filter_hits(group_id, user_id, rule_type, direction) "
                "VALUES(?, ?, ?, ?)",
                (str(group_id), str(user_id), str(rule_type), str(direction)),
            )
            connection.commit()

    async def blacklist_entries(self) -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._require_connection().execute(
                "SELECT scope, group_id, user_id, hit_count, blocked, manual, updated_at "
                "FROM blacklist_state ORDER BY blocked DESC, updated_at DESC, user_id"
            ).fetchall()
            return [dict(row) for row in rows]

    async def filter_hit_statistics(self) -> dict[str, int]:
        async with self._lock:
            rows = self._require_connection().execute(
                "SELECT direction || ':' || rule_type AS key, COUNT(*) AS count "
                "FROM filter_hits GROUP BY direction, rule_type ORDER BY direction, rule_type"
            ).fetchall()
            return {str(row["key"]): int(row["count"]) for row in rows}

    async def set_blacklist_entry(
        self,
        *,
        group_id: str,
        user_id: str,
        scope: str,
        blocked: bool,
        actor_id: str,
    ) -> dict[str, Any]:
        if scope not in {"global", "group"}:
            raise ValueError("invalid_scope")
        scope_group = str(group_id) if scope == "group" else ""
        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO blacklist_state(scope, group_id, user_id, hit_count, blocked, manual) "
                    "VALUES(?, ?, ?, 0, ?, ?) ON CONFLICT(scope, group_id, user_id) DO UPDATE SET "
                    "blocked=excluded.blocked, manual=excluded.manual, "
                    "hit_count=CASE WHEN excluded.blocked = 0 THEN 0 ELSE blacklist_state.hit_count END, "
                    "updated_at=CURRENT_TIMESTAMP",
                    (scope, scope_group, str(user_id), int(blocked), int(blocked)),
                )
                self._insert_audit(
                    connection,
                    actor_id=actor_id,
                    action="blacklist_block" if blocked else "blacklist_unblock",
                    target=f"user:{user_id}",
                    details={"scope": scope, "group_id": scope_group},
                )
                connection.commit()
                return {"scope": scope, "group_id": scope_group, "user_id": str(user_id), "blocked": blocked}
            except Exception:
                connection.rollback()
                raise

    async def record_audit(
        self,
        *,
        actor_id: str,
        action: str,
        target: str,
        details: dict[str, Any],
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            self._insert_audit(
                connection,
                actor_id=str(actor_id),
                action=str(action),
                target=str(target),
                details=details,
            )
            connection.commit()

    async def import_legacy_jsonl(
        self,
        *,
        import_id: str,
        group_id: str,
        source_name: str,
        staging_path: Path,
        staging_sha256: str,
        actor_id: str,
    ) -> dict[str, Any]:
        async with self._lock:
            connection = self._require_connection()
            return await asyncio.to_thread(
                self._import_legacy_jsonl_sync,
                connection,
                import_id=import_id,
                group_id=str(group_id),
                source_name=source_name,
                staging_path=Path(staging_path),
                staging_sha256=staging_sha256,
                actor_id=str(actor_id),
            )

    async def backup_to(self, destination: Path) -> Path:
        async with self._lock:
            connection = self._require_connection()
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup = sqlite3.connect(destination)
            try:
                connection.backup(backup)
                integrity = backup.execute("PRAGMA quick_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(f"backup_integrity:{integrity}")
            finally:
                backup.close()
            return destination

    async def restore_from_backup(
        self,
        *,
        source: Path,
        safety_backup: Path,
        actor_id: str,
    ) -> dict[str, Any]:
        async with self._lock:
            connection = self._require_connection()
            source = Path(source)
            safety_backup = Path(safety_backup)
            temporary = self.path.with_suffix(".restore.tmp")
            safety_backup.parent.mkdir(parents=True, exist_ok=True)
            backup = sqlite3.connect(safety_backup)
            try:
                connection.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("safety_backup_integrity")
            finally:
                backup.close()
            replaced = False
            try:
                await asyncio.to_thread(shutil.copy2, source, temporary)
                candidate = sqlite3.connect(temporary)
                try:
                    if candidate.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("backup_integrity_failed")
                    schema_row = candidate.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()
                    if schema_row is None:
                        raise ValueError("backup_schema_missing")
                    if not 1 <= int(schema_row[0]) <= SCHEMA_VERSION:
                        raise ValueError("backup_schema_unsupported")
                finally:
                    candidate.close()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.close()
                self._connection = None
                self._remove_sidecars()
                os.replace(temporary, self.path)
                replaced = True
                restored = self._create_connection()
                self._connection = restored
                self._insert_audit(
                    restored,
                    actor_id=str(actor_id),
                    action="restore_database_backup",
                    target="database",
                    details={
                        "backup_name": source.name,
                        "safety_backup_name": safety_backup.name,
                    },
                )
                restored.commit()
                return {
                    "restored": True,
                    "backup_name": source.name,
                    "safety_backup_name": safety_backup.name,
                    "schema_version": int(
                        restored.execute(
                            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                        ).fetchone()[0]
                    ),
                }
            except Exception:
                if temporary.exists():
                    temporary.unlink()
                if replaced:
                    if self._connection is not None:
                        self._connection.close()
                        self._connection = None
                    rollback = self.path.with_suffix(".rollback.tmp")
                    await asyncio.to_thread(shutil.copy2, safety_backup, rollback)
                    self._remove_sidecars()
                    os.replace(rollback, self.path)
                    self._connection = self._create_connection()
                elif self._connection is None:
                    self._connection = self._create_connection()
                raise

    def _create_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        try:
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
            return connection
        except Exception:
            connection.close()
            raise

    def _remove_sidecars(self) -> None:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()

    def _import_legacy_jsonl_sync(
        self,
        connection: sqlite3.Connection,
        *,
        import_id: str,
        group_id: str,
        source_name: str,
        staging_path: Path,
        staging_sha256: str,
        actor_id: str,
    ) -> dict[str, Any]:
        import hashlib
        import json

        digest = hashlib.sha256()
        with staging_path.open("rb") as stream:
            for raw_line in stream:
                digest.update(raw_line)
        if digest.hexdigest() != staging_sha256:
            raise ValueError("staging_checksum_mismatch")

        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM legacy_imports WHERE import_id = ?", (import_id,)
            ).fetchone():
                connection.rollback()
                return {"imported": False, "reason": "already_imported"}
            connection.execute(
                "INSERT INTO groups(group_id) VALUES(?) ON CONFLICT(group_id) DO NOTHING",
                (group_id,),
            )
            question_count = 0
            answer_count = 0
            with staging_path.open("rb") as stream:
                for raw_line in stream:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict) or not isinstance(
                        record.get("answers"), list
                    ):
                        raise TypeError("invalid_staging_record")
                    question_id = self._merge_legacy_question(connection, group_id, record)
                    question_count += 1
                    for answer in record["answers"]:
                        if not isinstance(answer, dict):
                            raise TypeError("invalid_staging_answer")
                        self._merge_legacy_answer(connection, question_id, answer)
                        answer_count += 1
            connection.execute(
                "INSERT INTO legacy_imports(import_id, group_id, source_name, staging_sha256, "
                "question_count, answer_count, actor_id) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    import_id,
                    group_id,
                    source_name,
                    staging_sha256,
                    question_count,
                    answer_count,
                    actor_id,
                ),
            )
            self._insert_audit(
                connection,
                actor_id=actor_id,
                action="import_legacy_library",
                target=f"group:{group_id}",
                details={
                    "import_id": import_id,
                    "source_name": source_name,
                    "question_count": question_count,
                    "answer_count": answer_count,
                },
            )
            connection.commit()
            return {
                "imported": True,
                "question_count": question_count,
                "answer_count": answer_count,
            }
        except Exception:
            connection.rollback()
            raise

    async def register_reply(
        self,
        *,
        platform: str,
        group_id: str,
        sent_message_id: str,
        answer_id: int,
        question_id: int,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            connection.execute(
                "INSERT INTO reply_records(platform, group_id, sent_message_id, answer_id, "
                "question_id) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(platform, group_id, sent_message_id) DO UPDATE SET "
                "answer_id=excluded.answer_id, question_id=excluded.question_id, "
                "state='active', deleted_at=NULL",
                (platform, group_id, sent_message_id, answer_id, question_id),
            )
            connection.commit()

    async def recent_reply_message_id(
        self,
        *,
        platform: str,
        group_id: str,
        position: int,
    ) -> str | None:
        async with self._lock:
            connection = self._require_connection()
            row = connection.execute(
                "SELECT sent_message_id FROM reply_records "
                "WHERE platform = ? AND group_id = ? AND state = 'active' "
                "ORDER BY id DESC LIMIT 1 OFFSET ?",
                (platform, group_id, max(0, position - 1)),
            ).fetchone()
            return str(row["sent_message_id"]) if row is not None else None

    async def fast_delete_reply(
        self,
        *,
        platform: str,
        group_id: str,
        sent_message_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                record = connection.execute(
                    "SELECT id, answer_id, question_id FROM reply_records "
                    "WHERE platform = ? AND group_id = ? AND sent_message_id = ? "
                    "AND state = 'active'",
                    (platform, group_id, sent_message_id),
                ).fetchone()
                if record is None or record["answer_id"] is None:
                    connection.rollback()
                    return {"deleted": False, "reason": "not_found"}
                answer_id = int(record["answer_id"])
                question_id = int(record["question_id"])
                connection.execute(
                    "UPDATE reply_records SET state = 'deleted', deleted_at=CURRENT_TIMESTAMP "
                    "WHERE answer_id = ? AND state = 'active'",
                    (answer_id,),
                )
                deleted = connection.execute(
                    "DELETE FROM answers WHERE id = ? AND question_id = ?",
                    (answer_id, question_id),
                ).rowcount
                if not deleted:
                    connection.execute(
                        "UPDATE reply_records SET state = 'missing', deleted_at=CURRENT_TIMESTAMP "
                        "WHERE id = ?",
                        (int(record["id"]),),
                    )
                    connection.commit()
                    return {"deleted": False, "reason": "answer_missing"}
                orphan_removed = bool(
                    connection.execute(
                        "DELETE FROM questions WHERE id = ? "
                        "AND NOT EXISTS(SELECT 1 FROM answers WHERE question_id = ?)",
                        (question_id, question_id),
                    ).rowcount
                )
                import json

                connection.execute(
                    "INSERT INTO audit_log(actor_id, action, target, details_json) "
                    "VALUES(?, 'fast_delete_answer', ?, ?)",
                    (
                        actor_id,
                        f"answer:{answer_id}",
                        json.dumps(
                            {
                                "platform": platform,
                                "group_id": group_id,
                                "sent_message_id": sent_message_id,
                                "question_id": question_id,
                                "orphan_question_removed": orphan_removed,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                )
                connection.commit()
                return {
                    "deleted": True,
                    "answer_id": answer_id,
                    "question_id": question_id,
                    "orphan_question_removed": orphan_removed,
                }
            except Exception:
                connection.rollback()
                raise

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

    async def answer_component_batch(
        self,
        *,
        group_id: str,
        after_answer_id: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                "SELECT a.id AS answer_id, a.question_id, a.components_json, "
                "(SELECT COUNT(*) FROM answers AS siblings "
                "WHERE siblings.question_id = a.question_id) AS question_answer_count "
                "FROM answers AS a "
                "JOIN questions AS q ON q.id = a.question_id "
                "WHERE q.group_id = ? AND a.id > ? ORDER BY a.id LIMIT ?",
                (str(group_id), int(after_answer_id), max(1, min(2000, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]

    async def apply_filter_cleanup(
        self,
        *,
        plan_id: str,
        group_id: str,
        actor_id: str,
        config_revision: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        import hashlib

        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                affected_question_ids: set[int] = set()
                rule_type_counts: dict[str, int] = {}
                for operation in operations:
                    answer_id = int(operation["answer_id"])
                    row = connection.execute(
                        "SELECT a.question_id, a.components_json FROM answers AS a "
                        "JOIN questions AS q ON q.id = a.question_id "
                        "WHERE a.id = ? AND q.group_id = ?",
                        (answer_id, str(group_id)),
                    ).fetchone()
                    if row is None:
                        raise ValueError("cleanup_plan_stale")
                    components_json = str(row["components_json"])
                    digest = hashlib.sha256(components_json.encode("utf-8")).hexdigest()
                    if (
                        digest != str(operation["components_sha256"])
                        or int(row["question_id"]) != int(operation["question_id"])
                    ):
                        raise ValueError("cleanup_plan_stale")
                    question_id = int(row["question_id"])
                    affected_question_ids.add(question_id)
                    rule_type = str(operation["rule_type"])
                    rule_type_counts[rule_type] = rule_type_counts.get(rule_type, 0) + 1
                    self._delete_answer_for_cleanup(connection, answer_id)
                orphan_questions = 0
                for question_id in affected_question_ids:
                    orphan_questions += connection.execute(
                        "DELETE FROM questions WHERE id = ? "
                        "AND NOT EXISTS(SELECT 1 FROM answers WHERE question_id = ?)",
                        (question_id, question_id),
                    ).rowcount
                details = {
                    "plan_id": plan_id,
                    "group_id": str(group_id),
                    "config_revision": str(config_revision),
                    "deleted_answers": len(operations),
                    "affected_questions": len(affected_question_ids),
                    "orphan_questions": orphan_questions,
                    "rule_type_counts": dict(sorted(rule_type_counts.items())),
                }
                self._insert_audit(
                    connection,
                    actor_id=actor_id,
                    action="cleanup_filtered_answers",
                    target=f"group:{group_id}",
                    details=details,
                )
                connection.commit()
                return {"applied": True, **details}
            except Exception:
                connection.rollback()
                raise

    async def replace_answer_media(
        self,
        *,
        answer_id: int,
        references: list[dict[str, Any]],
        answer_sendable_without_invalid: bool,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            connection.execute("DELETE FROM answer_media WHERE answer_id = ?", (int(answer_id),))
            connection.executemany(
                "INSERT INTO answer_media(answer_id, component_index, media_type, "
                "content_hash, relative_path, source_url, state, reason, "
                "answer_sendable_without_invalid, checked_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                [
                    (
                        int(answer_id),
                        int(reference["component_index"]),
                        str(reference["media_type"]),
                        str(reference.get("content_hash", "")),
                        str(reference.get("relative_path", "")),
                        str(reference.get("source_url", "")),
                        str(reference["state"]),
                        str(reference.get("reason", "")),
                        int(answer_sendable_without_invalid),
                    )
                    for reference in references
                ],
            )
            for reference in references:
                content_hash = str(reference.get("content_hash", ""))
                if content_hash:
                    connection.execute(
                        "UPDATE media_assets SET state = ?, checked_at = CURRENT_TIMESTAMP "
                        "WHERE content_hash = ? AND relative_path = ?",
                        (
                            str(reference["state"]),
                            content_hash,
                            str(reference.get("relative_path", "")),
                        ),
                    )
            connection.commit()

    async def media_health_preview(self, group_id: str) -> dict[str, Any]:
        async with self._lock:
            connection = self._require_connection()
            params = (str(group_id),)
            summary = connection.execute(
                "SELECT COUNT(*) AS media_components, "
                "COUNT(DISTINCT am.answer_id) AS affected_answers, "
                "COUNT(DISTINCT q.id) AS affected_questions, "
                "COUNT(DISTINCT q.group_id) AS affected_groups, "
                "COUNT(DISTINCT CASE WHEN am.answer_sendable_without_invalid = 0 "
                "THEN am.answer_id END) AS answers_becoming_empty "
                "FROM answer_media AS am JOIN answers AS a ON a.id = am.answer_id "
                "JOIN questions AS q ON q.id = a.question_id "
                "WHERE q.group_id = ? AND am.state != 'healthy'",
                params,
            ).fetchone()
            states = connection.execute(
                "SELECT am.state, COUNT(*) AS count FROM answer_media AS am "
                "JOIN answers AS a ON a.id = am.answer_id "
                "JOIN questions AS q ON q.id = a.question_id "
                "WHERE q.group_id = ? GROUP BY am.state ORDER BY am.state",
                params,
            ).fetchall()
            result = dict(summary)
            result["states"] = {str(row["state"]): int(row["count"]) for row in states}
            return result

    async def media_cleanup_candidates(self, group_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            connection = self._require_connection()
            answers = connection.execute(
                "SELECT DISTINCT a.id AS answer_id, a.question_id, a.components_json, a.weight "
                "FROM answer_media AS am JOIN answers AS a ON a.id = am.answer_id "
                "JOIN questions AS q ON q.id = a.question_id "
                "WHERE q.group_id = ? AND am.state != 'healthy' ORDER BY a.id",
                (str(group_id),),
            ).fetchall()
            result = []
            for answer in answers:
                references = connection.execute(
                    "SELECT component_index, state, reason FROM answer_media "
                    "WHERE answer_id = ? AND state != 'healthy' ORDER BY component_index",
                    (int(answer["answer_id"]),),
                ).fetchall()
                item = dict(answer)
                item["invalid_references"] = [dict(row) for row in references]
                result.append(item)
            return result

    async def apply_media_cleanup(
        self,
        *,
        plan_id: str,
        group_id: str,
        actor_id: str,
        mode: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        import hashlib
        import json

        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                removed_components = 0
                deleted_answers = 0
                updated_answers = 0
                merged_answers = 0
                affected_question_ids: set[int] = set()
                for operation in operations:
                    answer_id = int(operation["answer_id"])
                    row = connection.execute(
                        "SELECT a.question_id, a.components_json, a.weight FROM answers AS a "
                        "JOIN questions AS q ON q.id = a.question_id "
                        "WHERE a.id = ? AND q.group_id = ?",
                        (answer_id, str(group_id)),
                    ).fetchone()
                    if row is None:
                        raise ValueError("cleanup_plan_stale")
                    components_json = str(row["components_json"])
                    digest = hashlib.sha256(components_json.encode("utf-8")).hexdigest()
                    if digest != str(operation["components_sha256"]):
                        raise ValueError("cleanup_plan_stale")
                    current_references = connection.execute(
                        "SELECT component_index, state FROM answer_media "
                        "WHERE answer_id = ? AND state != 'healthy' ORDER BY component_index",
                        (answer_id,),
                    ).fetchall()
                    current_signature = [
                        [int(reference["component_index"]), str(reference["state"])]
                        for reference in current_references
                    ]
                    if current_signature != operation["invalid_signature"]:
                        raise ValueError("cleanup_plan_stale")
                    question_id = int(row["question_id"])
                    affected_question_ids.add(question_id)
                    if mode == "drop-answer" or operation["action"] == "delete":
                        self._delete_answer_for_cleanup(connection, answer_id)
                        deleted_answers += 1
                        removed_components += len(current_signature)
                        continue
                    payload = json.loads(components_json)
                    components = payload.get("components", [])
                    invalid_indices = {item[0] for item in current_signature}
                    remaining = [
                        component
                        for index, component in enumerate(components)
                        if index not in invalid_indices
                    ]
                    if not remaining:
                        self._delete_answer_for_cleanup(connection, answer_id)
                        deleted_answers += 1
                        removed_components += len(invalid_indices)
                        continue
                    updated_json = canonical_json(
                        {"schema_version": int(payload.get("schema_version", 1)), "components": remaining}
                    )
                    updated_key = _stored_answer_key(remaining)
                    duplicate = connection.execute(
                        "SELECT id, weight FROM answers WHERE question_id = ? "
                        "AND normalized_key = ? AND id != ?",
                        (question_id, updated_key, answer_id),
                    ).fetchone()
                    if duplicate is not None:
                        survivor_id = int(duplicate["id"])
                        connection.execute(
                            "UPDATE answers SET weight = weight + ?, updated_at=CURRENT_TIMESTAMP "
                            "WHERE id = ?",
                            (int(row["weight"]), survivor_id),
                        )
                        connection.execute(
                            "UPDATE contributions SET answer_id = ? WHERE answer_id = ?",
                            (survivor_id, answer_id),
                        )
                        connection.execute(
                            "UPDATE reply_records SET answer_id = ? WHERE answer_id = ?",
                            (survivor_id, answer_id),
                        )
                        connection.execute("DELETE FROM answers WHERE id = ?", (answer_id,))
                        merged_answers += 1
                    else:
                        connection.execute(
                            "UPDATE answers SET components_json = ?, normalized_key = ?, "
                            "updated_at=CURRENT_TIMESTAMP WHERE id = ?",
                            (updated_json, updated_key, answer_id),
                        )
                        connection.execute("DELETE FROM answer_media WHERE answer_id = ?", (answer_id,))
                        updated_answers += 1
                    removed_components += len(invalid_indices)
                orphan_questions = 0
                for question_id in affected_question_ids:
                    orphan_questions += connection.execute(
                        "DELETE FROM questions WHERE id = ? "
                        "AND NOT EXISTS(SELECT 1 FROM answers WHERE question_id = ?)",
                        (question_id, question_id),
                    ).rowcount
                details = {
                    "plan_id": plan_id,
                    "group_id": str(group_id),
                    "mode": mode,
                    "removed_components": removed_components,
                    "updated_answers": updated_answers,
                    "deleted_answers": deleted_answers,
                    "merged_answers": merged_answers,
                    "orphan_questions": orphan_questions,
                }
                self._insert_audit(
                    connection,
                    actor_id=actor_id,
                    action="cleanup_invalid_media",
                    target=f"group:{group_id}",
                    details=details,
                )
                connection.commit()
                return {"applied": True, **details}
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _delete_answer_for_cleanup(connection: sqlite3.Connection, answer_id: int) -> None:
        connection.execute(
            "UPDATE reply_records SET state = 'deleted', deleted_at=CURRENT_TIMESTAMP "
            "WHERE answer_id = ? AND state = 'active'",
            (int(answer_id),),
        )
        connection.execute("DELETE FROM answers WHERE id = ?", (int(answer_id),))

    async def search_questions(
        self,
        group_id: str,
        query: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            connection = self._require_connection()
            escaped = str(query).replace("\\", "\\\\").replace("%", "\\%").replace(
                "_", "\\_"
            )
            rows = connection.execute(
                "SELECT q.id AS question_id, q.plain_text, q.is_regex, q.frequency, "
                "COUNT(a.id) AS answer_count, COALESCE(SUM(a.weight), 0) AS total_weight "
                "FROM questions AS q LEFT JOIN answers AS a ON a.question_id = q.id "
                "WHERE q.group_id = ? AND q.plain_text LIKE ? ESCAPE '\\' "
                "GROUP BY q.id ORDER BY q.updated_at DESC, q.id DESC LIMIT ?",
                (str(group_id), f"%{escaped}%", max(1, min(50, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]

    async def question_detail(self, group_id: str, question_id: int) -> dict[str, Any] | None:
        async with self._lock:
            connection = self._require_connection()
            question = connection.execute(
                "SELECT id AS question_id, plain_text, components_json, is_regex, frequency "
                "FROM questions WHERE id = ? AND group_id = ?",
                (int(question_id), str(group_id)),
            ).fetchone()
            if question is None:
                return None
            answers = connection.execute(
                "SELECT id AS answer_id, components_json, weight, created_at, updated_at "
                "FROM answers WHERE question_id = ? ORDER BY weight DESC, id",
                (int(question_id),),
            ).fetchall()
            result = dict(question)
            result["answers"] = [dict(row) for row in answers]
            return result

    async def answer_detail(self, group_id: str, answer_id: int) -> dict[str, Any] | None:
        async with self._lock:
            connection = self._require_connection()
            row = connection.execute(
                "SELECT a.id AS answer_id, a.question_id, a.weight "
                "FROM answers AS a JOIN questions AS q ON q.id = a.question_id "
                "WHERE a.id = ? AND q.group_id = ?",
                (int(answer_id), str(group_id)),
            ).fetchone()
            return dict(row) if row is not None else None

    async def add_custom_pair(
        self,
        *,
        group_id: str,
        actor_id: str,
        question_key: str,
        question_components_json: str,
        question_text: str,
        answer_key: str,
        answer_components_json: str,
        is_regex: bool,
    ) -> dict[str, Any]:
        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO groups(group_id) VALUES(?) ON CONFLICT(group_id) DO NOTHING",
                    (str(group_id),),
                )
                connection.execute(
                    "INSERT INTO questions(group_id, normalized_key, components_json, "
                    "plain_text, is_regex) VALUES(?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id, normalized_key) DO UPDATE SET "
                    "components_json=excluded.components_json, plain_text=excluded.plain_text, "
                    "is_regex=excluded.is_regex, updated_at=CURRENT_TIMESTAMP",
                    (
                        str(group_id),
                        question_key,
                        question_components_json,
                        question_text,
                        int(is_regex),
                    ),
                )
                question_id = int(
                    connection.execute(
                        "SELECT id FROM questions WHERE group_id = ? AND normalized_key = ?",
                        (str(group_id), question_key),
                    ).fetchone()[0]
                )
                existing = connection.execute(
                    "SELECT id, weight FROM answers WHERE question_id = ? AND normalized_key = ?",
                    (question_id, answer_key),
                ).fetchone()
                if existing is None:
                    cursor = connection.execute(
                        "INSERT INTO answers(question_id, components_json, normalized_key) "
                        "VALUES(?, ?, ?)",
                        (question_id, answer_components_json, answer_key),
                    )
                    answer_id = int(cursor.lastrowid)
                    weight = 1
                    created = True
                else:
                    answer_id = int(existing["id"])
                    weight = int(existing["weight"]) + 1
                    connection.execute(
                        "UPDATE answers SET weight = ?, components_json = ?, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id = ?",
                        (weight, answer_components_json, answer_id),
                    )
                    created = False
                self._insert_audit(
                    connection,
                    actor_id=actor_id,
                    action="add_custom_pair",
                    target=f"answer:{answer_id}",
                    details={
                        "group_id": str(group_id),
                        "question_id": question_id,
                        "is_regex": bool(is_regex),
                        "created": created,
                        "weight": weight,
                    },
                )
                connection.commit()
                return {
                    "question_id": question_id,
                    "answer_id": answer_id,
                    "weight": weight,
                    "created": created,
                }
            except Exception:
                connection.rollback()
                raise

    async def set_answer_weight(
        self,
        *,
        group_id: str,
        actor_id: str,
        answer_id: int,
        weight: int,
    ) -> bool:
        if int(weight) < 1:
            return False
        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT a.weight FROM answers AS a JOIN questions AS q "
                    "ON q.id = a.question_id WHERE a.id = ? AND q.group_id = ?",
                    (int(answer_id), str(group_id)),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return False
                connection.execute(
                    "UPDATE answers SET weight = ?, updated_at=CURRENT_TIMESTAMP WHERE id = ?",
                    (int(weight), int(answer_id)),
                )
                self._insert_audit(
                    connection,
                    actor_id=actor_id,
                    action="set_answer_weight",
                    target=f"answer:{int(answer_id)}",
                    details={
                        "group_id": str(group_id),
                        "old_weight": int(row["weight"]),
                        "new_weight": int(weight),
                    },
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    async def delete_answer(
        self,
        *,
        group_id: str,
        actor_id: str,
        answer_id: int,
    ) -> dict[str, Any]:
        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT a.question_id FROM answers AS a JOIN questions AS q "
                    "ON q.id = a.question_id WHERE a.id = ? AND q.group_id = ?",
                    (int(answer_id), str(group_id)),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return {"deleted": False, "orphan_question_removed": False}
                question_id = int(row["question_id"])
                connection.execute(
                    "UPDATE reply_records SET state = 'deleted', deleted_at=CURRENT_TIMESTAMP "
                    "WHERE answer_id = ? AND state = 'active'",
                    (int(answer_id),),
                )
                connection.execute("DELETE FROM answers WHERE id = ?", (int(answer_id),))
                orphan_removed = bool(
                    connection.execute(
                        "DELETE FROM questions WHERE id = ? "
                        "AND NOT EXISTS(SELECT 1 FROM answers WHERE question_id = ?)",
                        (question_id, question_id),
                    ).rowcount
                )
                self._insert_audit(
                    connection,
                    actor_id=actor_id,
                    action="delete_answer",
                    target=f"answer:{int(answer_id)}",
                    details={
                        "group_id": str(group_id),
                        "question_id": question_id,
                        "orphan_question_removed": orphan_removed,
                    },
                )
                connection.commit()
                return {"deleted": True, "orphan_question_removed": orphan_removed}
            except Exception:
                connection.rollback()
                raise

    async def delete_question(
        self,
        *,
        group_id: str,
        actor_id: str,
        question_id: int,
    ) -> bool:
        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT COUNT(a.id) AS answer_count FROM questions AS q "
                    "LEFT JOIN answers AS a ON a.question_id = q.id "
                    "WHERE q.id = ? AND q.group_id = ? GROUP BY q.id",
                    (int(question_id), str(group_id)),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return False
                connection.execute(
                    "UPDATE reply_records SET state = 'deleted', deleted_at=CURRENT_TIMESTAMP "
                    "WHERE question_id = ? AND state = 'active'",
                    (int(question_id),),
                )
                connection.execute("DELETE FROM questions WHERE id = ?", (int(question_id),))
                self._insert_audit(
                    connection,
                    actor_id=actor_id,
                    action="delete_question",
                    target=f"question:{int(question_id)}",
                    details={
                        "group_id": str(group_id),
                        "answer_count": int(row["answer_count"]),
                    },
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    async def member_contribution_preview(
        self, *, group_id: str, user_id: str
    ) -> dict[str, Any]:
        async with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                "SELECT c.answer_id, a.question_id, COUNT(c.id) AS contribution_count, "
                "(SELECT COUNT(*) FROM contributions AS all_c "
                "WHERE all_c.answer_id = c.answer_id) AS total_contribution_count, "
                "a.weight AS current_weight FROM contributions AS c "
                "JOIN answers AS a ON a.id = c.answer_id "
                "JOIN questions AS q ON q.id = a.question_id "
                "WHERE c.group_id = ? AND c.user_id = ? AND q.group_id = ? "
                "GROUP BY c.answer_id, a.question_id, a.weight ORDER BY c.answer_id",
                (str(group_id), str(user_id), str(group_id)),
            ).fetchall()
            operations = [
                {
                    "answer_id": int(row["answer_id"]),
                    "question_id": int(row["question_id"]),
                    "contribution_count": int(row["contribution_count"]),
                    "total_contribution_count": int(row["total_contribution_count"]),
                    "current_weight": int(row["current_weight"]),
                }
                for row in rows
            ]
            pending_messages = int(
                connection.execute(
                    "SELECT COUNT(*) FROM pending_messages "
                    "WHERE group_id = ? AND sender_id = ?",
                    (str(group_id), str(user_id)),
                ).fetchone()[0]
            )
            affected_questions = {item["question_id"] for item in operations}
            deleted_answer_ids = {
                item["answer_id"]
                for item in operations
                if item["contribution_count"] >= item["current_weight"]
                and item["contribution_count"] == item["total_contribution_count"]
            }
            questions_becoming_empty = 0
            for question_id in affected_questions:
                answer_ids = {
                    int(row[0])
                    for row in connection.execute(
                        "SELECT id FROM answers WHERE question_id = ?", (question_id,)
                    )
                }
                questions_becoming_empty += int(
                    bool(answer_ids) and answer_ids.issubset(deleted_answer_ids)
                )
            return {
                "group_id": str(group_id),
                "user_id": str(user_id),
                "contributions": sum(item["contribution_count"] for item in operations),
                "affected_answers": len(operations),
                "affected_questions": len(affected_questions),
                "answers_becoming_empty": len(deleted_answer_ids),
                "questions_becoming_empty": questions_becoming_empty,
                "pending_messages": pending_messages,
                "operations": operations,
            }

    async def apply_member_contribution_cleanup(
        self,
        *,
        plan_id: str,
        group_id: str,
        user_id: str,
        actor_id: str,
        operations: list[dict[str, Any]],
        pending_messages: int,
    ) -> dict[str, Any]:
        async with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._member_contribution_operations(
                    connection, str(group_id), str(user_id)
                )
                current_pending = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM pending_messages "
                        "WHERE group_id = ? AND sender_id = ?",
                        (str(group_id), str(user_id)),
                    ).fetchone()[0]
                )
                if current != operations or current_pending != int(pending_messages):
                    raise ValueError("contribution_plan_stale")
                deleted_answers = 0
                reduced_answers = 0
                removed_contributions = 0
                question_ids: set[int] = set()
                for operation in operations:
                    answer_id = int(operation["answer_id"])
                    question_id = int(operation["question_id"])
                    count = int(operation["contribution_count"])
                    weight = int(operation["current_weight"])
                    question_ids.add(question_id)
                    removed_contributions += count
                    total_count = int(operation["total_contribution_count"])
                    if count >= weight and count == total_count:
                        connection.execute(
                            "UPDATE reply_records SET state = 'deleted', "
                            "deleted_at = CURRENT_TIMESTAMP "
                            "WHERE answer_id = ? AND state = 'active'",
                            (answer_id,),
                        )
                        connection.execute("DELETE FROM answers WHERE id = ?", (answer_id,))
                        deleted_answers += 1
                    else:
                        connection.execute(
                            "DELETE FROM contributions WHERE answer_id = ? "
                            "AND group_id = ? AND user_id = ?",
                            (answer_id, str(group_id), str(user_id)),
                        )
                        connection.execute(
                            "UPDATE answers SET weight = weight - ?, "
                            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (min(count, max(0, weight - 1)), answer_id),
                        )
                        reduced_answers += 1
                removed_pending = connection.execute(
                    "DELETE FROM pending_messages WHERE group_id = ? AND sender_id = ?",
                    (str(group_id), str(user_id)),
                ).rowcount
                orphan_questions = 0
                for question_id in question_ids:
                    orphan_questions += connection.execute(
                        "DELETE FROM questions WHERE id = ? "
                        "AND NOT EXISTS(SELECT 1 FROM answers WHERE question_id = ?)",
                        (question_id, question_id),
                    ).rowcount
                details = {
                    "plan_id": str(plan_id),
                    "group_id": str(group_id),
                    "user_id": str(user_id),
                    "removed_contributions": removed_contributions,
                    "reduced_answers": reduced_answers,
                    "deleted_answers": deleted_answers,
                    "orphan_questions": orphan_questions,
                    "removed_pending_messages": int(removed_pending),
                }
                self._insert_audit(
                    connection,
                    actor_id=str(actor_id),
                    action="delete_member_contributions",
                    target=f"group:{group_id}:user:{user_id}",
                    details=details,
                )
                connection.commit()
                return {"applied": True, **details}
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _member_contribution_operations(
        connection: sqlite3.Connection, group_id: str, user_id: str
    ) -> list[dict[str, int]]:
        rows = connection.execute(
            "SELECT c.answer_id, a.question_id, COUNT(c.id) AS contribution_count, "
            "(SELECT COUNT(*) FROM contributions AS all_c "
            "WHERE all_c.answer_id = c.answer_id) AS total_contribution_count, "
            "a.weight AS current_weight FROM contributions AS c "
            "JOIN answers AS a ON a.id = c.answer_id "
            "JOIN questions AS q ON q.id = a.question_id "
            "WHERE c.group_id = ? AND c.user_id = ? AND q.group_id = ? "
            "GROUP BY c.answer_id, a.question_id, a.weight ORDER BY c.answer_id",
            (group_id, user_id, group_id),
        ).fetchall()
        return [
            {
                "answer_id": int(row["answer_id"]),
                "question_id": int(row["question_id"]),
                "contribution_count": int(row["contribution_count"]),
                "total_contribution_count": int(row["total_contribution_count"]),
                "current_weight": int(row["current_weight"]),
            }
            for row in rows
        ]

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
        self,
        message: NormalizedMessage,
        interval_seconds: int,
        *,
        answer_sender_ids: tuple[str, ...] = (),
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
                    sender_allowed = not answer_sender_ids or message.sender_id in answer_sender_ids
                    if 0 <= elapsed <= interval_seconds and sender_allowed:
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
                    elif not (0 <= elapsed <= interval_seconds):
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

    async def list_question_group_ids(self) -> list[str]:
        async with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                "SELECT DISTINCT group_id FROM questions ORDER BY group_id"
            ).fetchall()
            return [str(row["group_id"]) for row in rows]

    async def find_exact_answers(
        self,
        group_ids: tuple[str, ...],
        normalized_key: str,
    ) -> list[ReplyCandidate]:
        if not group_ids:
            return []
        async with self._lock:
            connection = self._require_connection()
            placeholders = ",".join("?" for _ in group_ids)
            rows = connection.execute(
                "SELECT a.id AS answer_id, a.question_id, a.weight, "
                f"COALESCE((SELECT SUM(aq.frequency) FROM questions AS aq "
                f"WHERE aq.group_id IN ({placeholders}) "
                "AND aq.normalized_key = a.normalized_key), 0) "
                "AS answer_question_frequency, a.components_json "
                "FROM answers AS a JOIN questions AS q ON q.id = a.question_id "
                f"WHERE q.group_id IN ({placeholders}) "
                "AND q.normalized_key = ? AND a.weight > 0 "
                "ORDER BY a.id",
                (*group_ids, *group_ids, normalized_key),
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

    async def find_matchable_questions(
        self,
        group_ids: tuple[str, ...],
    ) -> list[QuestionCandidate]:
        if not group_ids:
            return []
        async with self._lock:
            connection = self._require_connection()
            placeholders = ",".join("?" for _ in group_ids)
            rows = connection.execute(
                "SELECT q.id, q.normalized_key, q.plain_text, q.is_regex "
                "FROM questions AS q WHERE q.id IN ("
                "SELECT MIN(grouped.id) FROM questions AS grouped "
                f"WHERE grouped.group_id IN ({placeholders}) "
                "AND grouped.plain_text != '' GROUP BY grouped.normalized_key"
                ") ORDER BY q.id",
                group_ids,
            ).fetchall()
            return [
                QuestionCandidate(
                    question_id=int(row["id"]),
                    normalized_key=str(row["normalized_key"]),
                    plain_text=str(row["plain_text"]),
                    is_regex=bool(row["is_regex"]),
                )
                for row in rows
            ]

    async def find_answers_for_question(
        self,
        group_ids: tuple[str, ...],
        normalized_key: str,
    ) -> list[ReplyCandidate]:
        if not group_ids:
            return []
        async with self._lock:
            connection = self._require_connection()
            placeholders = ",".join("?" for _ in group_ids)
            rows = connection.execute(
                "SELECT a.id AS answer_id, a.question_id, a.weight, "
                f"COALESCE((SELECT SUM(aq.frequency) FROM questions AS aq "
                f"WHERE aq.group_id IN ({placeholders}) "
                "AND aq.normalized_key = a.normalized_key), 0) "
                "AS answer_question_frequency, "
                "a.components_json FROM answers AS a "
                "JOIN questions AS q ON q.id = a.question_id "
                f"WHERE q.group_id IN ({placeholders}) "
                "AND q.normalized_key = ? AND a.weight > 0 ORDER BY a.id",
                (*group_ids, *group_ids, normalized_key),
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
    def _merge_legacy_question(
        connection: sqlite3.Connection,
        group_id: str,
        record: dict[str, Any],
    ) -> int:
        frequency = max(1, int(record.get("frequency", 1)))
        connection.execute(
            "INSERT INTO questions(group_id, normalized_key, components_json, plain_text, "
            "is_regex, frequency) VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(group_id, normalized_key) DO UPDATE SET "
            "frequency=questions.frequency + excluded.frequency, "
            "plain_text=CASE WHEN questions.plain_text = '' THEN excluded.plain_text "
            "ELSE questions.plain_text END, "
            "is_regex=CASE WHEN excluded.is_regex = 1 THEN 1 ELSE questions.is_regex END, "
            "updated_at=CURRENT_TIMESTAMP",
            (
                group_id,
                str(record["normalized_key"]),
                str(record["components_json"]),
                str(record.get("plain_text", "")),
                int(bool(record.get("is_regex", False))),
                frequency,
            ),
        )
        row = connection.execute(
            "SELECT id FROM questions WHERE group_id = ? AND normalized_key = ?",
            (group_id, str(record["normalized_key"])),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _merge_legacy_answer(
        connection: sqlite3.Connection,
        question_id: int,
        answer: dict[str, Any],
    ) -> None:
        weight = max(1, int(answer.get("weight", 1)))
        connection.execute(
            "INSERT INTO answers(question_id, components_json, normalized_key, weight) "
            "VALUES(?, ?, ?, ?) ON CONFLICT(question_id, normalized_key) DO UPDATE SET "
            "weight=answers.weight + excluded.weight, updated_at=CURRENT_TIMESTAMP",
            (
                question_id,
                str(answer["components_json"]),
                str(answer["normalized_key"]),
                weight,
            ),
        )

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
        connection.execute(
            "CREATE TABLE IF NOT EXISTS answer_media ("
            "answer_id INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE, "
            "component_index INTEGER NOT NULL, media_type TEXT NOT NULL, "
            "content_hash TEXT NOT NULL DEFAULT '', relative_path TEXT NOT NULL DEFAULT '', "
            "source_url TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, "
            "reason TEXT NOT NULL DEFAULT '', answer_sendable_without_invalid INTEGER NOT NULL "
            "DEFAULT 0 CHECK(answer_sendable_without_invalid IN (0, 1)), "
            "checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY(answer_id, component_index))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_answer_media_state ON answer_media(state)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS blacklist_state (scope TEXT NOT NULL, "
            "group_id TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL, "
            "hit_count INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0 "
            "CHECK(blocked IN (0, 1)), manual INTEGER NOT NULL DEFAULT 0 "
            "CHECK(manual IN (0, 1)), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY(scope, group_id, user_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS filter_hits (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "group_id TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT '', rule_type TEXT NOT NULL, "
            "direction TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_filter_hits_recent ON filter_hits(created_at)"
        )
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

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        *,
        actor_id: str,
        action: str,
        target: str,
        details: dict[str, Any],
    ) -> None:
        import json

        connection.execute(
            "INSERT INTO audit_log(actor_id, action, target, details_json) VALUES(?, ?, ?, ?)",
            (
                str(actor_id),
                action,
                target,
                json.dumps(details, ensure_ascii=False, separators=(",", ":")),
            ),
        )


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


def _stored_answer_key(components: list[dict[str, Any]]) -> str:
    matching_components = []
    transient_fields = {
        "url",
        "path",
        "base64",
        "message_id",
        "time",
        "seq",
        "media_path",
        "content_hash",
        "media_state",
    }
    for component in components:
        if not isinstance(component, dict):
            continue
        data = component.get("data", {})
        if not isinstance(data, dict):
            data = {}
        matching_data = {
            str(key): value for key, value in data.items() if str(key) not in transient_fields
        }
        file_value = matching_data.get("file")
        if isinstance(file_value, str) and file_value.lower().startswith(
            ("http://", "https://", "file:", "base64://", "data:")
        ):
            matching_data.pop("file", None)
        matching_components.append(
            {"type": str(component.get("type", "")), "data": matching_data}
        )
    return normalized_components_key(matching_components)
