"""Four-track Sunor merge via ffmpeg crossfade."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.application.pipeline.sunor_service import _generate_merge_4tracks
from app.application.pipeline.tts_chunking import concat_audio_to_mp3


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _make_sine_wav(path: Path, *, seconds: float = 0.25, freq: int = 440) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={seconds}",
            "-ar",
            "24000",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
def test_concat_four_tracks_fade_5000ms(tmp_path: Path):
    paths = []
    for i, freq in enumerate((440, 520, 600, 680), start=1):
        p = tmp_path / f"t{i}.wav"
        _make_sine_wav(p, freq=freq)
        paths.append(p)
    out = tmp_path / "merged.mp3"
    concat_audio_to_mp3(paths, out, fade_ms=5000)
    assert out.exists()
    assert out.stat().st_size > 200


@pytest.mark.asyncio
async def test_generate_merge_4tracks_downloads_and_concat(tmp_path, monkeypatch):
    from app.application.pipeline import sunor_service

    monkeypatch.setattr(sunor_service, "UPLOAD_DIR", tmp_path)

    wav = tmp_path / "part.wav"
    _make_sine_wav(wav)

    async def fake_download(url, dest):
        dest.write_bytes(wav.read_bytes())

    fake_track = type("T", (), {"audio_url": "http://example/a.mp3"})()

    async def fake_create(*args, **kwargs):
        return fake_track, "task-1", [fake_track, fake_track]

    monkeypatch.setattr(sunor_service, "_create_and_poll_music", fake_create)
    monkeypatch.setattr(sunor_service, "download_url_to_file", fake_download)
    monkeypatch.setattr(
        sunor_service,
        "_api_settings",
        lambda: ("key", "http://sunor.test", 60),
    )
    monkeypatch.setattr(sunor_service, "_max_poll_attempts", lambda _t: 3)

    with patch.object(sunor_service, "build_music_input_from_config", return_value={}):
        result = await _generate_merge_4tracks(
            {
                "generation_mode": "merge_4tracks",
                "merge_crossfade_ms": 5000,
                "title": "Evening calm",
                "tags": "ambient",
            }
        )

    assert Path(result.path).is_file()
    assert result.title == "Evening calm"
