from __future__ import annotations

import ast
import json
import os
import pickle
import pickletools
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar

FORBIDDEN_OPCODES = {
    "GLOBAL",
    "STACK_GLOBAL",
    "REDUCE",
    "BUILD",
    "INST",
    "OBJ",
    "NEWOBJ",
    "NEWOBJ_EX",
    "EXT1",
    "EXT2",
    "EXT4",
    "PERSID",
    "BINPERSID",
}


def scan_file(path: Path, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Scan an old .cl file in a separate restricted process."""
    target = Path(path).resolve()
    if target.suffix.lower() != ".cl":
        return _error_report(target, "unsupported_extension")
    if not target.is_file():
        return _error_report(target, "file_not_found")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "new_chat_learning.migration.scanner", str(target)],
            capture_output=True,
            env=_worker_environment(),
            text=True,
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _error_report(target, "scan_timeout")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        detail = completed.stderr.strip()[-500:] or "invalid_worker_output"
        return _error_report(target, detail)
    if not isinstance(report, dict):
        return _error_report(target, "invalid_report")
    return report


def scan_directory(path: Path, *, timeout_seconds: float = 30.0) -> list[dict[str, Any]]:
    root = Path(path)
    return [scan_file(item, timeout_seconds=timeout_seconds) for item in sorted(root.glob("*.cl"))]


def _worker(path: Path) -> int:
    report = _scan_bytes(path)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report.get("status") == "compatible" else 2


def _scan_bytes(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    opcode_counts: Counter[str] = Counter()
    forbidden: Counter[str] = Counter()
    try:
        for opcode, _arg, _position in pickletools.genops(data):
            opcode_counts[opcode.name] += 1
            if opcode.name in FORBIDDEN_OPCODES:
                forbidden[opcode.name] += 1
    except Exception as exc:  # noqa: BLE001 - worker must convert every malformed pickle to a report
        return _error_report(path, f"opcode_parse_failed:{type(exc).__name__}")
    if forbidden:
        return {
            "status": "rejected",
            "path": str(path),
            "size_bytes": len(data),
            "reason": "forbidden_pickle_opcode",
            "forbidden_opcodes": dict(forbidden),
            "opcode_counts": dict(opcode_counts),
        }
    try:
        with path.open("rb") as stream:
            root = RestrictedUnpickler(stream).load()
    except Exception as exc:  # noqa: BLE001 - worker must never propagate untrusted input
        return _error_report(path, f"restricted_load_failed:{type(exc).__name__}")
    structure = _summarize_library(root)
    if structure["question_count"] == 0:
        return {
            "status": "unsupported",
            "path": str(path),
            "size_bytes": len(data),
            "reason": "no_question_entries",
            "structure": structure,
            "opcode_counts": dict(opcode_counts),
        }
    return {
        "status": "compatible",
        "path": str(path),
        "size_bytes": len(data),
        "reason": "restricted_basic_containers",
        "structure": structure,
        "opcode_counts": dict(opcode_counts),
    }


class RestrictedUnpickler(pickle.Unpickler):
    _allowed: ClassVar[dict[str, set[str]]] = {
        "builtins": {"dict", "list", "tuple", "set", "str", "bytes", "int", "float", "bool"}
    }

    def find_class(self, module: str, name: str) -> Any:
        if module in self._allowed and name in self._allowed[module]:
            return getattr(__import__(module), name)
        raise pickle.UnpicklingError(f"global:{module}.{name}")

    def persistent_load(self, pid: Any) -> Any:
        raise pickle.UnpicklingError(f"persistent_id:{pid!r}")


def _summarize_library(root: Any) -> dict[str, Any]:
    question_count = 0
    answer_count = 0
    malformed_questions = 0
    component_types: Counter[str] = Counter()
    root_kind = type(root).__name__
    entries = root.items() if isinstance(root, dict) else ()
    for _question_key, question in entries:
        if not isinstance(question, dict):
            malformed_questions += 1
            continue
        answers = question.get("answer")
        if not isinstance(answers, list):
            malformed_questions += 1
            continue
        question_count += 1
        answer_count += len(answers)
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            raw = answer.get("answertext")
            if isinstance(raw, str):
                try:
                    components = json.loads(raw)
                except (TypeError, ValueError):
                    try:
                        components = ast.literal_eval(raw)
                    except (SyntaxError, ValueError, TypeError):
                        continue
                if isinstance(components, list):
                    for component in components:
                        if isinstance(component, dict):
                            component_types[str(component.get("type", "unknown"))] += 1
            elif isinstance(raw, list):
                for component in raw:
                    if isinstance(component, dict):
                        component_types[str(component.get("type", "unknown"))] += 1
    return {
        "root_type": root_kind,
        "question_count": question_count,
        "answer_count": answer_count,
        "malformed_questions": malformed_questions,
        "component_types": dict(component_types),
    }


def _error_report(path: Path, reason: str) -> dict[str, Any]:
    return {
        "status": "error",
        "path": str(path),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "reason": reason,
    }


def _worker_environment() -> dict[str, str]:
    environment = os.environ.copy()
    plugin_root = str(Path(__file__).resolve().parents[2])
    current = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (plugin_root, current) if part
    )
    return environment


if __name__ == "__main__":
    raise SystemExit(_worker(Path(sys.argv[1])))
