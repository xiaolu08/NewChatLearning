from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from new_chat_learning.infrastructure.database import SQLiteStore
from new_chat_learning.migration.converter import load_manifest, prepare_import


class MigrationService:
    PLAN_TTL_SECONDS = 3600
    RETAIN_APPLIED_SECONDS = 30 * 24 * 3600

    def __init__(self, data_dir: Path, store: SQLiteStore) -> None:
        self.data_dir = Path(data_dir)
        self.store = store
        self.staging_dir = self.data_dir / "temp" / "legacy-imports"
        self.backup_dir = self.data_dir / "backups"

    async def prepare(
        self,
        source: Path,
        *,
        actor_id: str | None = None,
        group_id: str | None = None,
        source_name: str | None = None,
    ) -> dict[str, Any]:
        import asyncio

        report = await asyncio.to_thread(
            prepare_import,
            Path(source),
            self.staging_dir,
            timeout_seconds=300.0,
        )
        if report.get("status") == "prepared" and actor_id and group_id:
            if source_name:
                report["source_name"] = Path(source_name).name[:255]
            report["actor_id"] = str(actor_id)
            report["group_id"] = str(group_id)
            report["created_at"] = int(time.time())
            report["expires_at"] = int(time.time()) + self.PLAN_TTL_SECONDS
            self._write_manifest(report)
        return report

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
        if manifest.get("actor_id") and manifest.get("actor_id") != str(actor_id):
            return {"imported": False, "reason": "wrong_actor"}
        if manifest.get("group_id") and manifest.get("group_id") != str(group_id):
            return {"imported": False, "reason": "wrong_group"}
        if int(manifest.get("expires_at", 0) or 0) and int(manifest["expires_at"]) <= int(
            time.time()
        ):
            self._remove_staging(manifest)
            (self.staging_dir / f"{import_id}.json").unlink(missing_ok=True)
            return {"imported": False, "reason": "plan_expired"}
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
            manifest["status"] = "applied"
            manifest["group_id"] = str(group_id)
            manifest["backup_name"] = backup_path.name
            manifest["applied_at"] = int(time.time())
            self._write_manifest(manifest)
        return result

    def list_web_imports(self, *, actor_id: str) -> list[dict[str, Any]]:
        self.cleanup_expired()
        if not self.staging_dir.is_dir():
            return []
        results = []
        for path in self.staging_dir.glob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict) or manifest.get("actor_id") != str(actor_id):
                continue
            results.append(self.public_manifest(manifest))
        return sorted(
            results,
            key=lambda item: int(item.get("created_at", item.get("applied_at", 0)) or 0),
            reverse=True,
        )[:50]

    def cleanup_expired(self) -> int:
        if not self.staging_dir.is_dir():
            return 0
        now = int(time.time())
        removed = 0
        for path in self.staging_dir.glob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict):
                continue
            status = manifest.get("status")
            expires_at = int(manifest.get("expires_at", 0) or 0)
            expired_plan = status == "prepared" and expires_at > 0 and expires_at <= now
            old_applied = status == "applied" and int(manifest.get("applied_at", 0) or 0) <= (
                now - self.RETAIN_APPLIED_SECONDS
            )
            if expired_plan:
                self._remove_staging(manifest)
            if expired_plan or old_applied:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _remove_staging(self, manifest: dict[str, Any]) -> None:
        staging_name = str(manifest.get("staging_file", ""))
        staging_path = (self.staging_dir / staging_name).resolve()
        if staging_path.is_relative_to(self.staging_dir.resolve()):
            staging_path.unlink(missing_ok=True)

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        from new_chat_learning.domain.message import canonical_json

        import_id = str(manifest.get("import_id", ""))
        if not import_id or any(character not in "0123456789abcdef" for character in import_id):
            raise ValueError("invalid_import_id")
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        (self.staging_dir / f"{import_id}.json").write_text(
            canonical_json(manifest), encoding="utf-8"
        )

    @staticmethod
    def public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "import_id",
            "source_name",
            "source_size_bytes",
            "status",
            "group_id",
            "created_at",
            "expires_at",
            "applied_at",
            "backup_name",
            "question_count",
            "answer_count",
            "skipped_questions",
            "skipped_answers",
            "unknown_components",
            "skip_reasons",
        )
        return {key: manifest[key] for key in fields if key in manifest}
