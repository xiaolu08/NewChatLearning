import asyncio
import json
import zipfile
from xml.etree import ElementTree

from new_chat_learning.application.export import LibraryExportService, _excel_text
from new_chat_learning.application.library import LibraryService
from new_chat_learning.infrastructure.database import SQLiteStore


def test_group_export_contains_safe_xlsx_lossless_jsonl_and_audit(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "library.sqlite3")
        await store.open()
        library = LibraryService(store, tmp_path)
        await library.add_text_pair(
            group_id="10001",
            actor_id="7",
            question="=SUM(1,1)",
            answer="+danger",
        )
        result = await LibraryExportService(tmp_path, store).export_group(
            group_id="10001",
            actor_id="7",
            source="command",
        )
        audit = await store.audit_entries(action="export_library", limit=10)
        await store.close()
        return result, audit

    result, audit = asyncio.run(scenario())

    assert result["question_count"] == 1
    assert result["answer_count"] == 1
    assert result["path"].name.startswith("NewChatLearning-group-10001-")
    with zipfile.ZipFile(result["path"]) as package:
        names = package.namelist()
        xlsx_name = next(name for name in names if name.endswith(".xlsx"))
        jsonl_name = next(name for name in names if name.endswith(".jsonl"))
        manifest = json.loads(package.read("manifest.json"))
        record = json.loads(package.read(jsonl_name).decode("utf-8"))
        assert manifest["group_id"] == "10001"
        assert record["group_id"] == "10001"
        assert record["question_text"] == "=SUM(1,1)"
        assert record["answer_components"]["components"][0]["data"]["text"] == "+danger"
        xlsx_bytes = package.read(xlsx_name)
    xlsx_path = tmp_path / "check.xlsx"
    xlsx_path.write_bytes(xlsx_bytes)
    with zipfile.ZipFile(xlsx_path) as workbook:
        sheet = workbook.read("xl/worksheets/sheet1.xml")
        ElementTree.fromstring(sheet)
        sheet_text = sheet.decode("utf-8")
        assert "'=SUM(1,1)" in sheet_text
        assert "'+danger" in sheet_text
    entry = audit["entries"][0]
    details = json.loads(entry["details_json"])
    assert details == {
        "group_id": "10001",
        "question_count": 1,
        "answer_count": 1,
        "source": "command",
    }
    assert "SUM" not in entry["details_json"]


def test_empty_group_export_is_valid(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "empty.sqlite3")
        await store.open()
        result = await LibraryExportService(tmp_path, store).export_group(
            group_id="10001", actor_id="7", source="webui"
        )
        await store.close()
        return result

    result = asyncio.run(scenario())
    assert result["question_count"] == 0
    assert result["answer_count"] == 0
    with zipfile.ZipFile(result["path"]) as package:
        jsonl_name = next(name for name in package.namelist() if name.endswith(".jsonl"))
        assert package.read(jsonl_name) == b""


def test_export_file_is_removed_when_audit_fails(tmp_path):
    class FailingAuditStore(SQLiteStore):
        async def record_audit(self, **_kwargs):
            raise RuntimeError("audit unavailable")

    async def scenario():
        store = FailingAuditStore(tmp_path / "audit-failure.sqlite3")
        await store.open()
        service = LibraryExportService(tmp_path, store)
        try:
            await service.export_group(group_id="10001", actor_id="7", source="webui")
        except RuntimeError:
            pass
        await store.close()
        return list(service.export_dir.glob("*.zip"))

    assert asyncio.run(scenario()) == []


def test_excel_text_removes_xml_invalid_characters_and_protects_formulas():
    assert _excel_text("=bad\x00\ud800value") == "'=badvalue"
