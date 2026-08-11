"""Tests for TTS provider catalogs and SpeechKit client helpers."""

from __future__ import annotations

import base64
import json

import pytest

from app.application.pipeline.normalize import _normalize_tts_gen_config
from app.application.pipeline.tts_chunking import max_chars_for_model
from app.application.pipeline.tts_voices import (
    DEFAULT_SPEECHKIT_VOICE,
    SPEECHKIT_MAX_CHARS,
    resolve_role,
)
from app.infrastructure.services.yandex_speechkit_client import _extract_audio_bytes


def test_normalize_speechkit_defaults():
    cfg = _normalize_tts_gen_config(
        {"enabled": True, "provider": "speechkit", "voice": "dasha", "speed": 0.9}
    )
    assert cfg["provider"] == "speechkit"
    assert cfg["voice"] == "dasha"
    assert cfg["speed"] == 0.9
    assert cfg["role"] == "neutral"


def test_normalize_legacy_without_provider_is_openai():
    cfg = _normalize_tts_gen_config({"voice": "shimmer", "speed": 0.85})
    assert cfg["provider"] == "openai"
    assert cfg["voice"] == "shimmer"


def test_normalize_resets_openai_voice_from_speechkit():
    cfg = _normalize_tts_gen_config(
        {"provider": "openai", "voice": "dasha", "speed": 0.9}
    )
    assert cfg["provider"] == "openai"
    assert cfg["voice"] == "shimmer"


def test_normalize_resets_speechkit_voice_from_openai():
    cfg = _normalize_tts_gen_config(
        {"provider": "speechkit", "voice": "shimmer", "speed": 0.9}
    )
    assert cfg["voice"] == DEFAULT_SPEECHKIT_VOICE


def test_resolve_role_for_dasha():
    assert resolve_role("dasha", "friendly") == "friendly"
    assert resolve_role("dasha", "whisper") == "neutral"
    assert resolve_role("filipp", "good") is None


def test_max_chars_speechkit():
    assert max_chars_for_model("speechkit") == SPEECHKIT_MAX_CHARS


def test_extract_audio_ndjson():
    payload = base64.b64encode(b"ID3fake-mp3-bytes").decode()
    line = json.dumps({"result": {"audioChunk": {"data": payload}}})
    raw = (line + "\n").encode()
    out = _extract_audio_bytes(raw)
    assert out.startswith(b"ID3")


@pytest.mark.asyncio
async def test_tts_gen_speechkit_route(monkeypatch):
    from app.application.pipeline.blocks.tts_gen import TtsGenBlock
    from app.application.pipeline.context import PipelineContext

    called = {}

    class _YSK:
        async def synthesize(self, text, **kwargs):
            called["text"] = text
            called["kwargs"] = kwargs
            return "/tmp/ysk.mp3"

    monkeypatch.setattr(
        "app.infrastructure.services.yandex_speechkit_client.YandexSpeechKitService",
        lambda: _YSK(),
    )

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=None,
        story_script="Сказка про зайца",
    )
    await TtsGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "provider": "speechkit",
            "voice": "dasha",
            "speed": 0.9,
            "role": "friendly",
        },
    )
    assert ctx.audio_local_path == "/tmp/ysk.mp3"
    assert "зайца" in called["text"]
    assert called["kwargs"]["voice"] == "dasha"
    assert called["kwargs"]["speed"] == 0.9
    assert called["kwargs"]["role"] == "friendly"


@pytest.mark.asyncio
async def test_tts_gen_openai_route():
    from app.application.pipeline.blocks.tts_gen import TtsGenBlock
    from app.application.pipeline.context import PipelineContext

    class _OAI:
        async def generate_speech(self, text, **kwargs):
            assert kwargs["voice"] == "shimmer"
            assert kwargs["speed"] == 0.85
            return "/tmp/oai.mp3"

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=_OAI(),
        story_script="Сказка",
    )
    await TtsGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "provider": "openai",
            "model": "gpt-4o-mini-tts",
            "voice": "shimmer",
            "speed": 0.85,
            "instructions": "Speak softly",
        },
    )
    assert ctx.audio_local_path == "/tmp/oai.mp3"
