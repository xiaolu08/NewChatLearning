from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from new_chat_learning.domain.message import canonical_json
from new_chat_learning.infrastructure.database import SQLiteStore

EXCEL_MAX_ROWS = 1_048_576
EXCEL_CELL_LIMIT = 32_767
EXPORT_TTL_SECONDS = 86_400


class LibraryExportService:
    def __init__(self, data_dir: Path, store: SQLiteStore) -> None:
        self.export_dir = Path(data_dir) / "exports"
        self.store = store

    async def export_group(self, *, group_id: str, actor_id: str, source: str) -> dict:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        base_name = f"NewChatLearning-group-{group_id}-{timestamp}"
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_expired()
        package_path = self.export_dir / f"{base_name}.zip"
        snapshot_path = self.export_dir / f".{base_name}.sqlite3.tmp"
        await self.store.backup_to(snapshot_path)
        try:
            question_count, answer_count = await asyncio.to_thread(
                self._write_package,
                package_path=package_path,
                base_name=base_name,
                group_id=group_id,
                snapshot_path=snapshot_path,
            )
        except Exception:
            package_path.unlink(missing_ok=True)
            raise
        finally:
            snapshot_path.unlink(missing_ok=True)
        try:
            await self.store.record_audit(
                actor_id=actor_id,
                action="export_library",
                target=f"group:{group_id}",
                details={
                    "group_id": group_id,
                    "question_count": question_count,
                    "answer_count": answer_count,
                    "source": source,
                },
            )
        except Exception:
            package_path.unlink(missing_ok=True)
            raise
        return {
            "path": package_path,
            "filename": package_path.name,
            "question_count": question_count,
            "answer_count": answer_count,
        }

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - EXPORT_TTL_SECONDS
        for path in self.export_dir.glob("NewChatLearning-group-*.zip"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    @staticmethod
    def _write_package(
        *,
        package_path: Path,
        base_name: str,
        group_id: str,
        snapshot_path: Path,
    ) -> tuple[int, int]:
        xlsx_path = package_path.with_suffix(".xlsx.tmp")
        jsonl_path = package_path.with_suffix(".jsonl.tmp")
        connection = sqlite3.connect(snapshot_path)
        connection.row_factory = sqlite3.Row
        try:
            question_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM questions WHERE group_id = ?", (group_id,)
                ).fetchone()[0]
            )
            answer_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM answers AS a JOIN questions AS q "
                    "ON q.id = a.question_id WHERE q.group_id = ?",
                    (group_id,),
                ).fetchone()[0]
            )
            row_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM questions AS q LEFT JOIN answers AS a "
                    "ON a.question_id = q.id WHERE q.group_id = ?",
                    (group_id,),
                ).fetchone()[0]
            )
            _write_xlsx(xlsx_path, connection, group_id, row_count)
            with jsonl_path.open("w", encoding="utf-8", newline="\n") as output:
                for row in _export_rows(connection, group_id):
                    output.write(canonical_json(_jsonl_record(group_id, row)))
                    output.write("\n")
            manifest = {
                "format": "NewChatLearning-library-export",
                "format_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "group_id": group_id,
                "question_count": question_count,
                "answer_count": answer_count,
                "xlsx_note": "Excel 仅供查看；超长单元格会截断，完整组件保存在 JSONL。",
            }
            with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(xlsx_path, f"{base_name}.xlsx")
                archive.writestr("manifest.json", canonical_json(manifest))
                archive.write(jsonl_path, f"{base_name}.jsonl")
            return question_count, answer_count
        finally:
            connection.close()
            xlsx_path.unlink(missing_ok=True)
            jsonl_path.unlink(missing_ok=True)


def _jsonl_record(group_id: str, row: dict) -> dict:
    return {
        "group_id": group_id,
        "question_id": int(row["question_id"]),
        "question_text": str(row["question_text"]),
        "question_components": _json_value(row["question_components_json"]),
        "question_normalized_key": str(row["question_normalized_key"]),
        "is_regex": bool(row["is_regex"]),
        "question_frequency": int(row["question_frequency"]),
        "question_created_at": str(row["question_created_at"]),
        "question_updated_at": str(row["question_updated_at"]),
        "answer_id": int(row["answer_id"]) if row["answer_id"] is not None else None,
        "answer_components": _json_value(row["answer_components_json"]),
        "answer_normalized_key": (
            str(row["answer_normalized_key"])
            if row["answer_normalized_key"] is not None
            else None
        ),
        "answer_weight": int(row["answer_weight"]) if row["answer_weight"] is not None else None,
        "answer_created_at": (
            str(row["answer_created_at"]) if row["answer_created_at"] is not None else None
        ),
        "answer_updated_at": (
            str(row["answer_updated_at"]) if row["answer_updated_at"] is not None else None
        ),
    }


def _json_value(value: object) -> object:
    if value in (None, ""):
        return None
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return {"invalid_json": str(value)}


def _export_rows(connection: sqlite3.Connection, group_id: str):
    return connection.execute(
        "SELECT q.id AS question_id, q.plain_text AS question_text, "
        "q.components_json AS question_components_json, q.is_regex, "
        "q.frequency AS question_frequency, a.id AS answer_id, "
        "a.components_json AS answer_components_json, a.weight AS answer_weight, "
        "q.normalized_key AS question_normalized_key, q.created_at AS question_created_at, "
        "q.updated_at AS question_updated_at, a.normalized_key AS answer_normalized_key, "
        "a.created_at AS answer_created_at, a.updated_at AS answer_updated_at "
        "FROM questions AS q LEFT JOIN answers AS a ON a.question_id = q.id "
        "WHERE q.group_id = ? ORDER BY q.id, a.id",
        (group_id,),
    )


def _write_xlsx(
    path: Path,
    connection: sqlite3.Connection,
    group_id: str,
    row_count: int,
) -> None:
    headers = (
        "问题ID",
        "问题文本",
        "问题类型",
        "问题频次",
        "答案ID",
        "答案预览",
        "答案权重",
    )
    sheet_count = max(1, math.ceil(row_count / (EXCEL_MAX_ROWS - 1)))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", _content_types(sheet_count))
        workbook.writestr("_rels/.rels", _root_relationships())
        workbook.writestr("xl/workbook.xml", _workbook_xml(sheet_count))
        workbook.writestr("xl/_rels/workbook.xml.rels", _workbook_relationships(sheet_count))
        workbook.writestr("xl/styles.xml", _styles_xml())
        rows = iter(_export_rows(connection, group_id))
        for index in range(1, sheet_count + 1):
            with workbook.open(f"xl/worksheets/sheet{index}.xml", "w") as output:
                _write_worksheet(output, headers, rows)


def _component_preview(value: object) -> str:
    payload = _json_value(value)
    if not isinstance(payload, dict):
        return ""
    components = payload.get("components", [])
    if not isinstance(components, list):
        return ""
    parts = []
    for component in components:
        if not isinstance(component, dict):
            continue
        component_type = str(component.get("type", "未知"))
        data = component.get("data", {})
        if component_type.lower() == "plain" and isinstance(data, dict):
            parts.append(str(data.get("text", "")))
        else:
            parts.append(f"[{component_type}]")
    return "".join(parts)


def _write_worksheet(output, headers: tuple[str, ...], rows) -> None:
    output.write(
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b"<sheetData>"
    )
    for row_index in range(1, EXCEL_MAX_ROWS + 1):
        row = None if row_index == 1 else next(rows, None)
        if row_index > 1 and row is None:
            break
        values = headers if row_index == 1 else _xlsx_row(row)
        cells = [f'<row r="{row_index}">']
        for column_index, value in enumerate(values, 1):
            reference = f"{_column_name(column_index)}{row_index}"
            if isinstance(value, int):
                cells.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                text = _excel_text(value)
                cells.append(
                    f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">'
                    f"{escape(text)}</t></is></c>"
                )
        cells.append("</row>")
        output.write("".join(cells).encode("utf-8"))
    output.write(b"</sheetData></worksheet>")


def _xlsx_row(row: sqlite3.Row) -> tuple:
    return (
        row["question_id"],
        row["question_text"],
        "正则" if row["is_regex"] else "文本",
        row["question_frequency"],
        row["answer_id"] if row["answer_id"] is not None else "",
        _component_preview(row["answer_components_json"]),
        row["answer_weight"] if row["answer_weight"] is not None else "",
    )


def _excel_text(value: object) -> str:
    text = "".join(character for character in str(value) if _xml_character(character))
    if text.startswith(("=", "+", "-", "@")):
        text = f"'{text}"
    return text[:EXCEL_CELL_LIMIT]


def _xml_character(character: str) -> bool:
    value = ord(character)
    return value in {0x09, 0x0A, 0x0D} or 0x20 <= value <= 0xD7FF or 0xE000 <= value <= 0xFFFD or 0x10000 <= value <= 0x10FFFF


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _content_types(sheet_count: int) -> str:
    sheets = "".join(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for index in range(1, sheet_count + 1))
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{sheets}</Types>'


def _root_relationships() -> str:
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'


def _workbook_xml(sheet_count: int) -> str:
    sheets = "".join(f'<sheet name="词库{index}" sheetId="{index}" r:id="rId{index}"/>' for index in range(1, sheet_count + 1))
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheets}</sheets></workbook>'


def _workbook_relationships(sheet_count: int) -> str:
    sheets = "".join(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>' for index in range(1, sheet_count + 1))
    style_id = sheet_count + 1
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{sheets}<Relationship Id="rId{style_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'


def _styles_xml() -> str:
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="1"><xf xfId="0"/></cellXfs></styleSheet>'
