from __future__ import annotations

from dataclasses import dataclass

MODES = {"disabled", "learning", "reply", "learning_reply", "silent"}
LEARNING_MODES = {"learning", "learning_reply", "silent"}


@dataclass(frozen=True)
class GroupSettingsCommand:
    name: str
    arguments: tuple[str, ...] = ()


def parse_legacy_group_command(text: str) -> GroupSettingsCommand | None:
    parts = text.strip().split()
    if not parts:
        return None
    command = parts[0].lower()
    arguments = tuple(part.lower() for part in parts[1:])
    if command == "!learning":
        return GroupSettingsCommand("learning", arguments)
    if command == "!reply":
        return GroupSettingsCommand("reply", arguments)
    if command == "!grouplist" and not arguments:
        return GroupSettingsCommand("mode")
    if command in {"!add", "!remove"} and len(arguments) >= 1:
        action = "on" if command == "!add" else "off"
        category = arguments[0]
        if category in {"learning", "learnings"}:
            return GroupSettingsCommand("learning", (action, *arguments[1:]))
        if category == "reply":
            return GroupSettingsCommand("reply", (action, *arguments[1:]))
    return None


def transition_toggle(mode: str, capability: str, enabled: bool) -> str:
    if mode not in MODES or capability not in {"learning", "reply", "silent"}:
        raise ValueError("invalid_group_command")
    learning = mode in LEARNING_MODES
    reply = mode in {"reply", "learning_reply"}
    silent = mode == "silent"
    if capability == "silent":
        if enabled:
            return "silent"
        return "learning" if silent else mode
    if capability == "learning":
        learning = enabled
        if not enabled:
            silent = False
    else:
        reply = enabled
        if enabled:
            silent = False
    if silent and learning:
        return "silent"
    if learning and reply:
        return "learning_reply"
    if learning:
        return "learning"
    if reply:
        return "reply"
    return "disabled"


def parse_on_off(arguments: tuple[str, ...]) -> bool | None:
    if len(arguments) != 1:
        return None
    value = arguments[0].lower()
    if value == "on":
        return True
    if value == "off":
        return False
    return None
