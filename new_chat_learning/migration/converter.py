from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from new_chat_learning.domain.message import canonical_json, normalized_components_key
from new_chat_learning.migration.scanner import RestrictedUnpickler, _scan_bytes

TRANSIENT_FIELDS = {"url", "path", "base64", "message_id", "time", "seq"}


def prepare_import(
    source: Path,
    staging_dir: Path,
    *,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    source = Path(source).resolve()
    staging_dir = Path(staging_dir).resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)
    import_id = uuid.uuid4().hex
    output = staging_dir / f"{import_id}.jsonl"
    manifest = staging_dir / f"{import_id}.json"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "new_chat_learning.migration.converter",
                str(source),
                str(output),
                str(manifest),
                import_id,
            ],
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "reason": "prepare_timeout", "import_id": import_id}
    if not manifest.is_file():
        return {
            "status": "error",
            "reason": completed.stderr.strip()[-500:] or "prepare_failed",
            "import_id": import_id,
        }
    try:
        report = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "error", "reason": "invalid_manifest", "import_id": import_id}
    return report


def load_manifest(staging_dir: Path, import_id: str) -> dict[str, Any] | None:
    if not import_id or any(character not in "0123456789abcdef" for character in import_id):
        return None
    path = Path(staging_dir) / f"{import_id}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _worker(source: Path, output: Path, manifest: Path, import_id: str) -> int:
    scan = _scan_bytes(source)
    if scan.get("status") != "compatible":
        report = {"status": "rejected", "reason": scan.get("reason"), "import_id": import_id}
        manifest.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        return 2
    with source.open("rb") as stream:
        root = RestrictedUnpickler(stream).load()
    counters: Counter[str] = Counter()
    digest = hashlib.sha256()
    with output.open("w", encoding="utf-8", newline="\n") as destination:
        for raw_question, question_data in root.items():
            converted = _convert_question(raw_question, question_data, counters)
            if converted is None:
                continue
            line = canonical_json(converted)
            destination.write(line + "\n")
            digest.update((line + "\n").encode("utf-8"))
    report = {
        "status": "prepared",
        "import_id": import_id,
        "source_name": source.name,
        "source_size_bytes": source.stat().st_size,
        "staging_file": output.name,
        "staging_sha256": digest.hexdigest(),
        "question_count": counters["questions"],
        "answer_count": counters["answers"],
        "skipped_questions": counters["skipped_questions"],
        "skipped_answers": counters["skipped_answers"],
        "unknown_components": counters["unknown_components"],
        "skip_reasons": {
            "invalid_question_shape": counters["invalid_question_shape"],
            "invalid_question_components": counters["invalid_question_components"],
            "questions_without_convertible_answers": counters[
                "questions_without_convertible_answers"
            ],
            "invalid_answer_shape": counters["invalid_answer_shape"],
            "invalid_answer_components": counters["invalid_answer_components"],
        },
    }
    manifest.write_text(canonical_json(report), encoding="utf-8")
    return 0


def _convert_question(raw_question: Any, value: Any, counters: Counter[str]) -> dict | None:
    if not isinstance(raw_question, str) or not isinstance(value, dict):
        counters["skipped_questions"] += 1
        counters["invalid_question_shape"] += 1
        return None
    question_components = _parse_components(raw_question, counters)
    answers = value.get("answer")
    if question_components is None or not isinstance(answers, list):
        counters["skipped_questions"] += 1
        counters["invalid_question_components"] += 1
        return None
    converted_answers = []
    for answer in answers:
        if not isinstance(answer, dict):
            counters["skipped_answers"] += 1
            counters["invalid_answer_shape"] += 1
            continue
        components = _parse_components(answer.get("answertext"), counters)
        if components is None:
            counters["skipped_answers"] += 1
            counters["invalid_answer_components"] += 1
            continue
        weight = max(1, _safe_int(answer.get("same"), 0) + 1)
        converted_answers.append(
            {
                "components_json": _components_json(components),
                "normalized_key": normalized_components_key(_matching_components(components)),
                "weight": weight,
                "timestamp": _safe_int(answer.get("time"), 0),
            }
        )
        counters["answers"] += 1
    if not converted_answers:
        counters["skipped_questions"] += 1
        counters["questions_without_convertible_answers"] += 1
        return None
    is_regex = bool(value.get("regular", False))
    normalized_key = normalized_components_key(_matching_components(question_components))
    if is_regex:
        normalized_key = f"regex:{normalized_key}"
    counters["questions"] += 1
    return {
        "components_json": _components_json(question_components),
        "normalized_key": normalized_key,
        "plain_text": _plain_text(question_components),
        "frequency": max(1, _safe_int(value.get("freq"), 1)),
        "is_regex": is_regex,
        "timestamp": _safe_int(value.get("time"), 0),
        "answers": converted_answers,
    }


def _parse_components(value: Any, counters: Counter[str]) -> list[dict] | None:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        try:
            raw = ast.literal_eval(value)
        except (SyntaxError, ValueError, TypeError):
            return None
    else:
        return None
    if not isinstance(raw, list) or not raw:
        return None
    converted = []
    for component in raw:
        if not isinstance(component, dict) or not component.get("type"):
            return None
        item = _convert_component(component)
        if item["type"].lower() not in {
            "plain", "face", "at", "atall", "image", "flashimage", "record", "voice",
            "video", "json", "share", "music", "musicshare", "dice", "file",
        }:
            counters["unknown_components"] += 1
        converted.append(item)
    return converted


def _convert_component(component: dict[str, Any]) -> dict[str, Any]:
    component_type = str(component.get("type"))
    data = {str(key): value for key, value in component.items() if key != "type"}
    if component_type.lower() == "plain":
        data = {"text": str(data.get("text", ""))}
    elif component_type.lower() == "face" and "faceId" in data:
        data["id"] = data.pop("faceId")
    elif component_type.lower() == "at" and "target" in data:
        data["qq"] = data.pop("target")
    elif component_type.lower() == "musicshare":
        data["_type"] = data.pop("kind", "custom")
        data["url"] = data.pop("jumpUrl", data.get("url", ""))
        data["audio"] = data.pop("musicUrl", data.get("audio", ""))
        data["image"] = data.pop("pictureUrl", data.get("image", ""))
        data["content"] = data.pop("summary", data.get("content", ""))
    return {"type": component_type, "data": data}


def _matching_components(components: list[dict]) -> list[dict]:
    result = []
    for component in components:
        data = dict(component["data"])
        for field in TRANSIENT_FIELDS:
            data.pop(field, None)
        result.append({"type": component["type"], "data": data})
    return result


def _components_json(components: list[dict]) -> str:
    return canonical_json({"schema_version": 1, "components": components})


def _plain_text(components: list[dict]) -> str:
    for component in components:
        if component["type"].lower() == "plain":
            return str(component["data"].get("text", "")).strip()
    return ""


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    raise SystemExit(
        _worker(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    )
