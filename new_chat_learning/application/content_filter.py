from __future__ import annotations

from dataclasses import dataclass

import regex

from new_chat_learning.infrastructure.config import ConfigService


@dataclass(frozen=True, slots=True)
class FilterMatch:
    matched: bool
    rule_type: str = ""
    rule_index: int = -1


class ContentFilterService:
    def __init__(self, config: ConfigService) -> None:
        self.config = config

    def reply_match(self, group_id: str, components: tuple[dict, ...]) -> FilterMatch:
        settings = self.config.filter_settings(group_id)
        if not settings["enabled"]:
            return FilterMatch(False)
        texts = self._plain_texts(components)
        joined = "".join(texts).strip()
        for index, value in enumerate(settings["contains"]):
            if any(value in text for text in texts):
                return FilterMatch(True, "contains", index)
        for index, value in enumerate(settings["exact"]):
            if joined == value:
                return FilterMatch(True, "exact", index)
        for index, value in enumerate(settings["sensitive"]):
            if any(value in text for text in texts):
                return FilterMatch(True, "sensitive", index)
        timeout = float(settings["regex_timeout_ms"]) / 1000.0
        for index, pattern in enumerate(settings["regex"]):
            try:
                if regex.search(pattern, joined, timeout=timeout) is not None:
                    return FilterMatch(True, "regex", index)
            except (regex.error, TimeoutError):
                continue
        blocked_types = set(settings["component_types"])
        for component in components:
            if str(component.get("type", "")).lower() in blocked_types:
                return FilterMatch(True, "component_type", -1)
        return FilterMatch(False)

    def sensitive_match(self, group_id: str, components: tuple[dict, ...]) -> FilterMatch:
        settings = self.config.filter_settings(group_id)
        if not settings["enabled"]:
            return FilterMatch(False)
        texts = self._plain_texts(components)
        for index, value in enumerate(settings["sensitive"]):
            if any(value in text for text in texts):
                return FilterMatch(True, "sensitive", index)
        return FilterMatch(False)

    @staticmethod
    def _plain_texts(components: tuple[dict, ...]) -> list[str]:
        return [
            str(component.get("data", {}).get("text", ""))
            for component in components
            if str(component.get("type", "")).lower() == "plain"
            and isinstance(component.get("data"), dict)
        ]
