from __future__ import annotations

import asyncio
import hashlib
import secrets
import sqlite3
import time
import urllib.error
from pathlib import Path
from sys import maxsize

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.web import file_response, json_response, request

from new_chat_learning.application.library import component_preview, parse_add_pair
from new_chat_learning.application.runtime import RuntimeApplication
from new_chat_learning.commands.fast_delete import FastDeleteRequest, parse_fast_delete
from new_chat_learning.commands.group_settings import (
    LEARNING_MODES,
    GroupSettingsCommand,
    parse_legacy_group_command,
    parse_on_off,
    transition_toggle,
)
from new_chat_learning.commands.permissions import is_group_admin
from new_chat_learning.constants import PLUGIN_NAME, PLUGIN_VERSION
from new_chat_learning.migration.scanner import scan_directory, scan_file
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
from new_chat_learning.web.auth import COOKIE_NAME, SESSION_TTL_SECONDS

WEB_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
EXPORT_TICKET_TTL_SECONDS = 300


class NewChatLearningPlugin(star.Star):
    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = config if config is not None else {}
        self.app: RuntimeApplication | None = None
        self._export_tickets: dict[str, dict] = {}

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
        for suffix, handler, methods, description in (
            ("auth/state", self.web_auth_state, ["GET"], "NewChatLearning 登录状态"),
            ("auth/setup", self.web_auth_setup, ["POST"], "NewChatLearning 首次设密"),
            ("auth/login", self.web_auth_login, ["POST"], "NewChatLearning 登录"),
            ("auth/logout", self.web_auth_logout, ["POST"], "NewChatLearning 退出"),
            (
                "auth/change-password",
                self.web_auth_change_password,
                ["POST"],
                "NewChatLearning 修改密码",
            ),
            ("media/groups", self.web_media_groups, ["GET"], "NewChatLearning 媒体群列表"),
            ("groups", self.web_groups, ["GET"], "NewChatLearning 群聊列表"),
            ("groups/settings", self.web_group_settings, ["GET"], "NewChatLearning 群聊设置"),
            (
                "groups/settings/update",
                self.web_group_settings_update,
                ["POST"],
                "NewChatLearning 保存群聊设置",
            ),
            ("permissions", self.web_permissions, ["GET"], "NewChatLearning 权限设置"),
            (
                "permissions/update",
                self.web_permissions_update,
                ["POST"],
                "NewChatLearning 保存权限设置",
            ),
            ("tts/settings", self.web_tts_settings, ["GET"], "NewChatLearning 语音设置"),
            (
                "tts/settings/update",
                self.web_tts_settings_update,
                ["POST"],
                "NewChatLearning 保存语音设置",
            ),
            ("tts/test", self.web_tts_test, ["POST"], "NewChatLearning 测试语音合成"),
            ("filters/settings", self.web_filter_settings, ["GET"], "NewChatLearning 过滤设置"),
            (
                "filters/settings/update",
                self.web_filter_settings_update,
                ["POST"],
                "NewChatLearning 保存过滤设置",
            ),
            ("filters/test", self.web_filter_test, ["POST"], "NewChatLearning 测试过滤规则"),
            (
                "filters/cleanup/prepare",
                self.web_filter_cleanup_prepare,
                ["POST"],
                "NewChatLearning 准备过滤词库清理",
            ),
            (
                "filters/cleanup/apply",
                self.web_filter_cleanup_apply,
                ["POST"],
                "NewChatLearning 执行过滤词库清理",
            ),
            (
                "filters/blacklist",
                self.web_filter_blacklist,
                ["GET"],
                "NewChatLearning 黑名单",
            ),
            (
                "filters/blacklist/update",
                self.web_filter_blacklist_update,
                ["POST"],
                "NewChatLearning 更新黑名单",
            ),
            ("library/search", self.web_library_search, ["GET"], "NewChatLearning 词库搜索"),
            ("library/question", self.web_library_question, ["GET"], "NewChatLearning 问题详情"),
            ("library/add", self.web_library_add, ["POST"], "NewChatLearning 添加问答"),
            (
                "library/export/prepare",
                self.web_library_export_prepare,
                ["POST"],
                "NewChatLearning 准备词库导出",
            ),
            ("library/export", self.web_library_export, ["GET"], "NewChatLearning 下载词库导出"),
            ("library/weight", self.web_library_weight, ["POST"], "NewChatLearning 修改权重"),
            (
                "library/delete-answer",
                self.web_library_delete_answer,
                ["POST"],
                "NewChatLearning 删除答案",
            ),
            (
                "library/delete-question",
                self.web_library_delete_question,
                ["POST"],
                "NewChatLearning 删除问题",
            ),
            (
                "library/contributions/prepare",
                self.web_contribution_cleanup_prepare,
                ["POST"],
                "NewChatLearning 准备成员贡献清理",
            ),
            (
                "library/contributions/apply",
                self.web_contribution_cleanup_apply,
                ["POST"],
                "NewChatLearning 执行成员贡献清理",
            ),
            ("media/preview", self.web_media_preview, ["GET"], "NewChatLearning 媒体影响预览"),
            ("media/scan", self.web_media_scan, ["POST"], "NewChatLearning 媒体扫描"),
            (
                "media/cleanup/prepare",
                self.web_media_cleanup_prepare,
                ["POST"],
                "NewChatLearning 媒体清理计划",
            ),
            (
                "media/cleanup/apply",
                self.web_media_cleanup_apply,
                ["POST"],
                "NewChatLearning 媒体清理执行",
            ),
            ("backups", self.web_backups, ["GET"], "NewChatLearning 备份列表"),
            (
                "backups/inspect",
                self.web_backup_inspect,
                ["GET"],
                "NewChatLearning 校验备份",
            ),
            (
                "backups/restore",
                self.web_backup_restore,
                ["POST"],
                "NewChatLearning 恢复备份",
            ),
            ("audit", self.web_audit, ["GET"], "NewChatLearning 审计日志"),
        ):
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/api/{suffix}", handler, methods, description
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
        legacy_command = None
        if self._legacy_command_aliases_enabled():
            legacy_command = parse_legacy_group_command(event.get_message_str())
        if legacy_command is not None:
            await self._handle_group_settings_command(
                event,
                legacy_command,
                source="legacy_command",
            )
            event.stop_event()
            return
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
        text_chain = render_message_chain(
            decision.candidate.components,
            max_plain_length=int(settings["max_plain_length"]),
            data_dir=self.app.data_dir,
        )
        if text_chain is None:
            return
        chain = text_chain
        tts = getattr(self.app, "tts", None)
        if tts is not None:
            audio_path = await tts.synthesize_reply(decision.candidate.components)
            if audio_path is not None:
                voice_chain = render_message_chain(
                    ({"type": "Record", "data": {"path": str(audio_path)}},),
                    max_plain_length=int(settings["max_plain_length"]),
                    data_dir=self.app.data_dir,
                )
                if voice_chain is not None:
                    chain = voice_chain
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
                message_chain=text_chain,
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
        if not is_group_admin(event, self.config):
            event.stop_event()
            return
        event.set_result(
            MessageEventResult().message(
                "NewChatLearning Beta\n"
                "/ncl status - 查看运行状态\n"
                "/ncl mode [模式] - 查看或设置本群运行模式\n"
                "/ncl learning on|off - 开关本群学习\n"
                "/ncl reply on|off - 开关本群词库回复\n"
                "/ncl silent on|off - 开关本群静默学习\n"
                "/ncl target list|add|remove|clear - 管理定向学习用户\n"
                "/ncl search <关键词> - 搜索本群问题\n"
                "/ncl show <问题ID> - 查看问题与答案\n"
                "/ncl add <问题> => <答案> - 添加文本问答\n"
                "/ncl add-regex <表达式> => <答案> - 添加正则问答\n"
                "/ncl weight <答案ID> <权重> - 修改答案权重\n"
                "/ncl delete-answer <答案ID> - 删除答案\n"
                "/ncl delete-question <问题ID> - 删除问题及全部答案\n"
                "/ncl contributions-prepare <用户QQ> - 预览成员贡献删除\n"
                "/ncl contributions-apply <计划ID> <用户QQ> confirm - 备份并删除贡献\n"
                "/ncl migrate-scan <文件或目录> - 安全扫描旧 .cl 词库\n"
                "/ncl migrate-prepare <文件> - 准备旧词库导入\n"
                "/ncl migrate-apply <导入ID> confirm - 备份并导入当前群\n"
                "/ncl media-scan - 扫描并标记本群媒体状态\n"
                "/ncl media-preview - 预览本群失效媒体影响\n"
                "/ncl media-cleanup-prepare [prune|drop-answer] - 准备清理\n"
                "/ncl media-cleanup-apply <计划ID> confirm - 备份并执行清理"
            )
        )

    @ncl.command("status")
    async def ncl_status(self, event: AstrMessageEvent) -> None:
        if not is_group_admin(event, self.config):
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

    @ncl.command("mode")
    async def ncl_mode(self, event: AstrMessageEvent) -> None:
        arguments = tuple(self._command_tail(event, "mode").lower().split())
        await self._handle_group_settings_command(
            event, GroupSettingsCommand("mode", arguments), source="command"
        )

    @ncl.command("learning")
    async def ncl_learning(self, event: AstrMessageEvent) -> None:
        arguments = tuple(self._command_tail(event, "learning").lower().split())
        await self._handle_group_settings_command(
            event, GroupSettingsCommand("learning", arguments), source="command"
        )

    @ncl.command("reply")
    async def ncl_reply(self, event: AstrMessageEvent) -> None:
        arguments = tuple(self._command_tail(event, "reply").lower().split())
        await self._handle_group_settings_command(
            event, GroupSettingsCommand("reply", arguments), source="command"
        )

    @ncl.command("silent")
    async def ncl_silent(self, event: AstrMessageEvent) -> None:
        arguments = tuple(self._command_tail(event, "silent").lower().split())
        await self._handle_group_settings_command(
            event, GroupSettingsCommand("silent", arguments), source="command"
        )

    @ncl.command("target")
    async def ncl_target(self, event: AstrMessageEvent) -> None:
        arguments = tuple(self._command_tail(event, "target").split())
        await self._handle_group_settings_command(
            event, GroupSettingsCommand("target", arguments), source="command"
        )

    @ncl.command("search")
    async def ncl_search(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        query = self._command_tail(event, "search")
        if not query:
            event.set_result(MessageEventResult().message("用法：/ncl search <关键词>"))
            return
        rows = await self.app.library.search(event.get_group_id(), query)
        if not rows:
            event.set_result(MessageEventResult().message("本群词库未找到匹配问题。"))
            return
        lines = [f"本群词库搜索：{query}"]
        for row in rows:
            kind = "正则" if row["is_regex"] else "文本"
            question_preview = str(row["plain_text"])
            if len(question_preview) > 80:
                question_preview = f"{question_preview[:79]}…"
            lines.append(
                f"Q{row['question_id']} [{kind}] {question_preview} "
                f"| 答案 {row['answer_count']} | 权重 {row['total_weight']}"
            )
        event.set_result(MessageEventResult().message("\n".join(lines)))

    @ncl.command("show")
    async def ncl_show(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        question_id = self._positive_int(self._command_tail(event, "show"))
        if question_id is None:
            event.set_result(MessageEventResult().message("用法：/ncl show <问题ID>"))
            return
        detail = await self.app.library.show(event.get_group_id(), question_id)
        if detail is None:
            event.set_result(MessageEventResult().message("本群词库中不存在该问题。"))
            return
        kind = "正则" if detail["is_regex"] else "文本"
        lines = [
            f"问题 Q{detail['question_id']} [{kind}]",
            component_preview(detail["components_json"], 160),
            f"记录频次：{detail['frequency']} | 答案数：{len(detail['answers'])}",
        ]
        for answer in detail["answers"][:20]:
            lines.append(
                f"A{answer['answer_id']} 权重 {answer['weight']}："
                f"{component_preview(answer['components_json'])}"
            )
        if len(detail["answers"]) > 20:
            lines.append(f"其余 {len(detail['answers']) - 20} 个答案请在 WebUI 查看。")
        event.set_result(MessageEventResult().message("\n".join(lines)))

    @ncl.command("add")
    async def ncl_add(self, event: AstrMessageEvent) -> None:
        await self._add_library_pair(event, command="add", is_regex=False)

    @ncl.command("add-regex")
    async def ncl_add_regex(self, event: AstrMessageEvent) -> None:
        await self._add_library_pair(event, command="add-regex", is_regex=True)

    @ncl.command("weight")
    async def ncl_weight(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        parts = self._command_tail(event, "weight").split()
        answer_id = self._positive_int(parts[0]) if len(parts) == 2 else None
        weight = self._positive_int(parts[1]) if len(parts) == 2 else None
        if answer_id is None or weight is None:
            event.set_result(MessageEventResult().message("用法：/ncl weight <答案ID> <正整数权重>"))
            return
        changed = await self.app.library.set_weight(
            group_id=event.get_group_id(),
            actor_id=event.get_sender_id(),
            answer_id=answer_id,
            weight=weight,
        )
        text = f"答案 A{answer_id} 的权重已设为 {weight}。" if changed else "本群不存在该答案。"
        event.set_result(MessageEventResult().message(text))

    @ncl.command("delete-answer")
    async def ncl_delete_answer(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        answer_id = self._positive_int(self._command_tail(event, "delete-answer"))
        if answer_id is None:
            event.set_result(MessageEventResult().message("用法：/ncl delete-answer <答案ID>"))
            return
        result = await self.app.library.delete_answer(
            group_id=event.get_group_id(),
            actor_id=event.get_sender_id(),
            answer_id=answer_id,
        )
        if not result["deleted"]:
            text = "本群不存在该答案。"
        elif result["orphan_question_removed"]:
            text = f"已删除答案 A{answer_id}；问题因无剩余答案一并删除。"
        else:
            text = f"已删除答案 A{answer_id}。"
        event.set_result(MessageEventResult().message(text))

    @ncl.command("delete-question")
    async def ncl_delete_question(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        question_id = self._positive_int(self._command_tail(event, "delete-question"))
        if question_id is None:
            event.set_result(MessageEventResult().message("用法：/ncl delete-question <问题ID>"))
            return
        deleted = await self.app.library.delete_question(
            group_id=event.get_group_id(),
            actor_id=event.get_sender_id(),
            question_id=question_id,
        )
        text = f"已删除问题 Q{question_id} 及其全部答案。" if deleted else "本群不存在该问题。"
        event.set_result(MessageEventResult().message(text))

    @ncl.command("contributions-prepare")
    async def ncl_contributions_prepare(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        user_id = self._qq_id(self._command_tail(event, "contributions-prepare"))
        if user_id is None:
            event.set_result(
                MessageEventResult().message("用法：/ncl contributions-prepare <用户QQ>")
            )
            return
        result = await self.app.contribution_cleanup.prepare(
            group_id=event.get_group_id(),
            user_id=user_id,
            actor_id=event.get_sender_id(),
        )
        if not result.get("prepared"):
            event.set_result(MessageEventResult().message("本群没有该成员可追踪的学习贡献。"))
            return
        event.set_result(
            MessageEventResult().message(
                f"成员 {user_id} 的贡献删除计划已准备，词库尚未改变。\n"
                f"贡献记录：{result['contributions']}，受影响答案：{result['affected_answers']}，"
                f"将删除答案：{result['answers_becoming_empty']}，"
                f"将删除空问题：{result['questions_becoming_empty']}，"
                f"待固化消息：{result['pending_messages']}\n"
                "计划一小时后过期。确认执行：\n"
                f"/ncl contributions-apply {result['plan_id']} {user_id} confirm"
            )
        )

    @ncl.command("contributions-apply")
    async def ncl_contributions_apply(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        parts = self._command_tail(event, "contributions-apply").split()
        user_id = self._qq_id(parts[1]) if len(parts) == 3 else None
        if len(parts) != 3 or user_id is None or parts[2].lower() != "confirm":
            event.set_result(
                MessageEventResult().message(
                    "用法：/ncl contributions-apply <计划ID> <用户QQ> confirm\n"
                    "该操作会先备份数据库，再删除预览中的成员贡献。"
                )
            )
            return
        result = await self.app.contribution_cleanup.apply(
            plan_id=parts[0].lower(),
            group_id=event.get_group_id(),
            user_id=user_id,
            actor_id=event.get_sender_id(),
        )
        if not result.get("applied"):
            reasons = {
                "plan_not_found": "找不到贡献删除计划。",
                "plan_not_ready": "贡献删除计划已执行或不可用。",
                "wrong_group": "计划不属于当前群。",
                "wrong_user": "计划不属于该成员。",
                "wrong_actor": "计划只能由创建它的管理员确认。",
                "plan_expired": "计划已过期，请重新准备。",
                "plan_stale": "成员贡献或词库已变化，请重新准备。",
                "invalid_plan": "计划格式无效。",
            }
            event.set_result(
                MessageEventResult().message(reasons.get(result.get("reason"), "删除未执行。"))
            )
            return
        event.set_result(
            MessageEventResult().message(
                f"成员 {user_id} 的可追踪学习贡献已删除。\n"
                f"移除贡献：{result['removed_contributions']}，降低权重答案："
                f"{result['reduced_answers']}，删除答案：{result['deleted_answers']}，"
                f"删除空问题：{result['orphan_questions']}，"
                f"移除待固化消息：{result['removed_pending_messages']}\n"
                f"执行前备份：{Path(result['backup_path']).name}"
            )
        )

    @ncl.command("migrate-scan")
    async def ncl_migrate_scan(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        raw_path = self._command_tail(event, "migrate-scan")
        if not raw_path:
            event.set_result(MessageEventResult().message("用法：/ncl migrate-scan <.cl 文件或目录>"))
            return
        path = Path(raw_path.strip().strip('"'))
        if path.is_dir():
            reports = await asyncio.to_thread(scan_directory, path, timeout_seconds=60.0)
        elif path.is_file():
            reports = [await asyncio.to_thread(scan_file, path, timeout_seconds=60.0)]
        else:
            event.set_result(MessageEventResult().message("找不到指定文件或目录。"))
            return
        if not reports:
            event.set_result(MessageEventResult().message("目录中没有 .cl 文件。"))
            return
        lines = ["旧词库安全扫描报告（仅扫描，不导入）"]
        for report in reports[:10]:
            name = Path(str(report.get("path", "未知文件"))).name
            structure = report.get("structure", {})
            if isinstance(structure, dict):
                lines.append(
                    f"{name}：{report.get('status', 'error')}，问题 {structure.get('question_count', 0)}，"
                    f"答案 {structure.get('answer_count', 0)}，异常问题 {structure.get('malformed_questions', 0)}"
                )
            else:
                lines.append(f"{name}：{report.get('status', 'error')}（{report.get('reason', '未知原因')}）")
        if len(reports) > 10:
            lines.append(f"其余 {len(reports) - 10} 个文件请在本地日志或 WebUI 查看。")
        event.set_result(MessageEventResult().message("\n".join(lines)))

    @ncl.command("migrate-prepare")
    async def ncl_migrate_prepare(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        raw_path = self._command_tail(event, "migrate-prepare")
        path = Path(raw_path.strip().strip('"')) if raw_path else None
        if path is None or not path.is_file() or path.suffix.lower() != ".cl":
            event.set_result(MessageEventResult().message("用法：/ncl migrate-prepare <.cl 文件>"))
            return
        report = await self.app.migration.prepare(path)
        if report.get("status") != "prepared":
            event.set_result(
                MessageEventResult().message(
                    f"旧词库准备失败：{report.get('reason', '未知原因')}"
                )
            )
            return
        event.set_result(
            MessageEventResult().message(
                "旧词库已完成隔离转换，尚未导入。\n"
                f"导入 ID：{report['import_id']}\n"
                f"可导入问题：{report['question_count']}，可处理答案记录：{report['answer_count']}\n"
                f"跳过问题：{report['skipped_questions']}，"
                f"跳过答案：{report['skipped_answers']}，"
                f"未知组件：{report['unknown_components']}\n"
                f"确认导入当前群：/ncl migrate-apply {report['import_id']} confirm"
            )
        )

    @ncl.command("media-scan")
    async def ncl_media_scan(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        result = await self.app.media.scan_group(event.get_group_id())
        preview = result["preview"]
        event.set_result(
            MessageEventResult().message(
                "本群媒体健康扫描完成（只标记，不删除）。\n"
                f"扫描答案：{result['scanned_answers']}，媒体组件：{result['scanned_components']}\n"
                f"失效组件：{preview['media_components']}，受影响答案：{preview['affected_answers']}，"
                f"清理后可能为空：{preview['answers_becoming_empty']}"
            )
        )

    @ncl.command("media-preview")
    async def ncl_media_preview(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        preview = await self.app.media.health_preview(event.get_group_id())
        states = preview.get("states", {})
        state_text = "，".join(f"{key} {value}" for key, value in states.items()) or "暂无扫描记录"
        event.set_result(
            MessageEventResult().message(
                "本群失效媒体影响预览（不会删除内容）\n"
                f"状态：{state_text}\n"
                f"失效组件：{preview['media_components']}，受影响答案：{preview['affected_answers']}，"
                f"受影响问题：{preview['affected_questions']}，"
                f"清理后可能为空的答案：{preview['answers_becoming_empty']}"
            )
        )

    @ncl.command("media-cleanup-prepare")
    async def ncl_media_cleanup_prepare(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        mode = self._command_tail(event, "media-cleanup-prepare").strip() or "prune"
        if mode not in {"prune", "drop-answer"}:
            event.set_result(
                MessageEventResult().message(
                    "用法：/ncl media-cleanup-prepare [prune|drop-answer]"
                )
            )
            return
        result = await self.app.media.prepare_cleanup(
            group_id=event.get_group_id(),
            actor_id=event.get_sender_id(),
            mode=mode,
        )
        if not result.get("prepared"):
            event.set_result(MessageEventResult().message("本群当前没有已标记的失效媒体。"))
            return
        mode_name = "移除失效组件" if mode == "prune" else "删除含失效媒体的整条答案"
        event.set_result(
            MessageEventResult().message(
                "本群媒体清理计划已准备，尚未修改词库。\n"
                f"模式：{mode_name}\n"
                f"失效组件：{result['invalid_components']}，"
                f"更新答案：{result['update_answers']}，删除答案：{result['delete_answers']}\n"
                "计划一小时后过期。确认执行：\n"
                f"/ncl media-cleanup-apply {result['plan_id']} confirm"
            )
        )

    @ncl.command("media-cleanup-apply")
    async def ncl_media_cleanup_apply(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        parts = self._command_tail(event, "media-cleanup-apply").split()
        if len(parts) != 2 or parts[1].lower() != "confirm":
            event.set_result(
                MessageEventResult().message(
                    "用法：/ncl media-cleanup-apply <计划ID> confirm\n"
                    "该操作会先备份数据库，再执行已预览的本群媒体清理。"
                )
            )
            return
        result = await self.app.media.apply_cleanup(
            plan_id=parts[0].lower(),
            group_id=event.get_group_id(),
            actor_id=event.get_sender_id(),
        )
        if not result.get("applied"):
            reasons = {
                "plan_not_found": "找不到清理计划。",
                "plan_not_ready": "清理计划已执行或不可用。",
                "wrong_group": "清理计划不属于当前群。",
                "wrong_actor": "清理计划只能由创建它的管理员确认。",
                "plan_expired": "清理计划已过期，请重新准备。",
                "plan_stale": "词库或扫描状态已变化，请重新扫描并准备。",
                "invalid_plan": "清理计划格式无效。",
            }
            event.set_result(
                MessageEventResult().message(reasons.get(result.get("reason"), "清理未执行。"))
            )
            return
        event.set_result(
            MessageEventResult().message(
                "本群失效媒体清理完成。\n"
                f"移除组件：{result['removed_components']}，更新答案：{result['updated_answers']}，"
                f"删除答案：{result['deleted_answers']}，合并重复答案：{result['merged_answers']}，"
                f"删除空问题：{result['orphan_questions']}\n"
                f"执行前备份：{Path(result['backup_path']).name}"
            )
        )

    @ncl.command("migrate-apply")
    async def ncl_migrate_apply(self, event: AstrMessageEvent) -> None:
        if not self._allow_group_library_command(event):
            return
        parts = self._command_tail(event, "migrate-apply").split()
        if len(parts) != 2 or parts[1].lower() != "confirm":
            event.set_result(
                MessageEventResult().message(
                    "用法：/ncl migrate-apply <导入ID> confirm\n"
                    "该操作会先备份数据库，再把准备内容导入当前群。"
                )
            )
            return
        result = await self.app.migration.apply(
            import_id=parts[0],
            group_id=event.get_group_id(),
            actor_id=event.get_sender_id(),
        )
        if not result.get("imported"):
            event.set_result(
                MessageEventResult().message(
                    f"导入未执行：{result.get('reason', '未知原因')}"
                )
            )
            return
        event.set_result(
            MessageEventResult().message(
                f"旧词库已导入当前群：合并问题记录 {result['question_count']}，"
                f"合并答案记录 {result['answer_count']}。\n"
                f"导入前备份：{Path(result['backup_path']).name}\n"
                "本次导入不会自动开启学习或词库回复。"
            )
        )

    async def _add_library_pair(
        self,
        event: AstrMessageEvent,
        *,
        command: str,
        is_regex: bool,
    ) -> None:
        if not self._allow_group_library_command(event):
            return
        pair = parse_add_pair(self._command_tail(event, command))
        if pair is None:
            event.set_result(
                MessageEventResult().message(f"用法：/ncl {command} <问题> => <答案>")
            )
            return
        try:
            result = await self.app.library.add_text_pair(
                group_id=event.get_group_id(),
                actor_id=event.get_sender_id(),
                question=pair.question,
                answer=pair.answer,
                is_regex=is_regex,
            )
        except ValueError:
            event.set_result(MessageEventResult().message("问题或答案无效、过长，未写入词库。"))
            return
        action = "已添加" if result["created"] else "已增加重复答案权重"
        event.set_result(
            MessageEventResult().message(
                f"{action}：Q{result['question_id']} / A{result['answer_id']}，"
                f"当前权重 {result['weight']}。"
            )
        )

    def _allow_group_library_command(self, event: AstrMessageEvent) -> bool:
        if (
            self.app is not None
            and event.get_group_id()
            and is_group_admin(event, getattr(self, "config", {}))
        ):
            return True
        event.stop_event()
        return False

    def _legacy_command_aliases_enabled(self) -> bool:
        snapshot = getattr(self.app.config, "snapshot", None) if self.app is not None else None
        if callable(snapshot):
            general = snapshot().get("general", {})
        else:
            general = getattr(self, "config", {}).get("general", {})
        return bool(general.get("legacy_command_aliases", True))

    async def _handle_group_settings_command(
        self,
        event: AstrMessageEvent,
        command: GroupSettingsCommand,
        *,
        source: str,
    ) -> None:
        if not self._allow_group_library_command(event):
            return
        group_id = event.get_group_id()
        settings = self.app.config.group_settings(group_id)
        if command.name == "mode" and not command.arguments:
            event.set_result(MessageEventResult().message(self._format_group_settings(settings)))
            return
        mode = settings["mode"]
        targets = list(settings["target_user_ids"])
        usage = self._group_command_usage(command.name)
        if command.name == "mode":
            if len(command.arguments) != 1 or command.arguments[0] not in {
                "disabled", "learning", "reply", "learning_reply", "silent"
            }:
                event.set_result(MessageEventResult().message(usage))
                return
            mode = command.arguments[0]
        elif command.name in {"learning", "reply", "silent"}:
            enabled = parse_on_off(command.arguments)
            if source == "legacy_command" and not command.arguments:
                capability_enabled = {
                    "learning": mode in LEARNING_MODES,
                    "reply": mode in {"reply", "learning_reply"},
                    "silent": mode == "silent",
                }
                enabled = not capability_enabled[command.name]
            if enabled is None:
                event.set_result(MessageEventResult().message(usage))
                return
            mode = transition_toggle(mode, command.name, enabled)
        elif command.name == "target":
            if not command.arguments or command.arguments[0].lower() == "list":
                event.set_result(MessageEventResult().message(self._format_group_settings(settings)))
                return
            action = command.arguments[0].lower()
            if action == "clear" and len(command.arguments) == 1:
                targets = []
            elif action in {"add", "remove"} and len(command.arguments) >= 2:
                requested = [self._qq_id(value) for value in command.arguments[1:]]
                if any(value is None for value in requested):
                    event.set_result(MessageEventResult().message(usage))
                    return
                if action == "add" and mode not in LEARNING_MODES:
                    event.set_result(
                        MessageEventResult().message("请先使用 /ncl learning on 开启本群学习。")
                    )
                    return
                requested_ids = [value for value in requested if value is not None]
                if action == "add":
                    targets = list(dict.fromkeys([*targets, *requested_ids]))
                else:
                    requested_set = set(requested_ids)
                    targets = [value for value in targets if value not in requested_set]
            else:
                event.set_result(MessageEventResult().message(usage))
                return
        else:
            event.set_result(MessageEventResult().message(usage))
            return
        try:
            result = await self.app.update_group_settings(
                group_id=group_id,
                mode=mode,
                target_user_ids=targets,
                expected_revision=settings["revision"],
                actor_id=event.get_sender_id(),
                source=source,
            )
        except ValueError as exc:
            text = (
                "配置已被其他入口修改，请重试。"
                if str(exc) == "revision_conflict"
                else "群聊设置无效，未保存。"
            )
            event.set_result(MessageEventResult().message(text))
            return
        except (OSError, RuntimeError):
            self.logger.exception("Failed to persist group settings from command.")
            event.set_result(MessageEventResult().message("群聊设置保存失败，原配置已保留。"))
            return
        event.set_result(MessageEventResult().message(self._format_group_settings(result, saved=True)))

    @staticmethod
    def _format_group_settings(settings: dict, *, saved: bool = False) -> str:
        labels = {
            "disabled": "停用",
            "learning": "仅学习",
            "reply": "仅回复",
            "learning_reply": "学习并回复",
            "silent": "静默学习",
        }
        targets = settings.get("target_user_ids", [])
        target_text = "、".join(targets) if targets else "全部成员"
        prefix = "本群设置已保存。\n" if saved else ""
        return f"{prefix}运行模式：{labels.get(settings.get('mode'), '未知')}\n定向学习：{target_text}"

    @staticmethod
    def _group_command_usage(name: str) -> str:
        usages = {
            "mode": "用法：/ncl mode [disabled|learning|reply|learning_reply|silent]",
            "learning": "用法：/ncl learning on|off",
            "reply": "用法：/ncl reply on|off",
            "silent": "用法：/ncl silent on|off",
            "target": "用法：/ncl target list|add <QQ...>|remove <QQ...>|clear",
        }
        return usages.get(name, "群聊设置命令无效。")

    @staticmethod
    def _command_tail(event: AstrMessageEvent, command: str) -> str:
        text = event.get_message_str().strip().lstrip("/")
        prefix = f"ncl {command}"
        return text[len(prefix) :].strip() if text.lower().startswith(prefix.lower()) else ""

    @staticmethod
    def _positive_int(value: str) -> int | None:
        try:
            parsed = int(value.strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    async def web_status(self):
        if self.app is None:
            return self._web_json(
                {"status": "error", "message": "插件尚未初始化"},
                status_code=503,
            )
        if not await self.app.web_auth.authorize(self._web_session_token()):
            return self._web_json(
                {"status": "error", "message": "需要登录"}, status_code=401
            )
        return self._web_json({"status": "ok", "data": await self.app.status()})

    async def web_auth_state(self):
        if self.app is None:
            return self._web_json({"status": "error", "message": "插件尚未初始化"}, status_code=503)
        state = await self.app.web_auth.state(self._web_session_token())
        return self._web_json({"status": "ok", "data": state})

    async def web_auth_setup(self):
        if self.app is None:
            return self._web_json({"status": "error", "message": "插件尚未初始化"}, status_code=503)
        payload = await self._web_payload()
        result, session = await self.app.web_auth.setup(
            str(payload.get("password", "")), str(request.client_host or "")
        )
        if result != "ok" or session is None:
            messages = {
                "already_configured": "管理密码已经设置。",
                "loopback_required": "首次设置密码只能从本机访问。",
                "password_too_short": "密码至少需要 12 个字符。",
                "password_too_long": "密码不能超过 256 个字符。",
            }
            return self._web_json(
                {"status": "error", "message": messages.get(result, "无法设置密码。")},
                status_code=400,
            )
        return self._web_session_response(session)

    async def web_auth_login(self):
        if self.app is None:
            return self._web_json({"status": "error", "message": "插件尚未初始化"}, status_code=503)
        payload = await self._web_payload()
        result, session = await self.app.web_auth.login(
            str(payload.get("password", "")), str(request.client_host or "")
        )
        if result != "ok" or session is None:
            status_code = 503 if result == "credential_error" else 429 if result == "locked" else 401
            return self._web_json(
                {"status": "error", "message": "登录失败，请稍后重试。"},
                status_code=status_code,
            )
        return self._web_session_response(session)

    async def web_auth_logout(self):
        if self.app is None:
            return self._web_json({"status": "error", "message": "插件尚未初始化"}, status_code=503)
        payload = await self._web_payload()
        valid = await self.app.web_auth.logout(
            self._web_session_token(), str(payload.get("csrf_token", ""))
        )
        if not valid:
            return self._web_json({"status": "error", "message": "请求未授权。"}, status_code=403)
        response = self._web_json({"status": "ok", "data": {"logged_out": True}})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    async def web_auth_change_password(self):
        if self.app is None:
            return self._web_json({"status": "error", "message": "插件尚未初始化"}, status_code=503)
        payload = await self._web_payload()
        result, session = await self.app.web_auth.change_password(
            session_token=self._web_session_token(),
            csrf_token=str(payload.get("csrf_token", "")),
            current_password=str(payload.get("current_password", "")),
            new_password=str(payload.get("new_password", "")),
        )
        if result != "ok" or session is None:
            messages = {
                "password_too_short": "新密码至少需要 12 个字符。",
                "password_too_long": "新密码不能超过 256 个字符。",
                "invalid_credentials": "当前密码不正确。",
                "csrf_invalid": "安全令牌已失效，请刷新页面。",
                "credential_error": "密码文件不可用，请检查本机数据目录。",
            }
            return self._web_json(
                {"status": "error", "message": messages.get(result, "请求未授权。")},
                status_code=403,
            )
        return self._web_session_response(session)

    async def web_media_groups(self):
        if self.app is None:
            return self._web_json({"status": "error", "message": "插件尚未初始化"}, status_code=503)
        if not await self.app.web_auth.authorize(self._web_session_token()):
            return self._web_json({"status": "error", "message": "需要登录"}, status_code=401)
        group_ids = await self.app.store.list_question_group_ids()
        return self._web_json({"status": "ok", "data": {"group_ids": group_ids}})

    async def web_groups(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        stored = await self.app.store.list_question_group_ids()
        group_ids = sorted(set(stored) | set(self.app.config.configured_group_ids()))
        return self._web_json(
            {
                "status": "ok",
                "data": {
                    "group_ids": group_ids,
                    "revision": self.app.config.revision,
                },
            }
        )

    async def web_group_settings(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        group_id = self._web_group_id(request.query.get("group_id", ""))
        if group_id is None:
            return self._web_json({"status": "error", "message": "群号无效。"}, status_code=400)
        return self._web_json(
            {"status": "ok", "data": self.app.config.group_settings(group_id)}
        )

    async def web_group_settings_update(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        group_id = self._web_group_id(payload.get("group_id", ""))
        mode = str(payload.get("mode", "")).strip()
        revision = str(payload.get("revision", "")).strip()
        raw_targets = payload.get("target_user_ids", [])
        if group_id is None or not isinstance(raw_targets, list):
            return self._web_json({"status": "error", "message": "群聊设置无效。"}, status_code=400)
        targets = []
        for value in raw_targets:
            user_id = str(value).strip()
            if not user_id.isdigit() or not 5 <= len(user_id) <= 20:
                return self._web_json(
                    {"status": "error", "message": "目标用户 QQ 号无效。"}, status_code=400
                )
            if user_id not in targets:
                targets.append(user_id)
        if len(targets) > 100:
            return self._web_json(
                {"status": "error", "message": "单群最多配置 100 个定向用户。"},
                status_code=400,
            )
        try:
            result = await self.app.update_group_settings(
                group_id=group_id,
                mode=mode,
                target_user_ids=targets,
                expected_revision=revision,
                actor_id=self._web_actor_id(),
            )
        except ValueError as exc:
            if str(exc) == "revision_conflict":
                return self._web_json(
                    {
                        "status": "error",
                        "message": "配置已被其他入口修改，请刷新后重试。",
                        "data": {"revision": self.app.config.revision},
                    },
                    status_code=409,
                )
            return self._web_json({"status": "error", "message": "群聊模式无效。"}, status_code=400)
        except (OSError, RuntimeError):
            self.logger.exception("Failed to persist NewChatLearning group settings.")
            return self._web_json(
                {"status": "error", "message": "AstrBot 插件配置保存失败，设置未生效。"},
                status_code=503,
            )
        return self._web_json({"status": "ok", "data": result})

    async def web_filter_settings(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        group_id = str(request.query.get("group_id", "")).strip()
        if group_id and self._web_group_id(group_id) is None:
            return self._web_json(
                {"status": "error", "message": "群号无效。"}, status_code=400
            )
        return self._web_json(
            {"status": "ok", "data": await self.app.filter_settings(group_id)}
        )

    async def web_permissions(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        return self._web_json(
            {"status": "ok", "data": self.app.config.permission_settings()}
        )

    async def web_permissions_update(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        if payload.get("confirmed") is not True:
            return self._web_json(
                {"status": "error", "message": "请先确认权限变更。"}, status_code=400
            )
        revision = str(payload.get("revision", "")).strip()
        values = {
            "plugin_admin_ids": payload.get("plugin_admin_ids"),
            "group_sub_admins": payload.get("group_sub_admins"),
        }
        try:
            result = await self.app.update_permission_settings(
                values=values,
                expected_revision=revision,
                actor_id=self._web_actor_id(),
            )
        except (TypeError, ValueError) as exc:
            if str(exc) == "revision_conflict":
                return self._web_json(
                    {
                        "status": "error",
                        "message": "配置已被其他入口修改，请刷新后重试。",
                        "data": {"revision": self.app.config.revision},
                    },
                    status_code=409,
                )
            return self._web_json(
                {"status": "error", "message": "权限设置无效，请检查 QQ 号、群号和数量限制。"},
                status_code=400,
            )
        except (OSError, RuntimeError):
            self.logger.exception("Failed to persist NewChatLearning permission settings.")
            return self._web_json(
                {"status": "error", "message": "AstrBot 插件配置保存失败，设置未生效。"},
                status_code=503,
            )
        return self._web_json({"status": "ok", "data": result})

    async def web_filter_settings_update(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        revision = str(payload.pop("revision", "")).strip()
        payload.pop("csrf_token", None)
        try:
            result = await self.app.update_filter_settings(
                values=payload,
                expected_revision=revision,
                actor_id=self._web_actor_id(),
            )
        except ValueError as exc:
            if str(exc) == "revision_conflict":
                return self._web_json(
                    {
                        "status": "error",
                        "message": "配置已被其他入口修改，请刷新后重试。",
                        "data": {"revision": self.app.config.revision},
                    },
                    status_code=409,
                )
            message = "正则表达式无法编译。" if str(exc) == "invalid_regex" else "过滤设置无效。"
            return self._web_json({"status": "error", "message": message}, status_code=400)
        except TypeError:
            return self._web_json(
                {"status": "error", "message": "过滤设置无效。"}, status_code=400
            )
        except (OSError, RuntimeError):
            self.logger.exception("Failed to persist NewChatLearning filter settings.")
            return self._web_json(
                {"status": "error", "message": "AstrBot 插件配置保存失败，设置未生效。"},
                status_code=503,
            )
        return self._web_json({"status": "ok", "data": result})

    async def web_tts_settings(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        settings = self.app.config.tts_settings()
        reference_path = Path(str(settings.pop("reference_audio_path", "")))
        return self._web_json(
            {
                "status": "ok",
                "data": {
                    **settings,
                    "reference_audio_configured": bool(reference_path.name),
                    "reference_audio_name": reference_path.name,
                    "runtime": self.app.tts.status(),
                },
            }
        )

    async def web_tts_settings_update(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        revision = str(payload.get("revision", "")).strip()
        current = self.app.config.tts_settings()
        reference_audio_path = current["reference_audio_path"]
        if payload.get("clear_reference_audio") is True:
            reference_audio_path = ""
        elif str(payload.get("reference_audio_path", "")).strip():
            reference_audio_path = str(payload["reference_audio_path"]).strip()
        values = {
            key: payload.get(key, current[key])
            for key in (
                "enabled",
                "driver",
                "probability_percent",
                "max_text_length",
                "voice",
                "rate",
                "volume",
                "endpoint_url",
                "timeout_seconds",
                "text_lang",
                "prompt_text",
                "prompt_lang",
            )
        }
        values["reference_audio_path"] = reference_audio_path
        try:
            result = await self.app.update_tts_settings(
                values=values,
                expected_revision=revision,
                actor_id=self._web_actor_id(),
            )
        except (TypeError, ValueError) as exc:
            if str(exc) == "revision_conflict":
                return self._web_json(
                    {
                        "status": "error",
                        "message": "配置已被其他入口修改，请刷新后重试。",
                        "data": {"revision": self.app.config.revision},
                    },
                    status_code=409,
                )
            messages = {
                "tts_driver_unavailable": "当前 Beta 只允许 Windows、本地 HTTP 和 GPT-SoVITS 驱动。",
                "tts_endpoint_must_be_loopback": "本地 HTTP 地址必须使用带端口的环回地址。",
                "invalid_tts_probability": "启用语音回复时，触发概率必须大于 0。",
                "gpt_sovits_reference_required": "启用 GPT-SoVITS 前必须配置参考音频。",
            }
            return self._web_json(
                {"status": "error", "message": messages.get(str(exc), "语音设置无效。")},
                status_code=400,
            )
        except (OSError, RuntimeError):
            self.logger.exception("Failed to persist NewChatLearning TTS settings.")
            return self._web_json(
                {"status": "error", "message": "AstrBot 插件配置保存失败，设置未生效。"},
                status_code=503,
            )
        public_result = dict(result)
        reference_path = Path(str(public_result.pop("reference_audio_path", "")))
        public_result["reference_audio_configured"] = bool(reference_path.name)
        public_result["reference_audio_name"] = reference_path.name
        public_result["runtime"] = self.app.tts.status()
        return self._web_json({"status": "ok", "data": public_result})

    async def web_tts_test(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        text = str(payload.get("text", "")).strip()
        try:
            output_path = await self.app.tts.synthesize(text)
        except ValueError as exc:
            messages = {
                "invalid_tts_text": "测试文本为空或超过当前长度限制。",
                "tts_endpoint_must_be_loopback": "本地 HTTP 地址必须使用环回地址。",
                "gpt_sovits_reference_required": "GPT-SoVITS 尚未配置参考音频。",
                "invalid_tts_audio": "TTS 服务返回的内容不是受支持的音频。",
            }
            return self._web_json(
                {"status": "error", "message": messages.get(str(exc), "语音测试失败。")},
                status_code=400,
            )
        except (OSError, RuntimeError, TimeoutError, urllib.error.URLError):
            return self._web_json(
                {"status": "error", "message": "语音驱动不可用、超时或合成失败。"},
                status_code=503,
            )
        return self._web_json(
            {
                "status": "ok",
                "data": {
                    "generated": True,
                    "file_name": output_path.name,
                    "size_bytes": output_path.stat().st_size,
                    "driver": self.app.config.tts_settings()["driver"],
                },
            }
        )

    async def web_filter_test(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        group_id = self._web_group_id(payload.get("group_id", ""))
        text = str(payload.get("text", ""))
        component_type = str(payload.get("component_type", "Plain")).strip()
        if group_id is None or not text or len(text) > 4000 or not component_type:
            return self._web_json(
                {"status": "error", "message": "测试参数无效。"}, status_code=400
            )
        result = self.app.test_filter_rules(
            group_id=group_id,
            text=text,
            component_type=component_type,
        )
        return self._web_json({"status": "ok", "data": result})

    async def web_filter_blacklist(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        return self._web_json(
            {"status": "ok", "data": {"entries": await self.app.blacklist_entries()}}
        )

    async def web_filter_blacklist_update(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        scope = str(payload.get("scope", "")).strip()
        raw_group_id = str(payload.get("group_id", "")).strip()
        group_id = self._web_group_id(raw_group_id) if scope == "group" else ""
        user_id = str(payload.get("user_id", "")).strip()
        blocked = payload.get("blocked")
        if (
            scope not in {"global", "group"}
            or (scope == "group" and group_id is None)
            or not user_id.isdigit()
            or not 5 <= len(user_id) <= 20
            or not isinstance(blocked, bool)
        ):
            return self._web_json(
                {"status": "error", "message": "黑名单参数无效。"}, status_code=400
            )
        result = await self.app.update_blacklist(
            group_id=group_id or "",
            user_id=user_id,
            scope=scope,
            blocked=blocked,
            actor_id=self._web_actor_id(),
        )
        return self._web_json({"status": "ok", "data": result})

    async def web_filter_cleanup_prepare(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        group_id = self._web_group_id(payload.get("group_id", ""))
        if group_id is None:
            return self._web_json(
                {"status": "error", "message": "群号无效。"}, status_code=400
            )
        result = await self.app.filter_cleanup.prepare_cleanup(
            group_id=group_id,
            actor_id=self._web_actor_id(),
        )
        if not result.get("prepared"):
            return self._web_json(
                {"status": "error", "message": "当前群没有命中过滤规则的答案。"},
                status_code=409,
            )
        public_result = {
            key: result[key]
            for key in (
                "prepared",
                "plan_id",
                "created_at",
                "expires_at",
                "affected_answers",
                "affected_questions",
                "questions_becoming_empty",
                "rule_type_counts",
            )
        }
        return self._web_json({"status": "ok", "data": public_result})

    async def web_filter_cleanup_apply(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        if payload.get("confirmed") is not True:
            return self._web_json(
                {"status": "error", "message": "请先确认执行清理。"}, status_code=400
            )
        group_id = self._web_group_id(payload.get("group_id", ""))
        plan_id = str(payload.get("plan_id", "")).lower()
        if group_id is None:
            return self._web_json(
                {"status": "error", "message": "群号无效。"}, status_code=400
            )
        result = await self.app.filter_cleanup.apply_cleanup(
            plan_id=plan_id,
            group_id=group_id,
            actor_id=self._web_actor_id(),
        )
        if not result.get("applied"):
            reasons = {
                "plan_not_found": "找不到清理计划。",
                "plan_not_ready": "清理计划已执行或不可用。",
                "wrong_group": "清理计划不属于当前群。",
                "wrong_actor": "清理计划不属于当前 WebUI 操作者。",
                "plan_expired": "清理计划已过期，请重新准备。",
                "plan_stale": "词库或过滤规则已变化，请重新生成清理计划。",
                "invalid_plan": "清理计划格式无效。",
            }
            return self._web_json(
                {"status": "error", "message": reasons.get(result.get("reason"), "清理未执行。")},
                status_code=409,
            )
        public_result = dict(result)
        public_result["backup_name"] = Path(str(result["backup_path"])).name
        public_result.pop("backup_path", None)
        return self._web_json({"status": "ok", "data": public_result})

    async def web_library_search(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        group_id = self._web_group_id(request.query.get("group_id", ""))
        query = str(request.query.get("query", "")).strip()
        if group_id is None:
            return self._web_json({"status": "error", "message": "群号无效。"}, status_code=400)
        if len(query) > 200:
            return self._web_json({"status": "error", "message": "搜索关键词不能超过 200 个字符。"}, status_code=400)
        rows = await self.app.library.search(group_id, query, limit=50)
        return self._web_json({"status": "ok", "data": {"questions": rows}})

    async def web_library_question(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        group_id = self._web_group_id(request.query.get("group_id", ""))
        question_id = self._web_positive_int(request.query.get("question_id", ""))
        if group_id is None or question_id is None:
            return self._web_json({"status": "error", "message": "问题参数无效。"}, status_code=400)
        detail = await self.app.library.show(group_id, question_id)
        if detail is None:
            return self._web_json({"status": "error", "message": "本群不存在该问题。"}, status_code=404)
        public_detail = dict(detail)
        public_detail["preview"] = component_preview(detail["components_json"], 400)
        public_detail.pop("components_json", None)
        public_detail["answers"] = [
            {
                **answer,
                "preview": component_preview(answer["components_json"], 400),
            }
            for answer in detail["answers"]
        ]
        for answer in public_detail["answers"]:
            answer.pop("components_json", None)
        return self._web_json({"status": "ok", "data": public_detail})

    async def web_library_export_prepare(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        if payload.get("confirmed") is not True:
            return self._web_json(
                {"status": "error", "message": "请先确认导出当前群词库。"},
                status_code=400,
            )
        group_id = self._web_group_id(payload.get("group_id", ""))
        if group_id is None:
            return self._web_json(
                {"status": "error", "message": "群号无效。"}, status_code=400
            )
        try:
            result = await self.app.export.export_group(
                group_id=group_id,
                actor_id=self._web_actor_id(),
                source="webui",
            )
        except (OSError, RuntimeError):
            self.logger.exception("Failed to export group library from WebUI.")
            return self._web_json(
                {"status": "error", "message": "词库导出失败，未生成文件。"},
                status_code=500,
            )
        self._prune_export_tickets()
        ticket = secrets.token_urlsafe(24)
        self._export_tickets[ticket] = {
            "session": self._web_actor_id(),
            "path": result["path"],
            "filename": result["filename"],
            "expires_at": time.monotonic() + EXPORT_TICKET_TTL_SECONDS,
        }
        return self._web_json(
            {
                "status": "ok",
                "data": {
                    "ticket": ticket,
                    "filename": result["filename"],
                    "question_count": result["question_count"],
                    "answer_count": result["answer_count"],
                    "expires_in_seconds": EXPORT_TICKET_TTL_SECONDS,
                },
            }
        )

    async def web_library_export(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        self._prune_export_tickets()
        ticket = str(request.query.get("ticket", ""))
        export = self._export_tickets.get(ticket)
        if (
            export is None
            or export.get("session") != self._web_actor_id()
            or not Path(export["path"]).is_file()
        ):
            return self._web_json(
                {"status": "error", "message": "导出下载票据无效或已过期。"},
                status_code=404,
            )
        self._export_tickets.pop(ticket, None)
        return file_response(
            export["path"],
            filename=export["filename"],
            content_type="application/zip",
            headers=WEB_HEADERS,
        )

    def _prune_export_tickets(self) -> None:
        now = time.monotonic()
        tickets = getattr(self, "_export_tickets", None)
        if tickets is None:
            self._export_tickets = {}
            return
        for ticket, export in list(tickets.items()):
            if float(export.get("expires_at", 0)) <= now:
                tickets.pop(ticket, None)

    async def web_library_add(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        group_id = self._web_group_id(payload.get("group_id", ""))
        question = str(payload.get("question", "")).strip()
        answer = str(payload.get("answer", "")).strip()
        is_regex = payload.get("is_regex", False)
        if group_id is None or not question or not answer or not isinstance(is_regex, bool):
            return self._web_json({"status": "error", "message": "问答参数无效。"}, status_code=400)
        try:
            result = await self.app.library.add_text_pair(
                group_id=group_id,
                actor_id=self._web_actor_id(),
                question=question,
                answer=answer,
                is_regex=is_regex,
            )
        except ValueError as exc:
            messages = {
                "text_too_long": "问题和答案均不能超过 4000 个字符。",
                "regex_too_long": "正则表达式不能超过 1000 个字符。",
                "invalid_regex": "正则表达式无法编译。",
            }
            return self._web_json(
                {"status": "error", "message": messages.get(str(exc), "问答内容无效。")},
                status_code=400,
            )
        return self._web_json({"status": "ok", "data": result})

    async def web_library_weight(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        group_id = self._web_group_id(payload.get("group_id", ""))
        answer_id = self._web_positive_int(payload.get("answer_id", ""))
        weight = self._web_positive_int(payload.get("weight", ""), maximum=1_000_000_000)
        if group_id is None or answer_id is None or weight is None:
            return self._web_json({"status": "error", "message": "答案 ID 或权重无效。"}, status_code=400)
        changed = await self.app.library.set_weight(
            group_id=group_id,
            actor_id=self._web_actor_id(),
            answer_id=answer_id,
            weight=weight,
        )
        if not changed:
            return self._web_json({"status": "error", "message": "本群不存在该答案。"}, status_code=404)
        return self._web_json({"status": "ok", "data": {"changed": True, "weight": weight}})

    async def web_library_delete_answer(self):
        return await self._web_library_delete("answer")

    async def web_library_delete_question(self):
        return await self._web_library_delete("question")

    async def web_contribution_cleanup_prepare(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        group_id = self._web_group_id(payload.get("group_id", ""))
        user_id = self._qq_id(payload.get("user_id", ""))
        if group_id is None or user_id is None:
            return self._web_json(
                {"status": "error", "message": "群号或用户 QQ 号无效。"},
                status_code=400,
            )
        result = await self.app.contribution_cleanup.prepare(
            group_id=group_id,
            user_id=user_id,
            actor_id=self._web_actor_id(),
        )
        if not result.get("prepared"):
            return self._web_json(
                {"status": "error", "message": "本群没有该成员可追踪的学习贡献。"},
                status_code=409,
            )
        public_result = dict(result)
        public_result.pop("operations", None)
        return self._web_json({"status": "ok", "data": public_result})

    async def web_contribution_cleanup_apply(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        if payload.get("confirmed") is not True:
            return self._web_json(
                {"status": "error", "message": "请先确认删除成员贡献。"}, status_code=400
            )
        group_id = self._web_group_id(payload.get("group_id", ""))
        user_id = self._qq_id(payload.get("user_id", ""))
        plan_id = str(payload.get("plan_id", "")).lower()
        if group_id is None or user_id is None:
            return self._web_json(
                {"status": "error", "message": "群号或用户 QQ 号无效。"},
                status_code=400,
            )
        result = await self.app.contribution_cleanup.apply(
            plan_id=plan_id,
            group_id=group_id,
            user_id=user_id,
            actor_id=self._web_actor_id(),
        )
        if not result.get("applied"):
            reasons = {
                "plan_not_found": "找不到贡献删除计划。",
                "plan_not_ready": "贡献删除计划已执行或不可用。",
                "wrong_group": "计划不属于当前群。",
                "wrong_user": "计划不属于该成员。",
                "wrong_actor": "计划不属于当前 WebUI 操作者。",
                "plan_expired": "计划已过期，请重新准备。",
                "plan_stale": "成员贡献或词库已变化，请重新准备。",
                "invalid_plan": "计划格式无效。",
            }
            return self._web_json(
                {"status": "error", "message": reasons.get(result.get("reason"), "删除未执行。")},
                status_code=409,
            )
        public_result = dict(result)
        public_result["backup_name"] = Path(str(result["backup_path"])).name
        public_result.pop("backup_path", None)
        return self._web_json({"status": "ok", "data": public_result})

    async def _web_library_delete(self, target: str):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        if payload.get("confirmed") is not True:
            return self._web_json(
                {"status": "error", "message": "请先确认删除词库记录。"}, status_code=400
            )
        group_id = self._web_group_id(payload.get("group_id", ""))
        target_id = self._web_positive_int(payload.get(f"{target}_id", ""))
        if group_id is None or target_id is None:
            return self._web_json({"status": "error", "message": "删除参数无效。"}, status_code=400)
        if target == "answer":
            detail = await self.app.store.answer_detail(group_id, target_id)
            if detail is None:
                return self._web_json({"status": "error", "message": "本群不存在该答案。"}, status_code=404)
            result = await self.app.library.delete_answer_with_backup(
                group_id=group_id, actor_id=self._web_actor_id(), answer_id=target_id
            )
        else:
            detail = await self.app.library.show(group_id, target_id)
            if detail is None:
                return self._web_json({"status": "error", "message": "本群不存在该问题。"}, status_code=404)
            result = await self.app.library.delete_question_with_backup(
                group_id=group_id, actor_id=self._web_actor_id(), question_id=target_id
            )
        public_result = dict(result)
        public_result["backup_name"] = Path(result["backup_path"]).name
        public_result.pop("backup_path", None)
        return self._web_json({"status": "ok", "data": public_result})

    async def web_media_preview(self):
        if self.app is None:
            return self._web_json({"status": "error", "message": "插件尚未初始化"}, status_code=503)
        if not await self.app.web_auth.authorize(self._web_session_token()):
            return self._web_json({"status": "error", "message": "需要登录"}, status_code=401)
        group_id = self._web_group_id(request.query.get("group_id", ""))
        if group_id is None:
            return self._web_json({"status": "error", "message": "群号无效。"}, status_code=400)
        preview = await self.app.media.health_preview(group_id)
        return self._web_json({"status": "ok", "data": preview})

    async def web_media_scan(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        group_id = self._web_group_id(payload.get("group_id", ""))
        if group_id is None:
            return self._web_json({"status": "error", "message": "群号无效。"}, status_code=400)
        result = await self.app.media.scan_group(group_id)
        return self._web_json({"status": "ok", "data": result})

    async def web_media_cleanup_prepare(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        group_id = self._web_group_id(payload.get("group_id", ""))
        mode = str(payload.get("mode", "prune"))
        if group_id is None or mode not in {"prune", "drop-answer"}:
            return self._web_json({"status": "error", "message": "清理参数无效。"}, status_code=400)
        result = await self.app.media.prepare_cleanup(
            group_id=group_id,
            actor_id=self._web_actor_id(),
            mode=mode,
        )
        if not result.get("prepared"):
            return self._web_json(
                {"status": "error", "message": "当前群没有已标记的失效媒体。"},
                status_code=409,
            )
        return self._web_json({"status": "ok", "data": result})

    async def web_media_cleanup_apply(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        if payload.get("confirmed") is not True:
            return self._web_json(
                {"status": "error", "message": "请先确认执行清理。"}, status_code=400
            )
        group_id = self._web_group_id(payload.get("group_id", ""))
        plan_id = str(payload.get("plan_id", "")).lower()
        if group_id is None:
            return self._web_json({"status": "error", "message": "群号无效。"}, status_code=400)
        result = await self.app.media.apply_cleanup(
            plan_id=plan_id,
            group_id=group_id,
            actor_id=self._web_actor_id(),
        )
        if not result.get("applied"):
            reasons = {
                "plan_not_found": "找不到清理计划。",
                "plan_not_ready": "清理计划已执行或不可用。",
                "wrong_group": "清理计划不属于当前群。",
                "wrong_actor": "清理计划不属于当前 WebUI 操作者。",
                "plan_expired": "清理计划已过期，请重新准备。",
                "plan_stale": "词库或扫描状态已变化，请重新扫描并准备。",
                "invalid_plan": "清理计划格式无效。",
            }
            return self._web_json(
                {"status": "error", "message": reasons.get(result.get("reason"), "清理未执行。")},
                status_code=409,
            )
        public_result = dict(result)
        public_result["backup_name"] = Path(str(result["backup_path"])).name
        public_result.pop("backup_path", None)
        return self._web_json({"status": "ok", "data": public_result})

    async def web_backups(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        return self._web_json(
            {"status": "ok", "data": {"backups": await self.app.backup.list_backups()}}
        )

    async def web_audit(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        action = str(request.query.get("action", "")).strip()
        raw_before_id = str(request.query.get("before_id", "")).strip()
        before_id = self._web_positive_int(raw_before_id) if raw_before_id else None
        if raw_before_id and before_id is None:
            return self._web_json(
                {"status": "error", "message": "审计分页游标无效。"},
                status_code=400,
            )
        raw_limit = str(request.query.get("limit", "50")).strip()
        try:
            limit = int(raw_limit)
        except ValueError:
            limit = 0
        if not 1 <= limit <= 100:
            return self._web_json(
                {"status": "error", "message": "分页大小必须在 1 到 100 之间。"},
                status_code=400,
            )
        try:
            result = await self.app.audit.list_entries(
                action=action,
                before_id=before_id,
                limit=limit,
            )
        except ValueError:
            return self._web_json(
                {"status": "error", "message": "审计动作筛选无效。"},
                status_code=400,
            )
        return self._web_json({"status": "ok", "data": result})

    async def web_backup_inspect(self):
        error = await self._authorized_web_read()
        if error is not None:
            return error
        name = str(request.query.get("name", "")).strip()
        try:
            result = await self.app.backup.inspect(name)
        except ValueError:
            return self._web_json(
                {"status": "error", "message": "备份文件不存在或名称无效。"},
                status_code=404,
            )
        return self._web_json({"status": "ok", "data": result})

    async def web_backup_restore(self):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        if payload.get("confirmed") is not True:
            return self._web_json(
                {"status": "error", "message": "请先确认恢复数据库备份。"}, status_code=400
            )
        try:
            result = await self.app.backup.restore(
                name=str(payload.get("name", "")),
                actor_id=self._web_actor_id(),
            )
        except ValueError as exc:
            message = (
                "备份完整性或 schema 不符合恢复要求。"
                if str(exc) == "backup_not_restorable"
                else "备份文件不存在或名称无效。"
            )
            return self._web_json({"status": "error", "message": message}, status_code=409)
        except (OSError, RuntimeError, sqlite3.DatabaseError):
            self.logger.exception("Failed to restore NewChatLearning database backup.")
            return self._web_json(
                {"status": "error", "message": "备份恢复失败，运行数据库已自动回滚。"},
                status_code=500,
            )
        await self.app.web_auth.invalidate_all_sessions()
        response = self._web_json({"status": "ok", "data": result})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    async def _authorized_web_payload(self):
        if self.app is None:
            return {}, self._web_json(
                {"status": "error", "message": "插件尚未初始化"}, status_code=503
            )
        payload = await self._web_payload()
        if not await self.app.web_auth.authorize(
            self._web_session_token(), str(payload.get("csrf_token", ""))
        ):
            return {}, self._web_json(
                {"status": "error", "message": "请求未授权。"}, status_code=403
            )
        return payload, None

    async def _authorized_web_read(self):
        if self.app is None:
            return self._web_json(
                {"status": "error", "message": "插件尚未初始化"}, status_code=503
            )
        if not await self.app.web_auth.authorize(self._web_session_token()):
            return self._web_json({"status": "error", "message": "需要登录"}, status_code=401)
        return None

    async def _web_payload(self) -> dict:
        body = await request.body()
        if len(body) > 32768:
            return {}
        try:
            payload = __import__("json").loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _web_session_token(self) -> str:
        return str(request.cookies.get(COOKIE_NAME, "") or "")

    def _web_actor_id(self) -> str:
        digest = hashlib.sha256(self._web_session_token().encode("utf-8")).hexdigest()[:16]
        return f"webui:{digest}"

    def _web_session_response(self, session):
        response = self._web_json(
            {"status": "ok", "data": {"csrf_token": session.csrf_token}}
        )
        response.set_cookie(
            COOKIE_NAME,
            session.token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    @staticmethod
    def _web_group_id(value) -> str | None:
        group_id = str(value).strip()
        return group_id if group_id.isdigit() and 5 <= len(group_id) <= 20 else None

    @staticmethod
    def _qq_id(value) -> str | None:
        user_id = str(value).strip()
        return user_id if user_id.isdigit() and 5 <= len(user_id) <= 20 else None

    @staticmethod
    def _web_positive_int(value, *, maximum: int | None = None) -> int | None:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        if parsed < 1 or (maximum is not None and parsed > maximum):
            return None
        return parsed

    @staticmethod
    def _web_json(data, *, status_code: int = 200):
        return json_response(data, status_code=status_code, headers=WEB_HEADERS)
