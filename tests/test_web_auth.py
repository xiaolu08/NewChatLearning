import asyncio
import json
import time

from new_chat_learning.infrastructure.database import SQLiteStore
from new_chat_learning.web.auth import SESSION_TTL_SECONDS, WebAuthService


def test_legacy_password_is_removed_and_state_is_ready(tmp_path):
    credential_path = tmp_path / "webui-password.json"
    credential_path.write_text(json.dumps({"algorithm": "scrypt"}), encoding="utf-8")

    async def scenario():
        service = WebAuthService(tmp_path)
        return await service.state(""), credential_path.exists()

    state, exists = asyncio.run(scenario())
    assert exists is False
    assert state["setup_required"] is False
    assert state["authenticated"] is False
    assert state["entry_mode"] == "passwordless"


def test_empty_login_creates_one_hour_session_and_supports_csrf_logout(tmp_path):
    async def scenario():
        service = WebAuthService(tmp_path)
        result, session = await service.login("", "127.0.0.1")
        authorized = await service.authorize(session.token)
        bad_csrf = await service.logout(session.token, "wrong")
        logged_out = await service.logout(session.token, session.csrf_token)
        return result, session, authorized, bad_csrf, logged_out, await service.authorize(session.token)

    result, session, authorized, bad_csrf, logged_out, after_logout = asyncio.run(scenario())
    assert result == "ok"
    assert 3500 <= session.expires_at - time.time() <= SESSION_TTL_SECONDS
    assert authorized is True
    assert bad_csrf is False
    assert logged_out is True
    assert after_logout is False


def test_restart_invalidates_previous_in_memory_sessions(tmp_path):
    async def scenario():
        first = WebAuthService(tmp_path)
        _result, session = await first.login("", "127.0.0.1")
        second = WebAuthService(tmp_path)
        return await second.authorize(session.token)

    assert asyncio.run(scenario()) is False


def test_auth_events_are_audited_without_password_material(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "runtime.sqlite3")
        await store.open()
        service = WebAuthService(tmp_path, store)
        _result, session = await service.login("", "127.0.0.1")
        await service.logout(session.token, session.csrf_token)
        rows = store._require_connection().execute(
            "SELECT action, details_json FROM audit_log ORDER BY id"
        ).fetchall()
        await store.close()
        return rows

    rows = asyncio.run(scenario())
    assert [row["action"] for row in rows] == ["webui_login", "webui_logout"]
    assert all("password" not in row["details_json"] for row in rows)
