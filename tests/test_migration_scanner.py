import pickle

from new_chat_learning.migration.scanner import scan_file


def test_scanner_restricts_and_summarizes_basic_library(tmp_path):
    path = tmp_path / "safe.cl"
    payload = {
        "question": {
            "answer": [
                {"answertext": "[{\"type\": \"Plain\", \"text\": \"answer\"}]"}
            ]
        }
    }
    path.write_bytes(pickle.dumps(payload, protocol=4))

    report = scan_file(path)

    assert report["status"] == "compatible"
    assert report["structure"]["question_count"] == 1
    assert report["structure"]["answer_count"] == 1
    assert report["structure"]["component_types"] == {"Plain": 1}


def test_scanner_rejects_global_opcode_without_loading(tmp_path):
    path = tmp_path / "unsafe.cl"
    path.write_bytes(b"cposix\nsystem\n.")

    report = scan_file(path)

    assert report["status"] == "rejected"
    assert report["reason"] == "forbidden_pickle_opcode"
    assert report["forbidden_opcodes"]["GLOBAL"] == 1


def test_scanner_reports_unknown_structure(tmp_path):
    path = tmp_path / "unknown.cl"
    path.write_bytes(pickle.dumps(["not", "a", "mapping"], protocol=4))

    report = scan_file(path)

    assert report["status"] == "unsupported"
    assert report["reason"] == "no_question_entries"


def test_scanner_worker_imports_from_outside_plugin_directory(tmp_path, monkeypatch):
    path = tmp_path / "safe.cl"
    path.write_bytes(
        pickle.dumps(
            {
                "question": {
                    "answer": [{"answertext": "[{'type': 'Plain', 'text': 'answer'}]"}]
                }
            },
            protocol=4,
        )
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    report = scan_file(path)

    assert report["status"] == "compatible"
    assert report["structure"]["question_count"] == 1
