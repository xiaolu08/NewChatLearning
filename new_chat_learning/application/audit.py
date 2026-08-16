from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

from new_chat_learning.infrastructure.database import SQLiteStore

ACTION_NAMES = {
    "add_custom_pair": "添加自定义问答",
    "blacklist_block": "人工封禁",
    "blacklist_unblock": "解除封禁",
    "cleanup_filtered_answers": "过滤词库清理",
    "cleanup_invalid_media": "失效媒体清理",
    "delete_answer": "删除答案",
    "delete_answers_by_text_globally": "跨群删除同文答案",
    "delete_member_contributions": "删除成员贡献",
    "delete_question": "删除问题",
    "export_library": "导出词库",
    "export_legacy_library": "导出旧版词库",
    "fast_delete_answer": "快速删除答案",
    "import_legacy_library": "导入旧词库",
    "import_external_library": "导入外部词库",
    "update_external_library": "更新外部词库",
    "set_external_library_enabled": "启停外部词库",
    "update_external_library_bindings": "更新外部词库绑定",
    "delete_external_library": "删除外部词库",
    "restore_database_backup": "恢复数据库备份",
    "run_scheduled_task": "执行定时任务",
    "set_answer_weight": "修改答案权重",
    "update_filter_settings": "更新过滤设置",
    "update_group_settings": "更新群聊设置",
    "update_cross_group_settings": "更新跨群设置",
    "update_share_welcome_message": "更新联动组欢迎语",
    "update_share_reply_cooldown": "更新联动组回复冷却",
    "update_global_switch": "更新全局开关",
    "update_permission_settings": "更新权限设置",
    "update_tts_settings": "更新语音设置",
    "update_tts_secrets": "更新语音密钥",
    "clear_tts_secrets": "清除语音密钥",
    "update_scheduled_task": "更新定时任务",
    "delete_scheduled_task": "删除定时任务",
    "webui_login": "WebUI 登录",
    "webui_logout": "WebUI 退出",
    "webui_password_change": "修改 WebUI 密码",
    "webui_password_setup": "设置 WebUI 密码",
}

SAFE_DETAIL_KEYS = {
    "affected_questions",
    "after_driver",
    "after_enabled",
    "after_group_count",
    "after_plugin_admin_count",
    "after_sub_admin_count",
    "answer_count",
    "backup_name",
    "before_group_count",
    "before_driver",
    "before_enabled",
    "before_plugin_admin_count",
    "before_sub_admin_count",
    "blocked",
    "config_revision",
    "created",
    "cleanup_mode",
    "cooldown_minutes",
    "category",
    "capability",
    "deleted_answers",
    "degraded_components",
    "destructive",
    "enabled",
    "group_id",
    "group_count",
    "hit_count",
    "import_id",
    "interval_minutes",
    "is_regex",
    "version",
    "manual",
    "merged_answers",
    "new_weight",
    "old_weight",
    "operation",
    "orphan_question_removed",
    "orphan_questions",
    "plan_id",
    "question_count",
    "question_id",
    "reduced_answers",
    "removed_components",
    "removed_contributions",
    "removed_pending_messages",
    "result",
    "rule_type_counts",
    "safety_backup_name",
    "scope",
    "source",
    "source_name",
    "task_type",
    "trigger_type",
    "updated_answers",
    "weight",
}


class AuditService:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    async def list_entries(
        self, *, action: str = "", before_id: int | None = None, limit: int = 50
    ) -> dict[str, Any]:
        action = str(action).strip()
        if action and not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", action):
            raise ValueError("invalid_audit_action")
        page = await self.store.audit_entries(
            action=action,
            before_id=before_id,
            limit=limit,
        )
        return {
            **page,
            "entries": [self._public_entry(entry) for entry in page["entries"]],
            "actions": [
                {
                    **entry,
                    "label": ACTION_NAMES.get(entry["action"], entry["action"]),
                }
                for entry in page["actions"]
            ],
        }

    @staticmethod
    def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
        action = str(entry["action"])
        try:
            details = json.loads(str(entry.get("details_json", "{}")))
        except (TypeError, ValueError):
            details = {}
        safe_details = {
            key: _safe_value(value)
            for key, value in details.items()
            if key in SAFE_DETAIL_KEYS and _safe_value(value) is not None
        } if isinstance(details, dict) else {}
        return {
            "id": int(entry["id"]),
            "action": action,
            "action_label": ACTION_NAMES.get(action, action),
            "actor": _public_actor(str(entry.get("actor_id", ""))),
            "target": _public_target(str(entry.get("target", ""))),
            "details": safe_details,
            "created_at": str(entry["created_at"]),
        }


def _safe_value(value: Any) -> Any | None:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        if len(value) > 160 or "/" in value or "\\" in value or value.startswith("file:"):
            return None
        return value
    if isinstance(value, dict) and len(value) <= 20:
        result = {}
        for key, item in value.items():
            safe = _safe_value(item)
            if re.fullmatch(r"[a-zA-Z0-9_:-]{1,64}", str(key)) and safe is not None:
                result[str(key)] = safe
        return result
    return None


def _public_actor(actor: str) -> str:
    if actor.startswith("webui:") or actor == "session":
        return "WebUI 会话"
    try:
        ipaddress.ip_address(actor)
    except ValueError:
        return actor[:80] if actor else "未知"
    return "客户端地址已隐藏"


def _public_target(target: str) -> str:
    if len(target) > 120 or "/" in target or "\\" in target or target.startswith("file:"):
        return "已隐藏"
    return target or "-"
