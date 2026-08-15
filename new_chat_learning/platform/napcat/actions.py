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
    try:
        messages = await parser(chain)
    except Exception:  # noqa: BLE001 - media resolvers use adapter-specific exception types
        # Expired media must not abort an otherwise valid text reply.
        fallback = type(chain)()
        fallback.chain = [
            component
            for component in getattr(chain, "chain", [])
            if component.__class__.__name__.lower()
            not in {"image", "record", "video", "file"}
        ]
        if not fallback.chain:
            return None
        try:
            messages = await parser(fallback)
        except Exception:  # noqa: BLE001 - media resolvers use adapter-specific exception types
            return None
    if not messages:
        return None
    routing = {}
    self_id = str(getattr(event, "get_self_id", lambda: "")())
    if self_id:
        routing["self_id"] = int(self_id) if self_id.isdigit() else self_id
    try:
        response = await bot.send_group_msg(
            group_id=int(group_id),
            message=messages,
            **routing,
        )
    except Exception:  # noqa: BLE001 - OneBot adapters expose platform-specific failures
        # A stale QQ media reference can parse successfully but fail only when NapCat
        # tries to download it. Retry once without media so mixed text replies survive.
        fallback = type(chain)()
        fallback.chain = [
            component
            for component in getattr(chain, "chain", [])
            if component.__class__.__name__.lower()
            not in {"image", "record", "video", "file", "marketface"}
        ]
        if not fallback.chain:
            return None
        try:
            fallback_messages = await parser(fallback)
            if not fallback_messages:
                return None
            response = await bot.send_group_msg(
                group_id=int(group_id),
                message=fallback_messages,
                **routing,
            )
        except Exception:  # noqa: BLE001 - stale media must not break the event pipeline
            return None
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
