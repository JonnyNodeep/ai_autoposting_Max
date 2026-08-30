"""Tests for sunor_service orchestration."""

from unittest.mock import AsyncMock, patch

import pytest

from app.application.pipeline.sunor_service import (
    SunorGenerationError,
    build_music_input_from_config,
    generate_sunor_track,
)
from app.infrastructure.services.sunor_client import MusicTrack, SunorTaskResult


def test_build_music_input_inspiration():
    inp = build_music_input_from_config(
        {"music_mode": "inspiration", "gpt_description_prompt": "lofi beat"}
    )
    assert inp["gpt_description_prompt"] == "lofi beat"


def test_build_music_input_instrumental():
    inp = build_music_input_from_config(
        {"music_mode": "instrumental", "tags": "jazz piano"}
    )
    assert inp["make_instrumental"] is True
    assert inp["tags"] == "jazz piano"


@pytest.mark.asyncio
async def test_generate_sunor_track_no_api_key(monkeypatch):
    monkeypatch.setattr("app.application.pipeline.sunor_service.settings.sunor.api_key", "")
    with pytest.raises(SunorGenerationError, match="API"):
        await generate_sunor_track({"music_mode": "inspiration", "gpt_description_prompt": "x"})


@pytest.mark.asyncio
async def test_generate_sunor_track_success(monkeypatch, tmp_path):
    monkeypatch.setattr("app.application.pipeline.sunor_service.settings.sunor.api_key", "k")
    monkeypatch.setattr("app.application.pipeline.sunor_service.UPLOAD_DIR", tmp_path)

    track = MusicTrack(
        audio_id="clip-1",
        audio_url="https://cdn.example/a.mp3",
        image_url="https://cdn.example/cover.jpg",
        title="Lullaby",
    )

    async def _fake_create(*args, **kwargs):
        return track, "task-1", [track]

    async def _fake_extend(*args, **kwargs):
        return track, "", [track]

    async def _fake_download(url, dest, **kwargs):
        dest.write_bytes(b"ID3")
        return dest

    monkeypatch.setattr(
        "app.application.pipeline.sunor_service._create_and_poll_music",
        _fake_create,
    )
    monkeypatch.setattr(
        "app.application.pipeline.sunor_service._maybe_extend_track",
        _fake_extend,
    )
    monkeypatch.setattr(
        "app.application.pipeline.sunor_service.download_url_to_file",
        _fake_download,
    )

    result = await generate_sunor_track(
        {
            "music_mode": "custom",
            "prompt": "[Verse]\nSleep",
            "tags": "lullaby",
        }
    )
    assert result.clip_id == "clip-1"
    assert result.task_id == "task-1"
    assert result.image_url == "https://cdn.example/cover.jpg"


@pytest.mark.asyncio
async def test_generate_sunor_track_download_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr("app.application.pipeline.sunor_service.settings.sunor.api_key", "k")
    monkeypatch.setattr("app.application.pipeline.sunor_service.UPLOAD_DIR", tmp_path)

    tracks = [
        MusicTrack(audio_id="clip-1", audio_url="https://cdn.example/1.mp3", variant_index=0),
        MusicTrack(audio_id="clip-2", audio_url="https://cdn.example/2.mp3", variant_index=1),
    ]

    async def _fake_create(*args, **kwargs):
        return tracks[0], "task-1", tracks

    calls: list[str] = []

    async def _fake_download(url, dest, **kwargs):
        calls.append(url)
        if url.endswith("/1.mp3"):
            raise RuntimeError("HTTP 403")
        dest.write_bytes(b"ID3")
        return dest

    monkeypatch.setattr(
        "app.application.pipeline.sunor_service._create_and_poll_music",
        _fake_create,
    )
    monkeypatch.setattr(
        "app.application.pipeline.sunor_service._maybe_extend_track",
        AsyncMock(return_value=(tracks[0], "", tracks)),
    )
    monkeypatch.setattr(
        "app.application.pipeline.sunor_service.download_url_to_file",
        _fake_download,
    )

    result = await generate_sunor_track(
        {
            "music_mode": "custom",
            "prompt": "[Verse]\nSleep",
            "tags": "lullaby",
        }
    )
    assert result.clip_id == "clip-2"
    assert calls == ["https://cdn.example/1.mp3", "https://cdn.example/2.mp3"]
