"""Motion FX presets and ffmpeg render for Living Postcards."""
from __future__ import annotations

import random
import subprocess
from pathlib import Path
from typing import Sequence

from loguru import logger

FX_DIR = Path(__file__).with_name("fx_overlays")

# 20 distinct overlay stems (black bg + bright particles for screen-blend)
ALL_OVERLAYS: tuple[str, ...] = (
    "soft_sparkle",
    "warm_dust",
    "gold_confetti",
    "soft_glow",
    "sparkle",
    "light_rays",
    "soft_bokeh",
    "gentle_glow",
    "pink_hearts",
    "silver_dust",
    "snow_fall",
    "rose_petals",
    "blue_sparkle",
    "rainbow_dots",
    "star_twinkle",
    "golden_rain",
    "bubble_glow",
    "firefly",
    "confetti_burst",
    "moonlight_shimmer",
)

# Slot groups pick from distinct subsets so morning/day/evening feel different
PRESET_GROUPS: dict[str, tuple[str, ...]] = {
    "morning": (
        "soft_sparkle",
        "warm_dust",
        "golden_rain",
        "firefly",
        "soft_glow",
    ),
    "official": (
        "gold_confetti",
        "confetti_burst",
        "star_twinkle",
        "rainbow_dots",
        "light_rays",
    ),
    "daily": (
        "sparkle",
        "pink_hearts",
        "rose_petals",
        "blue_sparkle",
        "silver_dust",
    ),
    "evening": (
        "soft_bokeh",
        "gentle_glow",
        "snow_fall",
        "bubble_glow",
        "moonlight_shimmer",
    ),
}

DEFAULT_DURATION_S = 4
FPS = 30
_OVERLAY_SIZE = "720x720"
_OVERLAY_DURATION = "3"

# Each recipe: ffmpeg -vf chain applied to black color source (screen-blend friendly)
_OVERLAY_FILTERS: dict[str, str] = {
    # Fine white sparkles
    "soft_sparkle": (
        "geq=lum='if(gt(random(1),0.993),255,0)':cb=128:cr=128,"
        "gblur=sigma=0.6"
    ),
    # Warm amber dust
    "warm_dust": (
        "geq=lum='if(gt(random(2),0.991),210,0)':"
        "cb='if(gt(lum(X,Y),20),100,128)':"
        "cr='if(gt(lum(X,Y),20),165,128)',"
        "gblur=sigma=0.8"
    ),
    # Larger gold confetti blobs
    "gold_confetti": (
        "geq=lum='if(gt(random(3),0.988),240,0)':"
        "cb='if(gt(lum(X,Y),20),85,128)':"
        "cr='if(gt(lum(X,Y),20),175,128)',"
        "dilation,gblur=sigma=1.2"
    ),
    # Soft diffused glow patches
    "soft_glow": (
        "geq=lum='if(gt(random(4),0.985),160,0)':cb=128:cr=128,"
        "gblur=sigma=8"
    ),
    # Dense crisp sparkle
    "sparkle": (
        "geq=lum='if(gt(random(5),0.989),255,0)':cb=128:cr=128,"
        "gblur=sigma=0.4"
    ),
    # Vertical light rays drifting in time
    "light_rays": (
        "geq=lum='if(gt(sin(X/14+T*1.8),0.92),190,0)':"
        "cb=118:cr=140,"
        "gblur=sigma=2.5"
    ),
    # Large soft bokeh circles
    "soft_bokeh": (
        "geq=lum='if(gt(random(6),0.986),200,0)':cb=128:cr=128,"
        "dilation,dilation,gblur=sigma=6"
    ),
    # Very gentle ambient glow
    "gentle_glow": (
        "geq=lum='if(gt(random(7),0.987),120,0)':"
        "cb='if(gt(lum(X,Y),15),115,128)':"
        "cr='if(gt(lum(X,Y),15),145,128)',"
        "gblur=sigma=10"
    ),
    # Pink heart-like tint particles
    "pink_hearts": (
        "geq=lum='if(gt(random(8),0.990),230,0)':"
        "cb='if(gt(lum(X,Y),20),145,128)':"
        "cr='if(gt(lum(X,Y),20),185,128)',"
        "dilation,gblur=sigma=1.0"
    ),
    # Cool silver dust
    "silver_dust": (
        "geq=lum='if(gt(random(9),0.992),245,0)':"
        "cb='if(gt(lum(X,Y),20),140,128)':"
        "cr='if(gt(lum(X,Y),20),115,128)',"
        "gblur=sigma=0.7"
    ),
    # Snow-like falling flakes (time-shifted noise bands)
    "snow_fall": (
        "geq=lum='if(gt(random(1+trunc(Y/40+T*3)),0.991),255,0)':cb=128:cr=128,"
        "gblur=sigma=0.9"
    ),
    # Larger rose petals
    "rose_petals": (
        "geq=lum='if(gt(random(10),0.987),200,0)':"
        "cb='if(gt(lum(X,Y),20),130,128)':"
        "cr='if(gt(lum(X,Y),20),190,128)',"
        "dilation,dilation,gblur=sigma=1.8"
    ),
    # Blue sparkles
    "blue_sparkle": (
        "geq=lum='if(gt(random(11),0.991),240,0)':"
        "cb='if(gt(lum(X,Y),20),170,128)':"
        "cr='if(gt(lum(X,Y),20),105,128)',"
        "gblur=sigma=0.5"
    ),
    # Multicolor rainbow dots
    "rainbow_dots": (
        "geq=lum='if(gt(random(12),0.990),230,0)':"
        "cb='if(gt(lum(X,Y),20),100+mod(X+Y,60),128)':"
        "cr='if(gt(lum(X,Y),20),100+mod(X*3+Y,60),128)',"
        "gblur=sigma=0.8"
    ),
    # Rare bright stars
    "star_twinkle": (
        "geq=lum='if(gt(random(13),0.996),255,0)':cb=128:cr=128,"
        "dilation,gblur=sigma=1.5"
    ),
    # Golden rain streaks
    "golden_rain": (
        "geq=lum='if(gt(sin(Y/10-T*4)+random(14)*0.3,0.85),200,0)':"
        "cb='if(gt(lum(X,Y),20),90,128)':"
        "cr='if(gt(lum(X,Y),20),170,128)',"
        "gblur=sigma=1.2"
    ),
    # Soft bubble glow
    "bubble_glow": (
        "geq=lum='if(gt(random(15),0.984),180,0)':"
        "cb='if(gt(lum(X,Y),15),150,128)':"
        "cr='if(gt(lum(X,Y),15),120,128)',"
        "dilation,dilation,gblur=sigma=7"
    ),
    # Firefly yellow-green
    "firefly": (
        "geq=lum='if(gt(random(16),0.993),255,0)':"
        "cb='if(gt(lum(X,Y),20),95,128)':"
        "cr='if(gt(lum(X,Y),20),145,128)',"
        "gblur=sigma=2.0"
    ),
    # Dense multicolor confetti burst
    "confetti_burst": (
        "geq=lum='if(gt(random(17),0.986),255,0)':"
        "cb='if(gt(lum(X,Y),20),80+mod(X,80),128)':"
        "cr='if(gt(lum(X,Y),20),80+mod(Y,80),128)',"
        "dilation,gblur=sigma=0.7"
    ),
    # Cool moonlight shimmer bands
    "moonlight_shimmer": (
        "geq=lum='if(gt(sin(Y/25+T*1.2)*sin(X/80+T),0.75),140,0)':"
        "cb='if(gt(lum(X,Y),15),145,128)':"
        "cr='if(gt(lum(X,Y),15),110,128)',"
        "gblur=sigma=4"
    ),
}


def choose_preset(slot_role: str, *, rng: random.Random | None = None) -> str:
    role = (slot_role or "").strip().lower() or "daily"
    options = PRESET_GROUPS.get(role) or PRESET_GROUPS["daily"]
    pick = rng.choice(options) if rng is not None else random.choice(options)
    return str(pick)


def resolve_overlay_path(preset_name: str) -> Path | None:
    stem = str(preset_name or "").strip()
    if not stem:
        return None
    for ext in (".webm", ".mov", ".mp4", ".png"):
        path = FX_DIR / f"{stem}{ext}"
        if path.is_file() and path.stat().st_size > 500:
            return path
    return None


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-600:]
        raise RuntimeError(f"ffmpeg failed: {tail}")


def render_motion_fx(
    image_path: Path,
    output_path: Path,
    *,
    duration_s: float = DEFAULT_DURATION_S,
    preset_name: str = "zoom_only",
    width: int = 1080,
    height: int = 1080,
    with_zoom: bool | None = None,
    use_overlay: bool = False,
    max_zoom: float = 1.06,
) -> Path:
    """Build silent MP4: gentle Ken Burns zoom; optional particle overlay."""
    duration_s = max(3.0, min(5.0, float(duration_s)))
    frames = max(int(duration_s * FPS), 1)
    image_path = Path(image_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if with_zoom is None:
        with_zoom = True

    overlay = None
    if use_overlay:
        overlay = resolve_overlay_path(preset_name)
        if overlay is None:
            ensure_overlays([preset_name], force=False)
            overlay = resolve_overlay_path(preset_name)

    base_scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )
    if with_zoom:
        # ~5–6% gentle one-way push-in over the clip (not a harsh zoom)
        cap = max(1.02, min(float(max_zoom), 1.12))
        step = max(0.0002, (cap - 1.0) / max(frames, 1))
        zoom_expr = f"min(zoom+{step:.6f},{cap:.4f})"
        base_vf = (
            f"{base_scale},"
            f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={FPS},format=yuv420p"
        )
    else:
        base_vf = f"{base_scale},fps={FPS},format=yuv420p"

    if overlay is None:
        logger.info(
            "motion_fx: zoom_only with_zoom={} preset={}",
            with_zoom,
            preset_name,
        )
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-vf",
                base_vf,
                "-t",
                f"{duration_s:.2f}",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return output_path

    # Screen blend: black overlay pixels stay invisible
    filter_complex = (
        f"[0:v]{base_vf}[base];"
        f"[1:v]scale={width}:{height},format=gbrp[ov];"
        f"[base]format=gbrp[baseg];"
        f"[baseg][ov]blend=all_mode=screen:all_opacity=0.55,format=yuv420p"
    )
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-stream_loop",
            "-1",
            "-i",
            str(overlay),
            "-filter_complex",
            filter_complex,
            "-t",
            f"{duration_s:.2f}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return output_path


def _generate_one_overlay(stem: str, *, force: bool = False) -> Path | None:
    FX_DIR.mkdir(parents=True, exist_ok=True)
    mp4 = FX_DIR / f"{stem}.mp4"
    if mp4.is_file() and mp4.stat().st_size > 500 and not force:
        return mp4

    filt = _OVERLAY_FILTERS.get(stem)
    if not filt:
        # Fallback unique-ish sparkle seeded by name hash
        seed = abs(hash(stem)) % 90 + 1
        filt = (
            f"geq=lum='if(gt(random({seed}),0.992),255,0)':cb=128:cr=128,"
            f"gblur=sigma=0.8"
        )

    try:
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={_OVERLAY_SIZE}:d={_OVERLAY_DURATION}:r={FPS}",
                "-vf",
                filt,
                "-t",
                _OVERLAY_DURATION,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "ultrafast",
                "-an",
                str(mp4),
            ]
        )
        return mp4
    except Exception as e:
        logger.warning("motion_fx overlay generate failed stem={} err={}", stem, e)
        return None


def ensure_overlays(
    names: Sequence[str] | None = None,
    *,
    force: bool = False,
) -> list[Path]:
    """Generate procedural overlays (black + bright particles) if missing."""
    stems = list(names) if names is not None else list(ALL_OVERLAYS)
    created: list[Path] = []
    for stem in stems:
        path = _generate_one_overlay(str(stem), force=force)
        if path is not None:
            created.append(path)
    return created


# Back-compat alias used by MotionFxBlock / tests
def ensure_placeholder_overlays(names: Sequence[str] | None = None) -> list[Path]:
    return ensure_overlays(names, force=False)
