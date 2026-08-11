"""Tests for TTS chunking and audio concat."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.application.pipeline.tts_chunking import (
    TTS_LOUDNORM,
    _acrossfade_filter,
    _audio_filter_chain,
    _mp3_encode_args,
    concat_audio_to_mp3,
    max_chars_for_model,
)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _make_sine_wav(path: Path, *, seconds: float = 0.3, freq: int = 440) -> None:
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


def test_max_chars_for_model():
    assert max_chars_for_model("gpt-4o-mini-tts") == 1600
    assert max_chars_for_model("tts-1-hd") == 4096


def test_loudnorm_in_encode_helpers():
    assert "loudnorm=I=-16" in TTS_LOUDNORM
    assert _audio_filter_chain(TTS_LOUDNORM) == TTS_LOUDNORM
    args = _mp3_encode_args()
    assert args[args.index("-af") + 1] == TTS_LOUDNORM
    assert "320k" in args


def test_acrossfade_filter_two_and_three():
    f2 = _acrossfade_filter(2, 0.05)
    assert "acrossfade" in f2
    assert "loudnorm" in f2
    assert "[joined]" in f2
    f3 = _acrossfade_filter(3, 0.05)
    assert "[a0]" in f3
    assert f3.count("acrossfade") == 2
    assert "loudnorm" in f3


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
def test_concat_audio_to_mp3_single(tmp_path: Path):
    wav = tmp_path / "a.wav"
    out = tmp_path / "out.mp3"
    _make_sine_wav(wav)
    concat_audio_to_mp3([wav], out)
    assert out.exists()
    assert out.stat().st_size > 100


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
def test_concat_audio_to_mp3_with_acrossfade(tmp_path: Path):
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    out = tmp_path / "out.mp3"
    _make_sine_wav(a, freq=440)
    _make_sine_wav(b, freq=660)
    concat_audio_to_mp3([a, b], out, fade_ms=50)
    assert out.exists()
    assert out.stat().st_size > 100
