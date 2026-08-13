from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from new_chat_learning.constants import SCHEMA_VERSION
from new_chat_learning.infrastructure.database import SQLiteStore


class BackupService:
    def __init__(self, data_dir: Path, store: SQLiteStore) -> None:
        self.backup_dir = Path(data_dir) / "backups"
        self.store = store

    async def list_backups(self) -> list[dict[str, object]]:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        root = self.backup_dir.resolve()
        for path in self.backup_dir.glob("*.sqlite3"):
            if not path.is_file() or path.resolve().parent != root:
                continue
            stat = await asyncio.to_thread(path.stat)
            entries.append(
                {
                    "name": path.name,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, timezone.utc
                    ).isoformat(),
                    "kind": _backup_kind(path.name),
                }
            )
        return sorted(entries, key=lambda item: str(item["modified_at"]), reverse=True)[:500]

    async def inspect(self, name: str) -> dict[str, object]:
        path = self._safe_backup_path(name)
        return await asyncio.to_thread(_inspect_database, path)

    async def restore(self, *, name: str, actor_id: str) -> dict[str, object]:
        source = self._safe_backup_path(name)
        inspection = await asyncio.to_thread(_inspect_database, source)
        if not inspection["restorable"]:
            raise ValueError("backup_not_restorable")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safety_backup = self.backup_dir / f"before-restore-{timestamp}.sqlite3"
        return await self.store.restore_from_backup(
            source=source,
            safety_backup=safety_backup,
            actor_id=actor_id,
        )

    def _safe_backup_path(self, name: str) -> Path:
        value = str(name).strip()
        if not value or Path(value).name != value or not value.endswith(".sqlite3"):
            raise ValueError("invalid_backup_name")
        path = (self.backup_dir / value).resolve()
        root = self.backup_dir.resolve()
        if path.parent != root or not path.is_file():
            raise ValueError("backup_not_found")
        return path


def _inspect_database(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        schema_row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        counts = {}
        for table in ("questions", "answers", "pending_messages", "audit_log"):
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            counts[table] = (
                int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                if exists
                else 0
            )
        schema_version = int(schema_row[0]) if schema_row else 0
        return {
            "name": path.name,
            "integrity": integrity,
            "schema_version": schema_version,
            "counts": counts,
            "restorable": integrity == "ok" and 1 <= schema_version <= SCHEMA_VERSION,
        }
    except (sqlite3.DatabaseError, TypeError, ValueError):
        return {
            "name": path.name,
            "integrity": "invalid_database",
            "schema_version": 0,
            "counts": {},
            "restorable": False,
        }
    finally:
        connection.close()


def _backup_kind(name: str) -> str:
    for prefix, kind in (
        ("before-restore-", "restore_safety"),
        ("before-media-cleanup-", "media_cleanup"),
        ("before-filter-cleanup-", "filter_cleanup"),
        ("before-library-delete-", "library_delete"),
        ("before-import-", "legacy_import"),
    ):
        if name.startswith(prefix):
            return kind
    return "other"
