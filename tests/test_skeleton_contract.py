import json
from pathlib import Path

import yaml

from new_chat_learning.constants import PLUGIN_NAME, PLUGIN_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_metadata_and_runtime_versions_match():
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))

    assert metadata["name"] == PLUGIN_NAME
    assert metadata["version"] == PLUGIN_VERSION
    assert metadata["astrbot_version"] == ">=4.27.3"
    assert "aiocqhttp" in metadata["support_platforms"]


def test_config_schema_and_dashboard_entry_are_valid():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    dashboard = (ROOT / "pages" / "dashboard" / "index.html").read_text(encoding="utf-8")

    assert schema["storage"]["items"]["media_quota_gb"]["default"] == 10.0
    assert schema["storage"]["items"]["media_persistence_enabled"]["default"] is True
    assert schema["storage"]["items"]["media_max_file_mb"]["default"] == 50.0
    assert schema["learning"]["items"]["enabled"]["default"] is False
    assert schema["learning"]["items"]["interval_seconds"]["default"] == 900
    assert schema["reply"]["items"]["enabled"]["default"] is False
    assert schema["reply"]["items"]["probability_percent"]["default"] == 50.0
    assert schema["reply"]["items"]["regex_enabled"]["default"] is True
    assert schema["reply"]["items"]["similarity_enabled"]["default"] is False
    assert schema["reply"]["items"]["similarity_threshold"]["default"] == 0.5
    assert schema["library"]["items"]["mode"]["default"] == "group"
    assert schema["library"]["items"]["group_tags"]["type"] == "template_list"
    assert schema["library"]["items"]["group_tags"]["default"] == []
    assert schema["tts"]["items"]["enabled"]["default"] is False
    assert 'apiGet("api/status")' in dashboard
    assert 'apiGet("/NewChatLearning/api/status")' not in dashboard
    assert 'id="library"' in dashboard
