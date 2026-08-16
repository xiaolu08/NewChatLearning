from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import regex

from new_chat_learning.domain.message import canonical_json, normalized_components_key
from new_chat_learning.infrastructure.database import SQLiteStore


@dataclass(frozen=True, slots=True)
class AddPairRequest:
    question: str
    answer: str


def parse_add_pair(value: str) -> AddPairRequest | None:
    question, separator, answer = str(value).partition("=>")
    question = question.strip()
    answer = answer.strip()
    if not separator or not question or not answer:
        return None
    return AddPairRequest(question=question, answer=answer)


def plain_components_json(text: str) -> str:
    return canonical_json(
        {
            "schema_version": 1,
            "components": [{"type": "Plain", "data": {"text": text}}],
        }
    )


def plain_normalized_key(text: str) -> str:
    return normalized_components_key([{"type": "Plain", "data": {"text": text}}])


def component_preview(components_json: str, limit: int = 80) -> str:
    try:
        payload = json.loads(components_json)
    except (TypeError, ValueError):
        return "[无法解析的消息]"
    components = payload.get("components", []) if isinstance(payload, dict) else []
    parts = []
    for component in components if isinstance(components, list) else []:
        if not isinstance(component, dict):
            continue
        component_type = str(component.get("type", "未知"))
        data = component.get("data", {})
        if component_type.lower() == "plain" and isinstance(data, dict):
            parts.append(str(data.get("text", "")))
        else:
            parts.append(f"[{component_type}]")
    preview = "".join(parts).strip() or "[空消息]"
    return preview if len(preview) <= limit else f"{preview[: limit - 1]}…"


class LibraryService:
    def __init__(self, store: SQLiteStore, data_dir: Path | None = None) -> None:
        self.store = store
        self.backup_dir = Path(data_dir) / "backups" if data_dir is not None else None

    async def search(self, group_id: str, query: str, limit: int = 10) -> list[dict]:
        return await self.store.search_questions(group_id, query.strip(), limit=limit)

    async def show(self, group_id: str, question_id: int) -> dict | None:
        return await self.store.question_detail(group_id, question_id)

    async def add_text_pair(
        self,
        *,
        group_id: str,
        actor_id: str,
        question: str,
        answer: str,
        is_regex: bool = False,
    ) -> dict:
        if len(question) > 4000 or len(answer) > 4000:
            raise ValueError("text_too_long")
        if is_regex:
            if len(question) > 1000:
                raise ValueError("regex_too_long")
            try:
                regex.compile(question)
            except regex.error as exc:
                raise ValueError("invalid_regex") from exc
        question_key = plain_normalized_key(question)
        if is_regex:
            question_key = f"regex:{question_key}"
        return await self.store.add_custom_pair(
            group_id=group_id,
            actor_id=actor_id,
            question_key=question_key,
            question_components_json=plain_components_json(question),
            question_text=question,
            answer_key=plain_normalized_key(answer),
            answer_components_json=plain_components_json(answer),
            is_regex=is_regex,
        )

    async def set_weight(
        self, *, group_id: str, actor_id: str, answer_id: int, weight: int
    ) -> bool:
        return await self.store.set_answer_weight(
            group_id=group_id,
            actor_id=actor_id,
            answer_id=answer_id,
            weight=weight,
        )

    async def delete_answer(
        self, *, group_id: str, actor_id: str, answer_id: int
    ) -> dict:
        return await self.store.delete_answer(
            group_id=group_id,
            actor_id=actor_id,
            answer_id=answer_id,
        )

    async def delete_question(
        self, *, group_id: str, actor_id: str, question_id: int
    ) -> bool:
        return await self.store.delete_question(
            group_id=group_id,
            actor_id=actor_id,
            question_id=question_id,
        )

    async def delete_answer_with_backup(
        self, *, group_id: str, actor_id: str, answer_id: int
    ) -> dict:
        backup_path = await self._backup_before_delete("answer", answer_id)
        result = await self.delete_answer(
            group_id=group_id,
            actor_id=actor_id,
            answer_id=answer_id,
        )
        return {**result, "backup_path": str(backup_path)}

    async def delete_question_with_backup(
        self, *, group_id: str, actor_id: str, question_id: int
    ) -> dict:
        backup_path = await self._backup_before_delete("question", question_id)
        deleted = await self.delete_question(
            group_id=group_id,
            actor_id=actor_id,
            question_id=question_id,
        )
        return {"deleted": deleted, "backup_path": str(backup_path)}

    async def delete_answer_text_globally(
        self,
        *,
        actor_id: str,
        answer_text: str,
    ) -> dict:
        text = str(answer_text).strip()
        if not text or len(text) > 4000:
            raise ValueError("invalid_global_answer_text")
        if self.backup_dir is None:
            raise RuntimeError("library_backup_directory_unavailable")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.backup_dir / f"before-library-delete-global-reply-{timestamp}.sqlite3"
        return await self.store.delete_answers_globally_with_backup(
            actor_id=actor_id,
            normalized_key=plain_normalized_key(text),
            backup_path=backup_path,
        )

    async def _backup_before_delete(self, target: str, target_id: int) -> Path:
        if self.backup_dir is None:
            raise RuntimeError("library_backup_directory_unavailable")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.backup_dir / (
            f"before-library-delete-{target}-{timestamp}-{target[0].upper()}{target_id}.sqlite3"
        )
        return await self.store.backup_to(backup_path)
