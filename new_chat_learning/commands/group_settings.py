from __future__ import annotations

import shlex
from dataclasses import dataclass

from new_chat_learning.domain.reply_policy import normalize_trigger_type

MODES = {"disabled", "learning", "reply", "learning_reply", "silent"}
LEARNING_MODES = {"learning", "learning_reply", "silent"}


@dataclass(frozen=True)
class GroupSettingsCommand:
    name: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class CrossGroupCommand:
    action: str
    category: str = ""
    group_ids: tuple[str, ...] = ()
    tag: str | None = None
    message_type: str | None = None


@dataclass(frozen=True)
class LegacyGlobalCommand:
    capability: str
    arguments: tuple[str, ...] = ()


def parse_legacy_group_command(text: str) -> GroupSettingsCommand | CrossGroupCommand | None:
    normalized_text = text.strip()
    if normalized_text.startswith("！"):
        normalized_text = f"!{normalized_text[1:]}"
    try:
        parts = shlex.split(normalized_text)
    except ValueError:
        return None
    if not parts:
        return None
    command = parts[0].lower()
    raw_arguments = tuple(parts[1:])
    arguments = tuple(part.lower() for part in raw_arguments)
    if command == "!learning":
        return GroupSettingsCommand("learning", arguments)
    if command == "!reply":
        if arguments and arguments[0] == "-s":
            message_type = normalize_trigger_type(
                raw_arguments[1] if len(raw_arguments) >= 2 else None
            )
            if message_type is not None:
                return CrossGroupCommand(
                    "set",
                    "reply_type_probability",
                    tuple(raw_arguments[3:]) if len(raw_arguments) >= 4 else (),
                    raw_arguments[2] if len(raw_arguments) >= 3 else None,
                    message_type,
                )
            return CrossGroupCommand(
                "set",
                "reply_probability",
                tuple(raw_arguments[2:]) if len(raw_arguments) >= 3 else (),
                raw_arguments[1] if len(raw_arguments) >= 2 else None,
            )
        if arguments and arguments[0] == "-d":
            message_type = normalize_trigger_type(
                raw_arguments[1] if len(raw_arguments) >= 2 else None
            )
            if message_type is not None:
                return CrossGroupCommand(
                    "remove",
                    "reply_type_probability",
                    tuple(raw_arguments[2:]) if len(raw_arguments) >= 3 else (),
                    message_type=message_type,
                )
            return CrossGroupCommand(
                "remove",
                "reply_probability",
                tuple(raw_arguments[1:]) if len(raw_arguments) >= 2 else (),
            )
        return GroupSettingsCommand("reply", arguments)
    if command == "!grouplist" and not arguments:
        return CrossGroupCommand("list")
    if command == "!sharelist" and not arguments:
        return CrossGroupCommand("share_list")
    if command in {"!add", "!remove"} and arguments:
        action = "add" if command == "!add" else "remove"
        category = arguments[0]
        if category not in {
            "learning",
            "learnings",
            "reply",
            "tag",
            "subadmin",
            "unmerge",
            "globe",
            "share",
        }:
            return None
        if category == "tag" and action == "add":
            if len(raw_arguments) < 3:
                return CrossGroupCommand(action, category)
            return CrossGroupCommand(
                action,
                category,
                tuple(raw_arguments[2:]),
                raw_arguments[1],
            )
        if category == "share":
            if len(raw_arguments) < 3:
                return CrossGroupCommand(action, category)
            return CrossGroupCommand(
                action,
                category,
                tuple(raw_arguments[1:-1]),
                raw_arguments[-1],
            )
        return CrossGroupCommand(action, category, tuple(raw_arguments[1:]))
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
