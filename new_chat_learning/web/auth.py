from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from new_chat_learning.infrastructure.database import SQLiteStore

COOKIE_NAME = "ncl_admin_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_LOCK_SECONDS = 15 * 60
LOGIN_FAILURE_LIMIT = 5


@dataclass(frozen=True, slots=True)
class AuthSession:
    token: str
    csrf_token: str
    expires_at: float


class WebAuthService:
    def __init__(self, data_dir: Path, store: SQLiteStore | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.credential_path = self.data_dir / "webui-password.json"
        self._sessions: dict[str, AuthSession] = {}
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self.store = store

    @property
    def is_configured(self) -> bool:
        return self.credential_path.is_file()

    async def state(self, session_token: str) -> dict[str, Any]:
        async with self._lock:
            session = self._valid_session(session_token)
            return {
                "setup_required": not self.is_configured,
                "authenticated": session is not None,
                "csrf_token": session.csrf_token if session is not None else None,
                "session_expires_at": session.expires_at if session is not None else None,
            }

    async def setup(self, password: str, client_host: str) -> tuple[str, AuthSession | None]:
        async with self._lock:
            if self.is_configured:
                return "already_configured", None
            if not _is_loopback(client_host):
                return "loopback_required", None
            password_error = _password_error(password)
            if password_error:
                return password_error, None
            credential = await asyncio.to_thread(_make_credential, password)
            await asyncio.to_thread(self._write_credential, credential)
            self._sessions.clear()
            await self._audit("webui_password_setup", client_host, {"result": "success"})
            return "ok", self._new_session()

    async def login(self, password: str, client_host: str) -> tuple[str, AuthSession | None]:
        async with self._lock:
            now = time.time()
            client_key = _client_key(client_host)
            if not self.is_configured:
                return "setup_required", None
            if self._is_locked(client_key, now) or self._is_locked("account", now):
                return "locked", None
            try:
                credential = await asyncio.to_thread(self._load_credential)
            except (OSError, ValueError, TypeError):
                await self._audit("webui_login", client_host, {"result": "credential_error"})
                return "credential_error", None
            valid = await asyncio.to_thread(_verify_password, password, credential)
            if not valid:
                self._record_failure(client_key, now)
                self._record_failure("account", now)
                await self._audit("webui_login", client_host, {"result": "failure"})
                return "invalid_credentials", None
            self._failures.pop(client_key, None)
            self._failures.pop("account", None)
            self._locked_until.pop(client_key, None)
            self._locked_until.pop("account", None)
            await self._audit("webui_login", client_host, {"result": "success"})
            return "ok", self._new_session(now)

    async def logout(self, session_token: str, csrf_token: str) -> bool:
        async with self._lock:
            session = self._valid_session(session_token)
            if session is None or not hmac.compare_digest(session.csrf_token, csrf_token):
                return False
            self._sessions.pop(session_token, None)
            await self._audit("webui_logout", "session", {"result": "success"})
            return True

    async def change_password(
        self,
        *,
        session_token: str,
        csrf_token: str,
        current_password: str,
        new_password: str,
    ) -> tuple[str, AuthSession | None]:
        async with self._lock:
            session = self._valid_session(session_token)
            if session is None:
                return "unauthorized", None
            if not hmac.compare_digest(session.csrf_token, csrf_token):
                return "csrf_invalid", None
            password_error = _password_error(new_password)
            if password_error:
                return password_error, None
            try:
                credential = await asyncio.to_thread(self._load_credential)
            except (OSError, ValueError, TypeError):
                return "credential_error", None
            if not await asyncio.to_thread(_verify_password, current_password, credential):
                return "invalid_credentials", None
            replacement = await asyncio.to_thread(_make_credential, new_password)
            await asyncio.to_thread(self._write_credential, replacement)
            self._sessions.clear()
            await self._audit("webui_password_change", "session", {"result": "success"})
            return "ok", self._new_session()

    async def authorize(self, session_token: str, csrf_token: str | None = None) -> bool:
        async with self._lock:
            session = self._valid_session(session_token)
            if session is None:
                return False
            return csrf_token is None or hmac.compare_digest(session.csrf_token, csrf_token)

    async def reauthenticate(
        self,
        *,
        session_token: str,
        csrf_token: str,
        password: str,
    ) -> str:
        async with self._lock:
            session = self._valid_session(session_token)
            if session is None:
                return "unauthorized"
            if not hmac.compare_digest(session.csrf_token, csrf_token):
                return "csrf_invalid"
            now = time.time()
            failure_key = f"reauth:{hashlib.sha256(session_token.encode('utf-8')).hexdigest()}"
            if self._is_locked(failure_key, now):
                return "locked"
            try:
                credential = await asyncio.to_thread(self._load_credential)
            except (OSError, ValueError, TypeError):
                return "credential_error"
            if not await asyncio.to_thread(_verify_password, password, credential):
                self._record_failure(failure_key, now)
                await self._audit(
                    "webui_reauthentication", "session", {"result": "failure"}
                )
                return "invalid_credentials"
            self._failures.pop(failure_key, None)
            self._locked_until.pop(failure_key, None)
            await self._audit("webui_reauthentication", "session", {"result": "success"})
            return "ok"

    def _new_session(self, now: float | None = None) -> AuthSession:
        now = time.time() if now is None else now
        session = AuthSession(
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=now + SESSION_TTL_SECONDS,
        )
        self._sessions[session.token] = session
        return session

    def _valid_session(self, token: str) -> AuthSession | None:
        now = time.time()
        expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)
        return self._sessions.get(str(token or ""))

    def _record_failure(self, key: str, now: float) -> None:
        recent = [value for value in self._failures.get(key, []) if now - value < LOGIN_WINDOW_SECONDS]
        recent.append(now)
        self._failures[key] = recent
        if len(recent) >= LOGIN_FAILURE_LIMIT:
            self._locked_until[key] = now + LOGIN_LOCK_SECONDS

    def _is_locked(self, key: str, now: float) -> bool:
        locked_until = self._locked_until.get(key, 0.0)
        if locked_until <= now:
            self._locked_until.pop(key, None)
            return False
        return True

    def _load_credential(self) -> dict[str, Any]:
        payload = json.loads(self.credential_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("algorithm") != "scrypt":
            raise ValueError("invalid credential file")
        return payload

    def _write_credential(self, credential: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.credential_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(credential, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(self.credential_path)

    async def _audit(self, action: str, actor_id: str, details: dict[str, Any]) -> None:
        if self.store is None:
            return
        await self.store.record_audit(
            actor_id=str(actor_id or "unknown"),
            action=action,
            target="webui",
            details=details,
        )


def _make_credential(password: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    now = int(time.time())
    return {
        "algorithm": "scrypt",
        "salt": salt.hex(),
        "digest": digest.hex(),
        "n": 2**14,
        "r": 8,
        "p": 1,
        "dklen": 32,
        "created_at": now,
        "updated_at": now,
    }


def _verify_password(password: str, credential: dict[str, Any]) -> bool:
    try:
        expected = bytes.fromhex(str(credential["digest"]))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(str(credential["salt"])),
            n=int(credential["n"]),
            r=int(credential["r"]),
            p=int(credential["p"]),
            dklen=int(credential["dklen"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def _password_error(password: str) -> str | None:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        return "password_too_short"
    if len(password) > PASSWORD_MAX_LENGTH:
        return "password_too_long"
    return None


def _is_loopback(client_host: str) -> bool:
    try:
        return ipaddress.ip_address(str(client_host)).is_loopback
    except ValueError:
        return False


def _client_key(client_host: str) -> str:
    try:
        return str(ipaddress.ip_address(str(client_host)))
    except ValueError:
        return "unknown"
