from __future__ import annotations

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.web import json_response

from new_chat_learning.application.runtime import RuntimeApplication
from new_chat_learning.commands.permissions import is_plugin_admin
from new_chat_learning.constants import PLUGIN_NAME, PLUGIN_VERSION


class NewChatLearningPlugin(star.Star):
    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = config if config is not None else {}
        self.app: RuntimeApplication | None = None

    async def initialize(self) -> None:
        data_dir = star.StarTools.get_data_dir(PLUGIN_NAME)
        self.app = RuntimeApplication(data_dir=data_dir, astrbot_config=self.config)
        await self.app.start()
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/api/status",
            self.web_status,
            ["GET"],
            "NewChatLearning Beta 运行状态",
        )
        self.logger.info("NewChatLearning %s Beta skeleton initialized.", PLUGIN_VERSION)

    async def terminate(self) -> None:
        if self.app is not None:
            await self.app.stop()
            self.app = None

    @filter.command_group("ncl")
    def ncl(self) -> None:
        """NewChatLearning 管理命令。"""

    @ncl.command("help")
    async def ncl_help(self, event: AstrMessageEvent) -> None:
        if not is_plugin_admin(event, self.config):
            event.stop_event()
            return
        event.set_result(
            MessageEventResult().message(
                "NewChatLearning Beta\n"
                "/ncl status - 查看插件骨架状态\n"
                "/ncl help - 查看当前可用命令\n"
                "学习、回复、迁移、TTS 与完整管理命令仍在开发中。"
            )
        )

    @ncl.command("status")
    async def ncl_status(self, event: AstrMessageEvent) -> None:
        if not is_plugin_admin(event, self.config):
            event.stop_event()
            return
        if self.app is None:
            text = "NewChatLearning Beta 尚未完成初始化。"
        else:
            status = await self.app.status()
            text = (
                "NewChatLearning Beta\n"
                f"状态：{status['state']}\n"
                f"数据库：schema v{status['database']['schema_version']}\n"
                f"问题：{status['statistics']['questions']}\n"
                f"答案：{status['statistics']['answers']}\n"
                "自动学习与自动回复：尚未启用"
            )
        event.set_result(MessageEventResult().message(text))

    async def web_status(self):
        if self.app is None:
            return json_response(
                {"status": "error", "message": "插件尚未初始化"},
                status_code=503,
            )
        return json_response({"status": "ok", "data": await self.app.status()})
