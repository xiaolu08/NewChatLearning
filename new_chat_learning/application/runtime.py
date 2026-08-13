from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from new_chat_learning.constants import PLUGIN_VERSION
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore


class RuntimeApplication:
    def __init__(self, data_dir: Path, astrbot_config: dict[str, Any]) -> None:
        self.data_dir = Path(data_dir)
        self.config = ConfigService(astrbot_config)
        self.store = SQLiteStore(self.data_dir / "new_chat_learning.sqlite3")
        self.started_at: datetime | None = None

    async def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("media", "backups", "temp", "logs"):
            (self.data_dir / directory).mkdir(exist_ok=True)
        await self.store.open()
        self.started_at = datetime.now(timezone.utc)

    async def stop(self) -> None:
        await self.store.close()

    async def status(self) -> dict[str, Any]:
        database = await self.store.health()
        statistics = await self.store.statistics()
        return {
            "name": "NewChatLearning",
            "version": PLUGIN_VERSION,
            "release_stage": "beta",
            "state": "running" if self.started_at else "stopped",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "zero_token_core": True,
            "automatic_learning": False,
            "automatic_reply": False,
            "data_dir": str(self.data_dir),
            "config_revision": self.config.revision,
            "database": database,
            "statistics": statistics,
        }
