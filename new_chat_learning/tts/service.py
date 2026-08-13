from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from new_chat_learning.infrastructure.config import ConfigService

MAX_AUDIO_BYTES = 10 * 1024 * 1024
GENERATED_AUDIO_TTL_SECONDS = 24 * 60 * 60
LOCAL_HTTP_HOSTS = {"127.0.0.1", "::1", "localhost"}


class TTSService:
    def __init__(
        self,
        data_dir: Path,
        config: ConfigService,
        *,
        random_source: random.Random | None = None,
    ) -> None:
        self.config = config
        self.output_dir = Path(data_dir) / "temp" / "tts"
        self.random = random_source or random.Random()
        self._lock = asyncio.Lock()

    async def synthesize_reply(
        self, components: tuple[dict[str, Any], ...]
    ) -> Path | None:
        try:
            settings = self.config.tts_settings()
            if not settings["enabled"]:
                return None
            probability = float(settings["probability_percent"]) / 100.0
            if self.random.random() > probability:
                return None
            text = _reply_text(components)
            if not text or len(text) > int(settings["max_text_length"]):
                return None
            return await self.synthesize(text)
        except (
            OSError,
            RuntimeError,
            ValueError,
            subprocess.SubprocessError,
            urllib.error.URLError,
        ):
            return None

    async def synthesize(self, text: str) -> Path:
        settings = self.config.tts_settings()
        text = str(text).strip()
        if not settings["enabled"]:
            raise RuntimeError("tts_disabled")
        if not text or len(text) > int(settings["max_text_length"]):
            raise ValueError("invalid_tts_text")
        async with self._lock:
            await asyncio.to_thread(self._prune_generated_audio)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            driver = str(settings["driver"])
            if driver == "windows":
                return await asyncio.to_thread(self._synthesize_windows, text, settings)
            if driver in {"gpt_sovits", "local_http"}:
                return await asyncio.to_thread(self._synthesize_http, text, settings)
            raise RuntimeError("tts_driver_unavailable")

    def status(self) -> dict[str, Any]:
        settings = self.config.tts_settings()
        driver = str(settings["driver"])
        available = (
            os.name == "nt" and shutil.which("powershell.exe") is not None
            if driver == "windows"
            else _is_loopback_http_url(str(settings["endpoint_url"]))
            if driver in {"gpt_sovits", "local_http"}
            else False
        )
        return {
            "enabled": settings["enabled"],
            "driver": driver,
            "available": available,
            "probability_percent": settings["probability_percent"],
            "max_text_length": settings["max_text_length"],
        }

    def _synthesize_windows(self, text: str, settings: dict[str, Any]) -> Path:
        if os.name != "nt" or shutil.which("powershell.exe") is None:
            raise RuntimeError("windows_tts_unavailable")
        token = f"{time.time_ns()}-{os.getpid()}"
        text_path = self.output_dir / f"{token}.txt"
        output_path = self.output_dir / f"{token}.wav"
        text_path.write_text(text, encoding="utf-8")
        script = """
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  if ($env:NCL_TTS_VOICE) { $speaker.SelectVoice($env:NCL_TTS_VOICE) }
  $speaker.Rate = [int]$env:NCL_TTS_RATE
  $speaker.Volume = [int]$env:NCL_TTS_VOLUME
  $speaker.SetOutputToWaveFile($env:NCL_TTS_OUTPUT)
  $speaker.Speak([IO.File]::ReadAllText($env:NCL_TTS_TEXT, [Text.Encoding]::UTF8))
} finally { $speaker.Dispose() }
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        env = os.environ.copy()
        env.update(
            {
                "NCL_TTS_TEXT": str(text_path),
                "NCL_TTS_OUTPUT": str(output_path),
                "NCL_TTS_VOICE": str(settings["voice"]),
                "NCL_TTS_RATE": str(settings["rate"]),
                "NCL_TTS_VOLUME": str(settings["volume"]),
            }
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            try:
                result = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-EncodedCommand",
                        encoded,
                    ],
                    check=False,
                    capture_output=True,
                    timeout=float(settings["timeout_seconds"]),
                    env=env,
                    creationflags=creationflags,
                )
            except subprocess.SubprocessError as exc:
                output_path.unlink(missing_ok=True)
                raise RuntimeError("windows_tts_failed") from exc
        finally:
            text_path.unlink(missing_ok=True)
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise RuntimeError("windows_tts_failed")
        _validate_audio_file(output_path)
        return output_path

    def _synthesize_http(self, text: str, settings: dict[str, Any]) -> Path:
        endpoint = str(settings["endpoint_url"])
        if not _is_loopback_http_url(endpoint):
            raise ValueError("tts_endpoint_must_be_loopback")
        if settings["driver"] == "gpt_sovits":
            payload = {
                "text": text,
                "text_lang": settings["text_lang"],
                "ref_audio_path": settings["reference_audio_path"],
                "prompt_text": settings["prompt_text"],
                "prompt_lang": settings["prompt_lang"],
                "text_split_method": "cut0",
                "media_type": "wav",
                "streaming_mode": False,
            }
            if not payload["ref_audio_path"]:
                raise ValueError("gpt_sovits_reference_required")
        else:
            payload = {"text": text, "voice": settings["voice"], "format": "wav"}
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "audio/*"},
            method="POST",
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _RejectRedirects()
        )
        with opener.open(request, timeout=float(settings["timeout_seconds"])) as response:
            content_type = str(response.headers.get("Content-Type", "")).lower()
            data = response.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES or not data:
            raise ValueError("invalid_tts_audio")
        if "audio" not in content_type and not _looks_like_audio(data):
            raise ValueError("invalid_tts_audio")
        output_path = self.output_dir / f"{time.time_ns()}-{os.getpid()}.wav"
        output_path.write_bytes(data)
        _validate_audio_file(output_path)
        return output_path

    def _prune_generated_audio(self) -> None:
        if not self.output_dir.is_dir():
            return
        cutoff = time.time() - GENERATED_AUDIO_TTL_SECONDS
        for path in self.output_dir.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect rejected", headers, fp)


def _reply_text(components: tuple[dict[str, Any], ...]) -> str:
    texts = []
    for component in components:
        component_type = str(component.get("type", "")).lower()
        data = component.get("data", {})
        if component_type in {"at", "atall"}:
            continue
        if component_type != "plain" or not isinstance(data, dict):
            return ""
        text = str(data.get("text", "")).strip()
        if text:
            texts.append(text)
    return " ".join(texts)


def _is_loopback_http_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in LOCAL_HTTP_HOSTS
        and bool(parsed.port)
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def _looks_like_audio(data: bytes) -> bool:
    return data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WAVE"


def _validate_audio_file(path: Path) -> None:
    try:
        size = path.stat().st_size
        header = path.read_bytes()[:12]
    except OSError:
        path.unlink(missing_ok=True)
        raise
    if size <= 12 or size > MAX_AUDIO_BYTES or not _looks_like_audio(header):
        path.unlink(missing_ok=True)
        raise ValueError("invalid_tts_audio")
