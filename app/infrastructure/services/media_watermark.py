"""Logo overlay for publish-time watermarks. Never mutates the source file."""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

LOGO_WIDTH_RATIO = 0.12
LOGO_OPACITY = 0.7
MARGIN_RATIO = 0.03


def apply_logo_image(src: str, logo_path: str, dest: str) -> str:
    """Composite channel logo onto image; write to dest. Returns dest."""
    from PIL import Image

    src_p = Path(src)
    logo_p = Path(logo_path)
    dest_p = Path(dest)
    if not src_p.is_file():
        raise FileNotFoundError(f"Image source missing: {src}")
    if not logo_p.is_file():
        raise FileNotFoundError(f"Logo missing: {logo_path}")

    base = Image.open(src_p).convert("RGBA")
    logo = Image.open(logo_p).convert("RGBA")

    target_w = max(1, int(base.width * LOGO_WIDTH_RATIO))
    ratio = target_w / logo.width
    target_h = max(1, int(logo.height * ratio))
    logo = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)

    if LOGO_OPACITY < 1.0:
        alpha = logo.split()[-1]
        alpha = alpha.point(lambda p: int(p * LOGO_OPACITY))
        logo.putalpha(alpha)

    margin = max(8, int(base.width * MARGIN_RATIO))
    x = base.width - logo.width - margin
    y = base.height - logo.height - margin
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay.paste(logo, (x, y), logo)
    combined = Image.alpha_composite(base, overlay)

    dest_p.parent.mkdir(parents=True, exist_ok=True)
    if dest_p.suffix.lower() in {".jpg", ".jpeg"}:
        combined.convert("RGB").save(dest_p, "JPEG", quality=95)
    else:
        combined.save(dest_p, "PNG")
    logger.info(f"Logo watermark image: {src} -> {dest}")
    return str(dest_p)


def apply_logo_video(src: str, logo_path: str, dest: str) -> str:
    """Overlay channel logo on video via ffmpeg; write to dest. Returns dest."""
    src_p = Path(src)
    logo_p = Path(logo_path)
    dest_p = Path(dest)
    if not src_p.is_file():
        raise FileNotFoundError(f"Video source missing: {src}")
    if not logo_p.is_file():
        raise FileNotFoundError(f"Logo missing: {logo_path}")

    dest_p.parent.mkdir(parents=True, exist_ok=True)
    # Scale logo to ~12% of main video width (scale2ref), bottom-right.
    filter_complex = (
        f"[1:v]format=rgba,colorchannelmixer=aa={LOGO_OPACITY}[lg];"
        f"[lg][0:v]scale2ref=w=iw*{LOGO_WIDTH_RATIO}:h=ow/mdar[logo][base];"
        f"[base][logo]overlay=W-w-W*{MARGIN_RATIO}:H-h-H*{MARGIN_RATIO}"
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src_p),
            "-i",
            str(logo_p),
            "-filter_complex",
            filter_complex,
            "-codec:a",
            "copy",
            str(dest_p),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f"ffmpeg logo watermark failed: {result.stderr[:400]}")
        raise RuntimeError("Video logo watermark failed")
    logger.info(f"Logo watermark video: {src} -> {dest}")
    return str(dest_p)
