from __future__ import annotations

from typing import Any


def is_plugin_admin(event: Any, config: dict[str, Any] | None) -> bool:
    if bool(getattr(event, "is_admin", lambda: False)()):
        return True
    sender_id = str(getattr(event, "get_sender_id", lambda: "")())
    permissions = (config or {}).get("permissions", {})
    configured = permissions.get("plugin_admin_ids", []) if isinstance(permissions, dict) else []
    return sender_id in {str(item).strip() for item in configured if str(item).strip()}


def is_group_admin(event: Any, config: dict[str, Any] | None) -> bool:
    if is_plugin_admin(event, config):
        return True
    sender_id = str(getattr(event, "get_sender_id", lambda: "")())
    group_id = str(getattr(event, "get_group_id", lambda: "")())
    permissions = (config or {}).get("permissions", {})
    entries = permissions.get("group_sub_admins", []) if isinstance(permissions, dict) else []
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("group_id", "")).strip() != group_id:
            continue
        admin_ids = entry.get("admin_ids", [])
        if isinstance(admin_ids, list) and sender_id in {
            str(item).strip() for item in admin_ids if str(item).strip()
        }:
            return True
    return False
