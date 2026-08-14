from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import random
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from new_chat_learning.tts.secrets import TTSSecretStore

if TYPE_CHECKING:
    from new_chat_learning.infrastructure.config import ConfigService
    from new_chat_learning.infrastructure.database import SQLiteStore

MAX_AUDIO_BYTES = 10 * 1024 * 1024
GENERATED_AUDIO_TTL_SECONDS = 24 * 60 * 60
LOCAL_HTTP_HOSTS = {"127.0.0.1", "::1", "localhost"}


class TTSService:
    def __init__(
        self,
        data_dir: Path,
        config: ConfigService,
        *,
        store: SQLiteStore | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.secrets = TTSSecretStore(data_dir)
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
            if driver in {
                "volcengine",
                "aliyun",
                "tencent",
                "azure",
                "openai",
                "openai_compatible",
                "custom_http",
            }:
                if self.store is None:
                    raise RuntimeError("tts_quota_store_unavailable")
                quota = await self.store.reserve_tts_quota(
                    now=_utc_now(),
                    minute_bucket=time.strftime("%Y%m%d%H%M", time.gmtime()),
                    day_bucket=time.strftime("%Y%m%d", time.gmtime()),
                    characters=len(text),
                    per_minute_requests=int(settings["per_minute_requests"]),
                    daily_requests=int(settings["daily_requests"]),
                    daily_characters=int(settings["daily_characters"]),
                )
                if not quota["allowed"]:
                    raise RuntimeError(f"tts_{quota['reason']}")
                return await asyncio.to_thread(self._synthesize_cloud, text, settings)
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
            "secret_configured": self.secrets.status()["configured"],
            "quota": {
                "per_minute_requests": settings.get("per_minute_requests", 10),
                "daily_requests": settings.get("daily_requests", 200),
                "daily_characters": settings.get("daily_characters", 50000),
            },
        }

    def secret_status(self) -> dict[str, Any]:
        return self.secrets.status()

    def update_secrets(self, values: dict[str, Any]) -> dict[str, Any]:
        self.secrets.update(values)
        return self.secrets.status()

    def clear_secrets(self) -> dict[str, Any]:
        self.secrets.clear()
        return self.secrets.status()

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

    def _synthesize_cloud(self, text: str, settings: dict[str, Any]) -> Path:
        driver = str(settings["driver"])
        secrets = self.secrets.read()
        required = {
            "openai": ("api_key",),
            "openai_compatible": ("api_key",),
            "azure": ("api_key",),
            "aliyun": ("aliyun_token",),
            "tencent": ("secret_id", "secret_key"),
            "volcengine": ("api_key",),
        }.get(driver, ())
        if any(not secrets.get(key) for key in required):
            raise RuntimeError("tts_secret_missing")
        if driver == "openai":
            endpoint = str(settings["endpoint_url"]) or "https://api.openai.com/v1/audio/speech"
            headers = {"Authorization": f"Bearer {secrets.get('api_key', '')}"}
            payload = {
                "model": settings["model"] or "gpt-4o-mini-tts",
                "voice": settings["voice"] or "alloy",
                "input": text,
                "response_format": "wav",
            }
        elif driver == "openai_compatible":
            endpoint = str(settings["endpoint_url"])
            headers = {"Authorization": f"Bearer {secrets.get('api_key', '')}"}
            payload = {
                "model": settings["model"],
                "voice": settings["voice"],
                "input": text,
                "response_format": "wav",
            }
        elif driver == "azure":
            endpoint = str(settings["endpoint_url"])
            if not endpoint and settings["region"]:
                endpoint = f"https://{settings['region']}.tts.speech.microsoft.com/cognitiveservices/v1"
            headers = {
                "Ocp-Apim-Subscription-Key": secrets.get("api_key", ""),
                "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
            }
            payload = (
                f"<speak version='1.0' xml:lang='zh-CN'><voice name='{_xml_escape(settings['voice'] or 'zh-CN-XiaoxiaoNeural')}'>{_xml_escape(text)}</voice></speak>"
            )
        elif driver == "aliyun":
            endpoint = str(settings["endpoint_url"]) or "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts"
            headers = {"X-NLS-Token": secrets.get("aliyun_token", "")}
            payload = {
                "appkey": settings["app_key"],
                "text": text,
                "format": "wav",
                "sample_rate": 16000,
                "voice": settings["voice"] or "逍遥",
            }
        elif driver == "tencent":
            endpoint = str(settings["endpoint_url"]) or "https://tts.tencentcloudapi.com"
            payload = {
                "Action": "TextToVoice",
                "Version": "2019-08-23",
                "Region": settings["region"] or "ap-guangzhou",
                "Text": text,
                "VoiceType": int(settings.get("voice") or 101001),
                "Codec": "wav",
                "SampleRate": 16000,
                "SessionId": uuid.uuid4().hex,
            }
            headers = _tencent_headers(endpoint, payload, secrets)
        elif driver == "volcengine":
            endpoint = str(settings["endpoint_url"]) or "https://openspeech.bytedance.com/api/v1/tts"
            headers = {"Authorization": f"Bearer {secrets.get('api_key', '')}"}
            payload = {
                "app": {"appid": settings["app_id"]},
                "user": {"uid": "newchatlearning"},
                "audio": {"voice_type": settings["voice"] or "BV700_V2_streaming", "encoding": "wav"},
                "request": {"reqid": uuid.uuid4().hex, "text": text, "operation": "query"},
            }
        else:
            endpoint = str(settings["endpoint_url"])
            headers = dict(settings["custom_headers"])
            payload = _substitute_mapping(settings["custom_body"], text, settings)
        if not endpoint.startswith("https://"):
            raise ValueError("tts_cloud_endpoint_must_be_https")
        if isinstance(payload, str):
            data = payload.encode("utf-8")
            headers = {**headers, "Content-Type": "application/ssml+xml"}
        else:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {**headers, "Content-Type": "application/json"}
        request = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirects())
        with opener.open(request, timeout=float(settings["timeout_seconds"])) as response:
            content_type = str(response.headers.get("Content-Type", "")).lower()
            raw = response.read(MAX_AUDIO_BYTES + 1)
        if driver == "custom_http" and "json" in content_type:
            try:
                value: Any = json.loads(raw.decode("utf-8"))
                for key in str(settings["custom_response_field"]).split("."):
                    value = value[key]
                raw = base64.b64decode(str(value), validate=True)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid_tts_audio") from exc
        if len(raw) > MAX_AUDIO_BYTES or not raw:
            raise ValueError("invalid_tts_audio")
        output_path = self.output_dir / f"{time.time_ns()}-{os.getpid()}.wav"
        output_path.write_bytes(raw)
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


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&apos;")
        .replace('"', "&quot;")
    )


def _substitute_mapping(value: Any, text: str, settings: dict[str, Any]) -> Any:
    replacements = {
        "${text}": text,
        "${voice}": str(settings.get("voice", "")),
        "${model}": str(settings.get("model", "")),
        "${app_id}": str(settings.get("app_id", "")),
        "${app_key}": str(settings.get("app_key", "")),
    }
    if isinstance(value, dict):
        return {str(key): _substitute_mapping(item, text, settings) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_mapping(item, text, settings) for item in value]
    if isinstance(value, str):
        result = value
        for key, replacement in replacements.items():
            result = result.replace(key, replacement)
        return result
    return value


def _tencent_headers(endpoint: str, payload: dict[str, Any], secrets: dict[str, str]) -> dict[str, str]:
    parsed = urllib.parse.urlsplit(endpoint)
    host = parsed.netloc
    service = "tts"
    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    content_type = "application/json; charset=utf-8"
    hashed_payload = hashlib.sha256(body.encode()).hexdigest()
    canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
    signed_headers = "content-type;host"
    canonical_request = "\n".join(["POST", parsed.path or "/", "", canonical_headers, signed_headers, hashed_payload])
    algorithm = "TC3-HMAC-SHA256"
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join([
        algorithm,
        str(timestamp),
        credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    secret_date = hmac.new(b"TC3" + secrets.get("secret_key", "").encode(), date.encode(), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode(), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"{algorithm} Credential={secrets.get('secret_id', '')}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": "TextToVoice",
        "X-TC-Version": "2019-08-23",
        "X-TC-Region": str(payload.get("Region", "ap-guangzhou")),
        "X-TC-Timestamp": str(timestamp),
        "Authorization": authorization,
    }
