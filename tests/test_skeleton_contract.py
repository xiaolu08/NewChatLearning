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
    module_purge = main_source.index("for _module_name, _module in tuple(sys.modules.items())")
    application_import = main_source.index("from new_chat_learning.application.library")
    assert root_setup < module_purge < application_import
    assert "importlib.invalidate_caches()" in main_source[module_purge:application_import]


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
    assert schema["reply"]["items"]["group_type_probability_overrides"]["type"] == "template_list"
    assert schema["reply"]["items"]["regex_enabled"]["default"] is True
    assert schema["reply"]["items"]["similarity_enabled"]["default"] is False
    assert schema["reply"]["items"]["similarity_threshold"]["default"] == 0.5
    assert schema["library"]["items"]["mode"]["default"] == "group"
    assert schema["library"]["items"]["group_tags"]["type"] == "template_list"
    assert schema["library"]["items"]["group_tags"]["default"] == []
    assert schema["library"]["items"]["share_groups"]["type"] == "template_list"
    assert schema["library"]["items"]["share_groups"]["default"] == []
    share_items = schema["library"]["items"]["share_groups"]["templates"]["share_groups"]["items"]
    assert share_items["welcome_message"]["type"] == "string"
    assert share_items["reply_cooldown_minutes"]["type"] == "int"
    assert share_items["sanhao_learning_enabled"]["type"] == "bool"
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
    assert 'id="share-welcome-group"' in dashboard
    assert 'id="share-welcome-message"' in dashboard
    assert 'id="share-reply-cooldown"' in dashboard
    assert 'id="share-sanhao-learning"' in dashboard
    assert 'apiGet("api/share-groups")' in dashboard
    assert 'postSecure("api/share-groups/welcome"' in dashboard
    assert 'postSecure("api/share-groups/reply-cooldown"' in dashboard
    assert 'postSecure("api/share-groups/sanhao-learning"' in dashboard
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
    assert 'id="tab-tasks"' in dashboard
    assert 'id="tasks-view"' in dashboard
    assert 'apiGet("api/tasks")' in dashboard
    assert 'postSecure("api/tasks/save"' in dashboard
    assert 'postSecure("api/tasks/run"' in dashboard
    assert 'postSecure("api/tasks/delete"' in dashboard
    tasks_refresh = dashboard.split(
        'el("tasks-refresh").addEventListener', 1
    )[1].split('el("task-save")', 1)[0]
    assert 'refreshAuth()' not in tasks_refresh
    assert 'location.reload' not in tasks_refresh
    assert 'id="tab-migration"' in dashboard
    assert 'id="migration-view"' in dashboard
    assert '#migration-prepare:disabled { cursor: not-allowed; }' in dashboard
    assert '#migration-prepare.busy:disabled { cursor: progress; }' in dashboard
    assert 'button.classList.add("busy")' in dashboard
    assert 'button.classList.remove("busy")' in dashboard
    assert 'id="password"' not in dashboard
    assert 'id="confirm-password"' not in dashboard
    assert 'apiPost("api/auth/login", { password: "" })' in dashboard
    assert 'authState.entry_mode !== "passwordless"' in dashboard
    assert "插件版本未完整更新" in dashboard
    assert '进入 NewChatLearning' in dashboard
    assert '>进入</button>' in dashboard
    assert 'minlength="12"' not in dashboard
    assert 'bridge.upload("api/migration/upload", file)' in dashboard
    assert "api/migration/upload?ticket=" not in dashboard
    assert 'postSecure("api/migration/prepare"' in dashboard
    assert 'postSecure("api/migration/apply"' in dashboard
    assert 'id="library-export"' in dashboard
    assert 'id="library-export-legacy"' in dashboard
    assert 'postSecure("api/library/export/prepare"' in dashboard
    assert 'format: libraryExportFormat' in dashboard
    assert 'bridge.download("api/library/export", { ticket: prepared.ticket }' in dashboard
    assert "csrf_token: authState" not in dashboard.split(
        'bridge.download("api/library/export"', 1
    )[1].split(";", 1)[0]
    assert 'confirmed: true' in dashboard
    assert 'id="audit-view"' in dashboard
    assert 'apiGet("api/audit"' in dashboard
    assert 'id="tab-diagnostics"' in dashboard
    assert 'id="diagnostics-view"' in dashboard
    assert 'apiGet("api/diagnostics")' in dashboard
    diagnostics_refresh = dashboard.split(
        'el("diagnostics-refresh").addEventListener', 1
    )[1].split('el("migration-refresh")', 1)[0]
    assert 'refreshAuth()' not in diagnostics_refresh
    assert 'location.reload' not in diagnostics_refresh
    assert 'await refreshOverview();' in dashboard
    refresh_handler = dashboard.split(
        'el("overview-refresh").addEventListener', 1
    )[1].split('el("tab-groups")', 1)[0]
    assert 'refreshAuth()' not in refresh_handler
    assert 'location.reload' not in refresh_handler


def test_dashboard_dense_shell_is_accessible_responsive_and_offline():
    dashboard = (ROOT / "pages" / "dashboard" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="dashboard-sidebar"' in dashboard
    assert 'id="nav-toggle"' in dashboard
    assert 'aria-controls="dashboard-sidebar"' in dashboard
    assert 'aria-current="page"' in dashboard
    assert 'tab.setAttribute("aria-current", "page")' in dashboard
    assert 'id="view-title"' in dashboard
    assert 'id="view-description"' in dashboard
    assert "const viewMeta = {" in dashboard
    assert "@media (max-width: 820px)" in dashboard
    assert "@media (max-width: 600px)" in dashboard
    assert "@media (prefers-reduced-motion: reduce)" in dashboard
    assert "button:focus-visible" in dashboard
    assert "font-variant-numeric: tabular-nums" in dashboard
    assert "body.nav-open .sidebar" in dashboard
    assert "window.addEventListener(\"keydown\"" in dashboard
    assert "https://cdn" not in dashboard
