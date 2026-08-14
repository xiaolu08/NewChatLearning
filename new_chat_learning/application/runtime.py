from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from new_chat_learning.application.audit import AuditService
from new_chat_learning.application.backup import BackupService
from new_chat_learning.application.content_filter import ContentFilterService
from new_chat_learning.application.contribution_cleanup import ContributionCleanupService
from new_chat_learning.application.diagnostics import RuntimeDiagnostics
from new_chat_learning.application.export import LibraryExportService
from new_chat_learning.application.filter_cleanup import FilterCleanupService
from new_chat_learning.application.learning import LearningResult, LearningService
from new_chat_learning.application.library import LibraryService
from new_chat_learning.application.media import MediaService
from new_chat_learning.application.migration import MigrationService
from new_chat_learning.application.reply import ReplyService
from new_chat_learning.application.tasks import ScheduledTaskService
from new_chat_learning.constants import PLUGIN_VERSION
from new_chat_learning.domain.message import NormalizedMessage, RecallNotice
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore
from new_chat_learning.tts import TTSService
from new_chat_learning.web.auth import WebAuthService

logger = logging.getLogger(__name__)


class RuntimeApplication:
    def __init__(self, data_dir: Path, astrbot_config: dict[str, Any]) -> None:
        self.data_dir = Path(data_dir)
        self.config = ConfigService(astrbot_config)
        self.store = SQLiteStore(self.data_dir / "new_chat_learning.sqlite3")
        self.diagnostics = RuntimeDiagnostics()
        self.learning = LearningService(self.store, self.config.learning_interval_seconds)
        self.library = LibraryService(self.store, self.data_dir)
        self.backup = BackupService(self.data_dir, self.store)
        self.audit = AuditService(self.store)
        self.media = MediaService(self.data_dir, self.store, self.config)
        self.migration = MigrationService(self.data_dir, self.store)
        self.content_filter = ContentFilterService(self.config)
        self.contribution_cleanup = ContributionCleanupService(self.data_dir, self.store)
        self.filter_cleanup = FilterCleanupService(
            self.data_dir,
            self.store,
            self.config,
            self.content_filter,
        )
        self.export = LibraryExportService(self.data_dir, self.store)
        self.tasks = ScheduledTaskService(
            self.data_dir,
            self.store,
            self.media,
            self.filter_cleanup,
            self.export,
            self.migration,
        )
        self.reply = ReplyService(self.store, self.config, self.content_filter)
        self.tts = TTSService(self.data_dir, self.config)
        self.web_auth = WebAuthService(self.data_dir, self.store)
        self.started_at: datetime | None = None

    async def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("media", "backups", "exports", "temp", "logs"):
            (self.data_dir / directory).mkdir(exist_ok=True)
        await self.store.open()
        await self.tasks.start()
        self.started_at = datetime.now(timezone.utc)

    async def stop(self) -> None:
        await self.tasks.stop()
        await self.store.close()
        self.started_at = None

    async def observe(self, message: NormalizedMessage) -> LearningResult:
        settings = self.config.filter_settings(message.group_id)
        scope = str(settings["blacklist_scope"])
        if await self.store.is_blacklisted(
            group_id=message.group_id,
            user_id=message.sender_id,
            scope=scope,
        ):
            return LearningResult(accepted=False)
        sensitive = self.content_filter.sensitive_match(message.group_id, message.components)
        if sensitive.matched:
            state = await self.store.record_sensitive_hit(
                group_id=message.group_id,
                user_id=message.sender_id,
                scope=scope,
                threshold=int(settings["blacklist_threshold"]),
            )
            if state["blocked"]:
                return LearningResult(accepted=False)
        localized = await self.media.localize_message(message)
        return await self.learning.observe(
            localized,
            answer_sender_ids=self.config.learning_target_users_for(message.group_id),
        )

    async def update_group_settings(
        self,
        *,
        group_id: str,
        mode: str,
        target_user_ids: list[str],
        expected_revision: str,
        actor_id: str,
        source: str = "webui",
    ) -> dict[str, Any]:
        before = self.config.group_settings(group_id)
        result = await self.config.update_group_settings(
            group_id=group_id,
            mode=mode,
            target_user_ids=target_user_ids,
            expected_revision=expected_revision,
        )
        try:
            await self.store.record_audit(
                actor_id=actor_id,
                action="update_group_settings",
                target=f"group:{group_id}",
                details={"before": before, "after": result, "source": source},
            )
        except Exception:
            logger.exception("Group settings were saved but audit recording failed.")
        return result

    async def filter_settings(self, group_id: str = "") -> dict[str, Any]:
        result = self.config.filter_settings(group_id)
        result["group_rules"] = self.config.snapshot()["filters"].get("group_rules", [])
        result["statistics"] = await self.store.filter_hit_statistics()
        return result

    async def update_filter_settings(
        self,
        *,
        values: dict[str, Any],
        expected_revision: str,
        actor_id: str,
    ) -> dict[str, Any]:
        before = self.config.snapshot()["filters"]
        await self.config.update_filter_settings(
            values=values,
            expected_revision=expected_revision,
        )
        try:
            await self.store.record_audit(
                actor_id=actor_id,
                action="update_filter_settings",
                target="filters",
                details={
                    "before_rule_counts": self._filter_rule_counts(before),
                    "after_rule_counts": self._filter_rule_counts(
                        self.config.snapshot()["filters"]
                    ),
                    "source": "webui",
                },
            )
        except Exception:
            logger.exception("Filter settings were saved but audit recording failed.")
        return await self.filter_settings()

    async def update_permission_settings(
        self,
        *,
        values: dict[str, Any],
        expected_revision: str,
        actor_id: str,
    ) -> dict[str, Any]:
        before = self.config.permission_settings()
        result = await self.config.update_permission_settings(
            values=values,
            expected_revision=expected_revision,
        )
        try:
            await self.store.record_audit(
                actor_id=actor_id,
                action="update_permission_settings",
                target="permissions",
                details={
                    "before_plugin_admin_count": len(before["plugin_admin_ids"]),
                    "after_plugin_admin_count": len(result["plugin_admin_ids"]),
                    "before_group_count": len(before["group_sub_admins"]),
                    "after_group_count": len(result["group_sub_admins"]),
                    "before_sub_admin_count": self._sub_admin_count(before),
                    "after_sub_admin_count": self._sub_admin_count(result),
                    "source": "webui",
                },
            )
        except Exception:
            logger.exception("Permission settings were saved but audit recording failed.")
        return result

    async def update_tts_settings(
        self,
        *,
        values: dict[str, Any],
        expected_revision: str,
        actor_id: str,
    ) -> dict[str, Any]:
        before = self.config.tts_settings()
        result = await self.config.update_tts_settings(
            values=values,
            expected_revision=expected_revision,
        )
        try:
            await self.store.record_audit(
                actor_id=actor_id,
                action="update_tts_settings",
                target="tts",
                details={
                    "before_enabled": before["enabled"],
                    "after_enabled": result["enabled"],
                    "before_driver": before["driver"],
                    "after_driver": result["driver"],
                    "source": "webui",
                },
            )
        except Exception:
            logger.exception("TTS settings were saved but audit recording failed.")
        return result

    def test_filter_rules(
        self,
        *,
        group_id: str,
        text: str,
        component_type: str = "Plain",
    ) -> dict[str, Any]:
        component = {"type": component_type, "data": {"text": text}}
        components = (component,)
        reply = self.content_filter.reply_match(group_id, components)
        sensitive = self.content_filter.sensitive_match(group_id, components)
        return {
            "reply": {"matched": reply.matched, "rule_type": reply.rule_type},
            "sensitive": {
                "matched": sensitive.matched,
                "rule_type": sensitive.rule_type,
            },
        }

    async def blacklist_entries(self) -> list[dict[str, Any]]:
        return await self.store.blacklist_entries()

    async def update_blacklist(
        self,
        *,
        group_id: str,
        user_id: str,
        scope: str,
        blocked: bool,
        actor_id: str,
    ) -> dict[str, Any]:
        return await self.store.set_blacklist_entry(
            group_id=group_id,
            user_id=user_id,
            scope=scope,
            blocked=blocked,
            actor_id=actor_id,
        )

    @staticmethod
    def _filter_rule_counts(settings: dict[str, Any]) -> dict[str, int]:
        return {
            key: len(settings.get(key, []))
            for key in ("contains", "exact", "regex", "component_types", "sensitive", "group_rules")
        }

    @staticmethod
    def _sub_admin_count(settings: dict[str, Any]) -> int:
        return sum(
            len(entry.get("admin_ids", []))
            for entry in settings.get("group_sub_admins", [])
            if isinstance(entry, dict)
        )

    async def recall(self, notice: RecallNotice) -> LearningResult:
        return await self.learning.recall(notice)

    async def status(self) -> dict[str, Any]:
        database = await self.store.health()
        statistics = await self.store.statistics()
        learning_enabled = bool(self.config.snapshot()["learning"]["enabled"])
        return {
            "name": "NewChatLearning",
            "version": PLUGIN_VERSION,
            "release_stage": "beta",
            "state": "running" if self.started_at else "stopped",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "zero_token_core": True,
            "automatic_learning": learning_enabled,
            "learning_capture_enabled": learning_enabled,
            "automatic_reply": bool(self.config.snapshot()["reply"]["enabled"]),
            "library": self.config.library_status(),
            "data_dir": str(self.data_dir),
            "config_revision": self.config.revision,
            "database": database,
            "statistics": statistics,
        }

    async def diagnostic_snapshot(self) -> dict[str, Any]:
        return await self.diagnostics.snapshot(self.store, self.config)
