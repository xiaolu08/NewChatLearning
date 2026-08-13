import json
from pathlib import Path

import yaml

from new_chat_learning.constants import PLUGIN_NAME, PLUGIN_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_metadata_and_runtime_versions_match():
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))

    assert metadata["name"] == PLUGIN_NAME
    assert metadata["version"] == PLUGIN_VERSION
    assert metadata["astrbot_version"] == ">=4.27.2"
    assert "aiocqhttp" in metadata["support_platforms"]


def test_astrbot_plugin_root_is_importable_before_application_imports():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    root_setup = main_source.index("PLUGIN_ROOT = Path(__file__).resolve().parent")
    application_import = main_source.index("from new_chat_learning.application.library")
    assert root_setup < application_import


def test_config_schema_and_dashboard_entry_are_valid():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    dashboard = (ROOT / "pages" / "dashboard" / "index.html").read_text(encoding="utf-8")

    assert schema["storage"]["items"]["media_quota_gb"]["default"] == 10.0
    assert schema["storage"]["items"]["media_persistence_enabled"]["default"] is True
    assert schema["storage"]["items"]["media_max_file_mb"]["default"] == 50.0
    assert schema["learning"]["items"]["enabled"]["default"] is False
    assert schema["learning"]["items"]["interval_seconds"]["default"] == 900
    assert schema["learning"]["items"]["target_users"]["type"] == "template_list"
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
    bridge_script = '<script src="/api/plugin/page/bridge-sdk.js"></script>'
    assert dashboard.count(bridge_script) == 1
    assert dashboard.index(bridge_script) < dashboard.index(
        "const bridge = window.AstrBotPluginPage;"
    )
    assert 'id="library"' in dashboard
    assert 'id="tab-groups"' in dashboard
    assert 'id="overview-refresh"' in dashboard
    assert 'id="tab-audit"' in dashboard
    assert 'id="tab-permissions"' in dashboard
    assert 'id="permissions-view"' in dashboard
    assert 'apiGet("api/permissions")' in dashboard
    assert 'api/permissions/update' in dashboard
    assert 'id="tab-tts"' in dashboard
    assert 'id="tts-view"' in dashboard
    assert 'apiGet("api/tts/settings")' in dashboard
    assert 'api/tts/settings/update' in dashboard
    assert 'api/tts/test' in dashboard
    assert 'id="tab-migration"' in dashboard
    assert 'id="migration-view"' in dashboard
    assert 'bridge.upload("api/migration/upload", file)' in dashboard
    assert "api/migration/upload?ticket=" not in dashboard
    assert 'postSecure("api/migration/prepare"' in dashboard
    assert 'postSecure("api/migration/apply"' in dashboard
    assert 'id="library-export"' in dashboard
    assert 'postSecure("api/library/export/prepare"' in dashboard
    assert 'bridge.download("api/library/export", { ticket: prepared.ticket }' in dashboard
    assert "csrf_token: authState" not in dashboard.split(
        'bridge.download("api/library/export"', 1
    )[1].split(";", 1)[0]
    assert 'confirmed: true' in dashboard
    assert 'id="audit-view"' in dashboard
    assert 'apiGet("api/audit"' in dashboard
    assert 'await refreshOverview();' in dashboard
    refresh_handler = dashboard.split(
        'el("overview-refresh").addEventListener', 1
    )[1].split('el("tab-groups")', 1)[0]
    assert 'refreshAuth()' not in refresh_handler
    assert 'location.reload' not in refresh_handler
