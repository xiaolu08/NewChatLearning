from __future__ import annotations

from typing import Any


def is_plugin_admin(event: Any, config: dict[str, Any] | None) -> bool:
    if bool(getattr(event, "is_admin", lambda: False)()):
        return True
    sender_id = str(getattr(event, "get_sender_id", lambda: "")())
    permissions = (config or {}).get("permissions", {})
    configured = permissions.get("plugin_admin_ids", []) if isinstance(permissions, dict) else []
    return sender_id in {str(item).strip() for item in configured if str(item).strip()}
