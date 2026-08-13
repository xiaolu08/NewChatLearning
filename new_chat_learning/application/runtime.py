from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from new_chat_learning.application.learning import LearningResult, LearningService
from new_chat_learning.application.library import LibraryService
from new_chat_learning.application.media import MediaService
from new_chat_learning.application.migration import MigrationService
from new_chat_learning.application.reply import ReplyService
from new_chat_learning.constants import PLUGIN_VERSION
from new_chat_learning.domain.message import NormalizedMessage, RecallNotice
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore
from new_chat_learning.web.auth import WebAuthService


class RuntimeApplication:
    def __init__(self, data_dir: Path, astrbot_config: dict[str, Any]) -> None:
        self.data_dir = Path(data_dir)
        self.config = ConfigService(astrbot_config)
        self.store = SQLiteStore(self.data_dir / "new_chat_learning.sqlite3")
        self.learning = LearningService(self.store, self.config.learning_interval_seconds)
        self.library = LibraryService(self.store, self.data_dir)
        self.media = MediaService(self.data_dir, self.store, self.config)
        self.migration = MigrationService(self.data_dir, self.store)
        self.reply = ReplyService(self.store, self.config)
        self.web_auth = WebAuthService(self.data_dir, self.store)
        self.started_at: datetime | None = None

    async def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("media", "backups", "temp", "logs"):
            (self.data_dir / directory).mkdir(exist_ok=True)
        await self.store.open()
        self.started_at = datetime.now(timezone.utc)

    async def stop(self) -> None:
        await self.store.close()

    async def observe(self, message: NormalizedMessage) -> LearningResult:
        localized = await self.media.localize_message(message)
        return await self.learning.observe(localized)

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
