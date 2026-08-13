from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from sys import maxsize

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.web import json_response, request

from new_chat_learning.application.library import component_preview, parse_add_pair
from new_chat_learning.application.runtime import RuntimeApplication
from new_chat_learning.commands.fast_delete import FastDeleteRequest, parse_fast_delete
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
            ("library/search", self.web_library_search, ["GET"], "NewChatLearning 词库搜索"),
            ("library/question", self.web_library_question, ["GET"], "NewChatLearning 问题详情"),
            ("library/add", self.web_library_add, ["POST"], "NewChatLearning 添加问答"),
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
        if not is_group_admin(event, self.config):
            event.stop_event()
            return
        event.set_result(
            MessageEventResult().message(
                "NewChatLearning Beta\n"
                "/ncl status - 查看运行状态\n"
                "/ncl search <关键词> - 搜索本群问题\n"
                "/ncl show <问题ID> - 查看问题与答案\n"
                "/ncl add <问题> => <答案> - 添加文本问答\n"
                "/ncl add-regex <表达式> => <答案> - 添加正则问答\n"
                "/ncl weight <答案ID> <权重> - 修改答案权重\n"
                "/ncl delete-answer <答案ID> - 删除答案\n"
                "/ncl delete-question <问题ID> - 删除问题及全部答案\n"
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
        if self.app is not None and event.get_group_id() and is_group_admin(event, self.config):
            return True
        event.stop_event()
        return False

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

    async def _web_library_delete(self, target: str):
        payload, error = await self._authorized_web_payload()
        if error is not None:
            return error
        reauthentication = await self.app.web_auth.reauthenticate(
            session_token=self._web_session_token(),
            csrf_token=str(payload.get("csrf_token", "")),
            password=str(payload.get("password", "")),
        )
        if reauthentication != "ok":
            return self._web_json({"status": "error", "message": "密码确认失败，删除未执行。"}, status_code=403)
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
        reauthentication = await self.app.web_auth.reauthenticate(
            session_token=self._web_session_token(),
            csrf_token=str(payload.get("csrf_token", "")),
            password=str(payload.get("password", "")),
        )
        if reauthentication != "ok":
            return self._web_json(
                {"status": "error", "message": "密码确认失败，清理未执行。"},
                status_code=403,
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
