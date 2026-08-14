import asyncio
import io
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from new_chat_learning.infrastructure.config import ConfigService
from new_chat_learning.tts.service import TTSService, _is_loopback_http_url, _reply_text


def enabled_config(**overrides):
    values = {
        "enabled": True,
        "driver": "local_http",
        "probability_percent": 100,
        "max_text_length": 100,
        "voice": "test",
        "endpoint_url": "http://127.0.0.1:9880/tts",
        "timeout_seconds": 5,
    }
    values.update(overrides)
    return ConfigService({"tts": values})


def test_tts_only_extracts_plain_text_and_ignores_at_components():
    assert _reply_text(
        (
            {"type": "At", "data": {"qq": "12345"}},
            {"type": "Plain", "data": {"text": " hello "}},
            {"type": "Plain", "data": {"text": "world"}},
        )
    ) == "hello world"
    assert _reply_text(({"type": "Image", "data": {"url": "https://example.test/a"}},)) == ""


def test_local_http_tts_requires_literal_loopback_host_and_explicit_port():
    assert _is_loopback_http_url("http://127.0.0.1:9880/tts") is True
    assert _is_loopback_http_url("http://localhost:9880/tts") is True
    assert _is_loopback_http_url("http://[::1]:9880/tts") is True
    assert _is_loopback_http_url("http://127.0.0.1/tts") is False
    assert _is_loopback_http_url("http://192.168.1.5:9880/tts") is False
    assert _is_loopback_http_url("http://user@127.0.0.1:9880/tts") is False


def test_tts_reply_is_disabled_for_probability_length_and_mixed_media(tmp_path):
    async def scenario():
        disabled = TTSService(tmp_path, ConfigService({}))
        probability = TTSService(
            tmp_path,
            enabled_config(probability_percent=0, enabled=False),
        )
        too_long = TTSService(tmp_path, enabled_config(max_text_length=3))
        mixed = TTSService(tmp_path, enabled_config())
        components = ({"type": "Plain", "data": {"text": "hello"}},)
        return (
            await disabled.synthesize_reply(components),
            await probability.synthesize_reply(components),
            await too_long.synthesize_reply(components),
            await mixed.synthesize_reply(
                components + ({"type": "Image", "data": {"url": "x"}},)
            ),
        )

    assert asyncio.run(scenario()) == (None, None, None, None)


def test_local_http_tts_writes_valid_wav_and_disables_proxy_redirects(tmp_path, monkeypatch):
    wave = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"data" * 8
    captured = {}

    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(wave)
            headers = Message()
            headers["Content-Type"] = "audio/wav"
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    class Opener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["payload"] = request.data
            return Response()

    monkeypatch.setattr(
        "new_chat_learning.tts.service.urllib.request.build_opener",
        lambda *handlers: captured.setdefault("handlers", handlers) and Opener(),
    )
    service = TTSService(tmp_path, enabled_config())

    output = asyncio.run(service.synthesize("hello"))

    assert output.is_file()
    assert output.read_bytes() == wave
    assert captured["url"] == "http://127.0.0.1:9880/tts"
    assert b'"text": "hello"' in captured["payload"]
    assert len(captured["handlers"]) == 2


def test_tts_status_marks_cloud_driver_unavailable_without_exposing_secrets(tmp_path):
    service = TTSService(
        tmp_path,
        SimpleNamespace(
            tts_settings=lambda: {
                "enabled": False,
                "driver": "openai",
                "endpoint_url": "https://api.example.test/tts",
                "probability_percent": 0,
                "max_text_length": 100,
            }
        ),
    )

    status = service.status()
    assert status["enabled"] is False
    assert status["driver"] == "openai"
    assert status["available"] is False
    assert status["probability_percent"] == 0
    assert status["max_text_length"] == 100
    assert status["secret_configured"] is False
    assert status["quota"]["daily_requests"] == 200


def test_invalid_audio_response_is_rejected_and_not_left_on_disk(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        def __init__(self, value):
            super().__init__(value)
            self.headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        "new_chat_learning.tts.service.urllib.request.build_opener",
        lambda *_handlers: SimpleNamespace(open=lambda *_args, **_kwargs: Response(b'{"error":true}')),
    )
    service = TTSService(tmp_path, enabled_config())

    with pytest.raises(ValueError, match="invalid_tts_audio"):
        asyncio.run(service.synthesize("hello"))

    assert not list((Path(tmp_path) / "temp" / "tts").glob("*.wav"))
