from __future__ import annotations

import asyncio
from pathlib import Path
from sys import maxsize

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.web import json_response

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
                "/ncl migrate-scan <文件或目录> - 安全扫描旧 .cl 词库"
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
            return json_response(
                {"status": "error", "message": "插件尚未初始化"},
                status_code=503,
            )
        return json_response({"status": "ok", "data": await self.app.status()})
