"""Integration smoke for motion_fx ffmpeg render."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ffmpeg = shutil.which("ffmpeg")


@pytest.mark.skipif(not ffmpeg, reason="ffmpeg not installed")
def test_render_motion_fx_zoom_only(tmp_path: Path):
    from PIL import Image

    from app.application.pipeline.postcards.motion import render_motion_fx

    img = tmp_path / "card.png"
    Image.new("RGB", (640, 640), (180, 140, 200)).save(img)
    out = tmp_path / "zoom.mp4"
    render_motion_fx(
        img,
        out,
        duration_s=3.2,
        preset_name="zoom_only",
        with_zoom=True,
        use_overlay=False,
        max_zoom=1.06,
    )
    assert out.is_file()
    assert out.stat().st_size > 1000


@pytest.mark.skipif(not ffmpeg, reason="ffmpeg not installed")
def test_render_motion_fx_produces_mp4(tmp_path: Path):
    from PIL import Image, ImageStat

    from app.application.pipeline.postcards.motion import (
        ensure_placeholder_overlays,
        render_motion_fx,
    )

    ensure_placeholder_overlays(["soft_sparkle"])
    img = tmp_path / "card.png"
    # Bright postcard so we can detect unwanted darkening
    Image.new("RGB", (640, 640), (210, 160, 180)).save(img)
    out = tmp_path / "out.mp4"
    render_motion_fx(
        img, out, duration_s=3.2, preset_name="soft_sparkle", use_overlay=True
    )
    assert out.is_file()
    assert out.stat().st_size > 1000

    # Extract a mid frame and ensure average brightness stayed high (not black veil)
    frame = tmp_path / "frame.png"
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "1.0",
            "-i",
            str(out),
            "-frames:v",
            "1",
            str(frame),
        ],
        check=True,
        capture_output=True,
    )
    assert frame.is_file()
    mean = ImageStat.Stat(Image.open(frame)).mean
    # Source mean ~210/160/180 → after screen blend should stay clearly bright
    assert sum(mean) / 3 > 120, mean
