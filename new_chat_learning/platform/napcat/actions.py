from __future__ import annotations

from typing import Any


def _message_id(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    value = response.get("message_id")
    if value is None and isinstance(response.get("data"), dict):
        value = response["data"].get("message_id")
    return str(value) if value not in (None, "") else None


async def send_group_message_with_id(event: Any, chain: Any) -> str | None:
    bot = getattr(event, "bot", None)
    parser = getattr(event.__class__, "_parse_onebot_json", None)
    group_id = str(getattr(event, "get_group_id", lambda: "")())
    if bot is None or not callable(parser) or not group_id.isdigit():
        await event.send(chain)
        return None
    if any(
        component.__class__.__name__.lower() in {"file", "node", "nodes"}
        for component in getattr(chain, "chain", [])
    ):
        await event.send(chain)
        return None
    messages = await parser(chain)
    if not messages:
        return None
    routing = {}
    self_id = str(getattr(event, "get_self_id", lambda: "")())
    if self_id:
        routing["self_id"] = int(self_id) if self_id.isdigit() else self_id
    response = await bot.send_group_msg(
        group_id=int(group_id),
        message=messages,
        **routing,
    )
    from astrbot.api.event import AstrMessageEvent

    await AstrMessageEvent.send(event, chain)
    return _message_id(response)


async def recall_message(event: Any, message_id: str) -> bool:
    bot = getattr(event, "bot", None)
    if bot is None:
        return False
    routing = {}
    self_id = str(getattr(event, "get_self_id", lambda: "")())
    if self_id:
        routing["self_id"] = int(self_id) if self_id.isdigit() else self_id
    await bot.call_action(
        "delete_msg",
        message_id=int(message_id) if str(message_id).isdigit() else str(message_id),
        **routing,
    )
    return True
