from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from new_chat_learning.domain.message import canonical_json
from new_chat_learning.infrastructure.database import SQLiteStore


class ContributionCleanupService:
    def __init__(self, data_dir: Path, store: SQLiteStore) -> None:
        self.plan_dir = Path(data_dir) / "temp" / "contribution-cleanups"
        self.backup_dir = Path(data_dir) / "backups"
        self.store = store
        self._lock = asyncio.Lock()

    async def prepare(
        self, *, group_id: str, user_id: str, actor_id: str
    ) -> dict[str, object]:
        async with self._lock:
            preview = await self.store.member_contribution_preview(
                group_id=str(group_id), user_id=str(user_id)
            )
            if not preview["contributions"] and not preview["pending_messages"]:
                return {"prepared": False, "reason": "no_contributions"}
            plan_id = secrets.token_hex(16)
            created_at = datetime.now(timezone.utc)
            manifest = {
                "plan_id": plan_id,
                "status": "prepared",
                "actor_id": str(actor_id),
                "created_at": created_at.isoformat(),
                "expires_at": (created_at + timedelta(hours=1)).isoformat(),
                **preview,
            }
            self.plan_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                (self.plan_dir / f"{plan_id}.json").write_text,
                canonical_json(manifest),
                "utf-8",
            )
            return {"prepared": True, **manifest}

    async def apply(
        self, *, plan_id: str, group_id: str, user_id: str, actor_id: str
    ) -> dict[str, object]:
        if len(plan_id) != 32 or any(value not in "0123456789abcdef" for value in plan_id):
            return {"applied": False, "reason": "plan_not_found"}
        async with self._lock:
            path = self.plan_dir / f"{plan_id}.json"
            try:
                manifest = json.loads(await asyncio.to_thread(path.read_text, "utf-8"))
            except (OSError, TypeError, ValueError):
                return {"applied": False, "reason": "plan_not_found"}
            error = self._manifest_error(manifest, group_id, user_id, actor_id)
            if error:
                return {"applied": False, "reason": error}
            current = await self.store.member_contribution_preview(
                group_id=str(group_id), user_id=str(user_id)
            )
            if (
                current["operations"] != manifest.get("operations")
                or current["pending_messages"] != manifest.get("pending_messages")
            ):
                return {"applied": False, "reason": "plan_stale"}
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = (
                self.backup_dir
                / f"before-contribution-delete-{timestamp}-{plan_id[:12]}.sqlite3"
            )
            await self.store.backup_to(backup_path)
            try:
                result = await self.store.apply_member_contribution_cleanup(
                    plan_id=plan_id,
                    group_id=str(group_id),
                    user_id=str(user_id),
                    actor_id=str(actor_id),
                    operations=list(manifest["operations"]),
                    pending_messages=int(manifest["pending_messages"]),
                )
            except ValueError as error:
                if str(error) == "contribution_plan_stale":
                    return {
                        "applied": False,
                        "reason": "plan_stale",
                        "backup_path": str(backup_path),
                    }
                raise
            manifest["status"] = "applied"
            manifest["applied_at"] = datetime.now(timezone.utc).isoformat()
            manifest["backup_name"] = backup_path.name
            await asyncio.to_thread(path.write_text, canonical_json(manifest), "utf-8")
            return {**result, "backup_path": str(backup_path)}

    @staticmethod
    def _manifest_error(
        manifest: dict, group_id: str, user_id: str, actor_id: str
    ) -> str:
        if manifest.get("status") != "prepared":
            return "plan_not_ready"
        if str(manifest.get("group_id")) != str(group_id):
            return "wrong_group"
        if str(manifest.get("user_id")) != str(user_id):
            return "wrong_user"
        if str(manifest.get("actor_id")) != str(actor_id):
            return "wrong_actor"
        try:
            expires_at = datetime.fromisoformat(str(manifest["expires_at"]))
        except (KeyError, TypeError, ValueError):
            return "invalid_plan"
        return "plan_expired" if datetime.now(timezone.utc) >= expires_at else ""
