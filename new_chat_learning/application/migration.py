from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from new_chat_learning.infrastructure.database import SQLiteStore
from new_chat_learning.migration.converter import load_manifest, prepare_import


class MigrationService:
    def __init__(self, data_dir: Path, store: SQLiteStore) -> None:
        self.data_dir = Path(data_dir)
        self.store = store
        self.staging_dir = self.data_dir / "temp" / "legacy-imports"
        self.backup_dir = self.data_dir / "backups"

    async def prepare(self, source: Path) -> dict[str, Any]:
        import asyncio

        return await asyncio.to_thread(
            prepare_import,
            Path(source),
            self.staging_dir,
            timeout_seconds=300.0,
        )

    async def apply(
        self,
        *,
        import_id: str,
        group_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        manifest = load_manifest(self.staging_dir, import_id)
        if manifest is None or manifest.get("status") != "prepared":
            return {"imported": False, "reason": "manifest_not_found"}
        staging_name = str(manifest.get("staging_file", ""))
        staging_path = (self.staging_dir / staging_name).resolve()
        if not staging_path.is_relative_to(self.staging_dir.resolve()) or not staging_path.is_file():
            return {"imported": False, "reason": "staging_not_found"}
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.backup_dir / f"before-import-{timestamp}-{import_id[:12]}.sqlite3"
        await self.store.backup_to(backup_path)
        result = await self.store.import_legacy_jsonl(
            import_id=import_id,
            group_id=group_id,
            source_name=str(manifest.get("source_name", "legacy.cl")),
            staging_path=staging_path,
            staging_sha256=str(manifest.get("staging_sha256", "")),
            actor_id=actor_id,
        )
        result["backup_path"] = str(backup_path)
        if result.get("imported"):
            staging_path.unlink(missing_ok=True)
            manifest_path = self.staging_dir / f"{import_id}.json"
            manifest["status"] = "applied"
            manifest["group_id"] = str(group_id)
            manifest["backup_name"] = backup_path.name
            from new_chat_learning.domain.message import canonical_json

            manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
        return result
