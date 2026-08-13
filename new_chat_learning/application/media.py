from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import mimetypes
import socket
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO

from new_chat_learning.domain.message import NormalizedMessage
from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.infrastructure.database import SQLiteStore

MEDIA_TYPES = {"image", "flashimage", "record", "voice", "video", "file"}


@dataclass(frozen=True, slots=True)
class StoredMedia:
    content_hash: str
    relative_path: str
    size_bytes: int


class MediaService:
    def __init__(self, data_dir: Path, store: SQLiteStore, config: ConfigService) -> None:
        self.data_dir = Path(data_dir)
        self.media_dir = self.data_dir / "media"
        self.temp_dir = self.data_dir / "temp"
        self.store = store
        self.config = config
        self._lock = asyncio.Lock()

    async def localize_message(self, message: NormalizedMessage) -> NormalizedMessage:
        settings = self.config.media_settings()
        if not settings["enabled"]:
            return message
        changed = False
        localized = []
        for component in message.components:
            updated = await self._localize_component(component, settings)
            localized.append(updated)
            changed = changed or updated is not component
        return replace(message, components=tuple(localized)) if changed else message

    async def _localize_component(
        self,
        component: dict,
        settings: dict[str, object],
    ) -> dict:
        component_type = str(component.get("type", "")).lower()
        data = component.get("data", {})
        if component_type not in MEDIA_TYPES or not isinstance(data, dict):
            return component
        source = _media_source(data)
        if not source:
            return component
        try:
            stored = await self._store_source(
                source,
                component_type,
                max_file_bytes=int(settings["max_file_bytes"]),
                quota_bytes=int(settings["quota_bytes"]),
                timeout_seconds=float(settings["timeout_seconds"]),
                original_name=str(data.get("name") or data.get("file_name") or ""),
            )
        except (OSError, ValueError, TimeoutError):
            return component
        updated_data = dict(data)
        for field in ("path", "file_", "file"):
            value = str(updated_data.get(field) or "")
            if value and not value.startswith(("http://", "https://")):
                updated_data.pop(field, None)
        url = str(updated_data.get("url") or "")
        if url and not url.startswith(("http://", "https://")):
            updated_data.pop("url", None)
        updated_data.update(
            {
                "media_path": stored.relative_path,
                "content_hash": stored.content_hash,
                "media_state": "healthy",
            }
        )
        return {"type": component.get("type", ""), "data": updated_data}

    async def _store_source(
        self,
        source: str,
        media_type: str,
        *,
        max_file_bytes: int,
        quota_bytes: int,
        timeout_seconds: float,
        original_name: str,
    ) -> StoredMedia:
        async with self._lock:
            current_usage = await self.store.media_usage_bytes()
            self.media_dir.mkdir(parents=True, exist_ok=True)
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            temp_path, content_type = await asyncio.to_thread(
                _copy_source_to_temp,
                source,
                self.temp_dir,
                max_file_bytes,
                timeout_seconds,
            )
            try:
                content_hash, size_bytes = await asyncio.to_thread(_hash_file, temp_path)
                existing = await self.store.find_media_asset(content_hash)
                existing_relative = str(existing.get("relative_path") or "") if existing else ""
                relative = _safe_relative_media_path(
                    self.data_dir,
                    existing_relative,
                ) or (
                    Path("media")
                    / content_hash[:2]
                    / f"{content_hash}{_safe_extension(source, original_name, content_type)}"
                )
                destination = self.data_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    temp_path.unlink(missing_ok=True)
                else:
                    already_counted = existing is not None and existing.get("state") == "healthy"
                    if not already_counted and current_usage + size_bytes > quota_bytes:
                        raise ValueError("media quota reached")
                    temp_path.replace(destination)
                source_url = source if source.startswith(("http://", "https://")) else ""
                await self.store.register_media_asset(
                    content_hash=content_hash,
                    relative_path=relative.as_posix(),
                    media_type=media_type,
                    size_bytes=size_bytes,
                    original_name=original_name,
                    source_url=source_url,
                )
                return StoredMedia(content_hash, relative.as_posix(), size_bytes)
            finally:
                temp_path.unlink(missing_ok=True)


def _media_source(data: dict) -> str:
    return str(data.get("path") or data.get("url") or data.get("file_") or data.get("file") or "")


def _copy_source_to_temp(
    source: str,
    temp_dir: Path,
    max_file_bytes: int,
    timeout_seconds: float,
) -> tuple[Path, str]:
    limit = max_file_bytes
    if limit <= 0:
        raise ValueError("media quota reached")
    with tempfile.NamedTemporaryFile(delete=False, dir=temp_dir, suffix=".part") as handle:
        temp_path = Path(handle.name)
        try:
            if source.startswith("base64://"):
                encoded = source.removeprefix("base64://")
                if len(encoded) > (limit * 4 // 3) + 8:
                    raise ValueError("media exceeds size limit")
                payload = base64.b64decode(encoded, validate=True)
                if len(payload) > limit:
                    raise ValueError("media exceeds size limit")
                handle.write(payload)
                return temp_path, ""
            if source.startswith(("http://", "https://")):
                _validate_public_url(source)
                opener = urllib.request.build_opener(_SafeRedirectHandler())
                request = urllib.request.Request(
                    source,
                    headers={"User-Agent": "NewChatLearning/0.1 Beta"},
                )
                with opener.open(request, timeout=timeout_seconds) as response:
                    declared = int(response.headers.get("Content-Length") or 0)
                    if declared > limit:
                        raise ValueError("media exceeds size limit")
                    _copy_limited(response, handle, limit)
                    return temp_path, str(response.headers.get_content_type() or "")
            local_path = _local_path(source)
            if not local_path.is_file() or local_path.stat().st_size > limit:
                raise ValueError("invalid local media")
            with local_path.open("rb") as input_file:
                _copy_limited(input_file, handle, limit)
            return temp_path, mimetypes.guess_type(local_path.name)[0] or ""
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise


def _copy_limited(source: BinaryIO, destination: BinaryIO, limit: int) -> None:
    total = 0
    while chunk := source.read(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise ValueError("media exceeds size limit")
        destination.write(chunk)


def _local_path(source: str) -> Path:
    if source.startswith("file:"):
        parsed = urllib.parse.urlparse(source)
        path = urllib.request.url2pathname(parsed.path)
        if len(path) >= 3 and path[0] in {"/", "\\"} and path[2] == ":":
            path = path[1:]
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        return Path(path)
    return Path(source)


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        while chunk := file.read(64 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _safe_extension(source: str, original_name: str, content_type: str) -> str:
    candidates = [original_name, urllib.parse.urlparse(source).path]
    for candidate in candidates:
        suffix = Path(candidate).suffix.lower()
        if suffix and len(suffix) <= 10 and suffix[1:].isalnum():
            return suffix
    return mimetypes.guess_extension(content_type, strict=False) or ""


def _safe_relative_media_path(data_dir: Path, value: str) -> Path | None:
    if not value:
        return None
    relative = Path(value)
    if relative.is_absolute():
        return None
    root = data_dir.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        return None
    return relative


def _validate_public_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("unsupported media URL")
    try:
        default_port = 443 if parsed.scheme == "https" else 80
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or default_port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise ValueError("media host cannot be resolved") from error
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("private media URL is not allowed")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
