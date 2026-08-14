from __future__ import annotations

import ctypes
import json
import os
import secrets as _secrets
from ctypes import wintypes
from pathlib import Path
from typing import Any


class TTSSecretStore:
    """Store cloud credentials with Windows user-scope DPAPI."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "secrets" / "tts-secrets.dpapi"

    def read(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        plaintext = self._unprotect(self.path.read_bytes())
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("tts_secret_store_corrupt") from exc
        if not isinstance(payload, dict):
            raise TypeError("tts_secret_store_corrupt")
        return {
            str(key): str(value)
            for key, value in payload.items()
            if str(key) and isinstance(value, str)
        }

    def update(self, values: dict[str, Any]) -> None:
        current = self.read()
        for key, value in values.items():
            name = str(key).strip()
            if not name or len(name) > 80:
                raise ValueError("invalid_tts_secret_name")
            text = str(value or "")
            if len(text) > 4096 or "\x00" in text:
                raise ValueError("invalid_tts_secret")
            if text:
                current[name] = text
            else:
                current.pop(name, None)
        if not current:
            self.clear()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        protected = self._protect(
            json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        temporary = self.path.with_name(f".{self.path.name}.{_secrets.token_hex(8)}.tmp")
        temporary.write_bytes(protected)
        os.replace(temporary, self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def status(self) -> dict[str, Any]:
        values = self.read()
        return {
            "configured": bool(values),
            "keys": sorted(values),
            "mask": _mask_secret(next(iter(values.values()), "")),
        }

    @staticmethod
    def _protect(value: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("dpapi_unavailable")
        return _dpapi_call("CryptProtectData", value)

    @staticmethod
    def _unprotect(value: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("dpapi_unavailable")
        return _dpapi_call("CryptUnprotectData", value)


def _mask_secret(value: str) -> str:
    value = str(value)
    if not value:
        return ""
    return "*" * max(4, len(value) - 4) + value[-4:]


def _dpapi_call(function_name: str, value: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    source_buffer = ctypes.create_string_buffer(value)
    source = DATA_BLOB(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = DATA_BLOB()
    function = getattr(crypt32, function_name)
    function.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)):
        raise OSError(ctypes.get_last_error(), f"{function_name} failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
