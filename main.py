from __future__ import annotations

import asyncio
from sys import maxsize

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.web import json_response

from new_chat_learning.application.runtime import RuntimeApplication
from new_chat_learning.commands.fast_delete import FastDeleteRequest, parse_fast_delete
from new_chat_learning.commands.permissions import is_group_admin, is_plugin_admin
from new_chat_learning.constants import PLUGIN_NAME, PLUGIN_VERSION
from new_chat_learning.platform.astrbot.renderer import render_message_chain
from new_chat_learning.platform.napcat.actions import (
    recall_message,
    send_group_message_with_id,
)
from new_chat_learning.platform.napcat.normalizer import (
    normalize_group_message,
    parse_recall_notice,
    reply_matching_key,
)


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

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE,
        priority=maxsize - 100,
    )
    async def capture_group_message(self, event: AstrMessageEvent) -> None:
        if self.app is None:
            return
        recall = parse_recall_notice(event)
        if recall is not None:
            await self.app.recall(recall)
            return
        group_id = event.get_group_id()
        fast_delete = parse_fast_delete(event)
        if fast_delete is not None:
            await self._handle_fast_delete(event, fast_delete)
            event.stop_event()
            return
        learning_enabled = self.app.config.learning_enabled_for(group_id)
        reply_enabled = self.app.config.reply_enabled_for(group_id)
        if not learning_enabled and not reply_enabled:
            return
        message = normalize_group_message(event)
        if message is None:
            return
        if learning_enabled:
            await self.app.observe(message)
        if not reply_enabled:
            return

        mentioned_bot = any(
            str(component.get("type", "")).lower() == "at"
            and str(component.get("data", {}).get("qq", "")) == str(event.get_self_id())
            for component in message.components
        )
        decision = await self.app.reply.decide(
            group_id,
            reply_matching_key(message, event.get_self_id()),
            plain_text=message.plain_text,
            mentioned_bot=mentioned_bot,
        )
        if not decision.should_reply or decision.candidate is None:
            return
        settings = self.app.config.reply_settings()
        chain = render_message_chain(
            decision.candidate.components,
            max_plain_length=int(settings["max_plain_length"]),
            data_dir=self.app.data_dir,
        )
        if chain is None:
            return
        if decision.wait_seconds > 0:
            await asyncio.sleep(decision.wait_seconds)
        sent_message_id = await send_group_message_with_id(event, chain)
        self.app.reply.mark_sent(group_id)
        if sent_message_id is not None:
            await self.app.store.register_reply(
                platform=event.get_platform_name(),
                group_id=group_id,
                sent_message_id=sent_message_id,
                answer_id=decision.candidate.answer_id,
                question_id=decision.candidate.question_id,
            )
        try:
            await self.context.message_history_manager.insert_message_chain(
                platform_id=event.get_platform_id(),
                user_id=event.unified_msg_origin,
                message_chain=chain,
                role="bot",
                sender_id=event.get_self_id() or "bot",
                sender_name="NewChatLearning",
            )
        except Exception:
            self.logger.exception("Failed to persist NewChatLearning local reply.")
        event.stop_event()

    async def _handle_fast_delete(
        self,
        event: AstrMessageEvent,
        request: FastDeleteRequest,
    ) -> None:
        if self.app is None or not is_group_admin(event, self.config):
            return
        platform = event.get_platform_name()
        group_id = event.get_group_id()
        target_id = request.quoted_message_id
        if target_id is None and request.recent_position is not None:
            target_id = await self.app.store.recent_reply_message_id(
                platform=platform,
                group_id=group_id,
                position=request.recent_position,
            )
        if target_id is None:
            event.set_result(event.plain_result("未找到可删除的 NewChatLearning 回复。"))
            return
        result = await self.app.store.fast_delete_reply(
            platform=platform,
            group_id=group_id,
            sent_message_id=target_id,
            actor_id=event.get_sender_id(),
        )
        if not result["deleted"]:
            event.set_result(event.plain_result("未找到可删除的 NewChatLearning 回复。"))
            return
        command_id = str(getattr(event.message_obj, "message_id", "") or "")
        if command_id:
            try:
                await recall_message(event, command_id)
            except Exception:
                self.logger.exception("Failed to recall fast-delete command.")
        event.set_result(event.plain_result("已删除对应答案。"))

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
                f"待固化消息：{status['statistics']['pending_messages']}\n"
                f"群聊学习：{'已启用' if status['automatic_learning'] else '未启用'}\n"
                f"本地词库回复：{'已启用' if status['automatic_reply'] else '未启用'}\n"
                f"词库范围：{'全局/标签' if status['library']['mode'] == 'global' else '仅本群'}"
            )
        event.set_result(MessageEventResult().message(text))

    async def web_status(self):
        if self.app is None:
            return json_response(
                {"status": "error", "message": "插件尚未初始化"},
                status_code=503,
            )
        return json_response({"status": "ok", "data": await self.app.status()})
