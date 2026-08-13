import asyncio
import json
import time

from new_chat_learning.infrastructure.database import SQLiteStore
from new_chat_learning.web.auth import PASSWORD_MIN_LENGTH, SESSION_TTL_SECONDS, WebAuthService


def test_first_setup_requires_loopback_and_strong_password(tmp_path):
    async def scenario():
        service = WebAuthService(tmp_path)
        remote = await service.setup("long-enough-password", "192.168.1.2")
        short = await service.setup("short", "127.0.0.1")
        success = await service.setup("long-enough-password", "::1")
        return service, remote, short, success

    service, remote, short, success = asyncio.run(scenario())

    assert remote == ("loopback_required", None)
    assert short == ("password_too_short", None)
    assert success[0] == "ok"
    assert service.is_configured is True
    credential = json.loads((tmp_path / "webui-password.json").read_text(encoding="utf-8"))
    assert credential["algorithm"] == "scrypt"
    assert "long-enough-password" not in str(credential)


def test_password_minimum_length_is_eight_characters(tmp_path):
    async def scenario():
        too_short = await WebAuthService(tmp_path / "short").setup("1234567", "127.0.0.1")
        accepted = await WebAuthService(tmp_path / "accepted").setup("12345678", "127.0.0.1")
        return too_short, accepted

    too_short, accepted = asyncio.run(scenario())

    assert PASSWORD_MIN_LENGTH == 8
    assert too_short == ("password_too_short", None)
    assert accepted[0] == "ok"


def test_login_session_csrf_logout_and_restart_invalidation(tmp_path):
    async def scenario():
        service = WebAuthService(tmp_path)
        _result, setup_session = await service.setup("long-enough-password", "127.0.0.1")
        logged_in = await service.login("long-enough-password", "127.0.0.1")
        session = logged_in[1]
        authorized = await service.authorize(session.token)
        bad_csrf = await service.logout(session.token, "wrong")
        logged_out = await service.logout(session.token, session.csrf_token)
        after_logout = await service.authorize(session.token)
        restarted = WebAuthService(tmp_path)
        setup_invalidated = await restarted.authorize(setup_session.token)
        return logged_in, authorized, bad_csrf, logged_out, after_logout, setup_invalidated

    result = asyncio.run(scenario())

    assert result[0][0] == "ok"
    assert result[1:] == (True, False, True, False, False)


def test_webui_session_ttl_is_one_hour(tmp_path):
    async def scenario():
        service = WebAuthService(tmp_path)
        _result, session = await service.setup("long-enough-password", "127.0.0.1")
        return session

    session = asyncio.run(scenario())

    assert SESSION_TTL_SECONDS == 60 * 60
    assert 3500 <= session.expires_at - time.time() <= 3600


def test_failed_logins_lock_client_and_account(tmp_path):
    async def scenario():
        service = WebAuthService(tmp_path)
        await service.setup("long-enough-password", "127.0.0.1")
        failures = [await service.login("wrong-password", "127.0.0.1") for _ in range(5)]
        locked = await service.login("long-enough-password", "127.0.0.1")
        other_client = await service.login("long-enough-password", "127.0.0.2")
        return failures, locked, other_client

    failures, locked, other_client = asyncio.run(scenario())

    assert all(result[0] == "invalid_credentials" for result in failures)
    assert locked == ("locked", None)
    assert other_client == ("locked", None)


def test_password_change_requires_csrf_and_invalidates_old_sessions(tmp_path):
    async def scenario():
        service = WebAuthService(tmp_path)
        _result, session = await service.setup("long-enough-password", "127.0.0.1")
        invalid = await service.change_password(
            session_token=session.token,
            csrf_token="wrong",
            current_password="long-enough-password",
            new_password="another-long-password",
        )
        changed = await service.change_password(
            session_token=session.token,
            csrf_token=session.csrf_token,
            current_password="long-enough-password",
            new_password="another-long-password",
        )
        old_session_valid = await service.authorize(session.token)
        old_password = await service.login("long-enough-password", "127.0.0.1")
        new_password = await service.login("another-long-password", "127.0.0.1")
        return invalid, changed, old_session_valid, old_password, new_password

    invalid, changed, old_session_valid, old_password, new_password = asyncio.run(scenario())

    assert invalid == ("csrf_invalid", None)
    assert changed[0] == "ok"
    assert old_session_valid is False
    assert old_password[0] == "invalid_credentials"
    assert new_password[0] == "ok"


def test_explicit_session_invalidation_clears_all_logins(tmp_path):
    async def scenario():
        service = WebAuthService(tmp_path)
        _result, first = await service.setup("long-enough-password", "127.0.0.1")
        _result, second = await service.login("long-enough-password", "127.0.0.1")
        await service.invalidate_all_sessions()
        return await service.authorize(first.token), await service.authorize(second.token)

    assert asyncio.run(scenario()) == (False, False)


def test_auth_events_are_audited_without_password_material(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "runtime.sqlite3")
        await store.open()
        service = WebAuthService(tmp_path, store)
        _result, session = await service.setup("long-enough-password", "127.0.0.1")
        await service.login("wrong-password", "127.0.0.1")
        await service.login("long-enough-password", "127.0.0.1")
        await service.logout(session.token, session.csrf_token)
        rows = store._require_connection().execute(
            "SELECT action, details_json FROM audit_log ORDER BY id"
        ).fetchall()
        await store.close()
        return rows

    rows = asyncio.run(scenario())

    actions = [row["action"] for row in rows]
    serialized = " ".join(row["details_json"] for row in rows)
    assert actions == ["webui_password_setup", "webui_login", "webui_login", "webui_logout"]
    assert "long-enough-password" not in serialized
    assert "wrong-password" not in serialized


def test_corrupt_credential_file_fails_closed(tmp_path):
    async def scenario():
        service = WebAuthService(tmp_path)
        service.credential_path.write_text("not-json", encoding="utf-8")
        return await service.login("long-enough-password", "127.0.0.1")

    assert asyncio.run(scenario()) == ("credential_error", None)
