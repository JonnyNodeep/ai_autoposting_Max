"""Build fairy-tale slideshow MP4 from scene images + narration audio."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from loguru import logger

MIN_SCENE_DURATION_S = 2.5
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 30

_NORMALIZE_VF = (
    f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
    f"force_original_aspect_ratio=increase,"
    f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
    "setsar=1"
)


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        tail = err[-800:] if len(err) > 800 else err
        raise RuntimeError(f"ffmpeg failed: {tail}")


def probe_audio_duration_s(audio_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {(result.stderr or '')[:300]}")
    try:
        return max(0.1, float((result.stdout or "").strip()))
    except ValueError as exc:
        raise RuntimeError(f"ffprobe bad duration: {result.stdout!r}") from exc


def scene_durations_s(
    span_lengths: list[int],
    total_audio_s: float,
    *,
    min_scene_s: float = MIN_SCENE_DURATION_S,
) -> list[float]:
    if not span_lengths:
        raise ValueError("span_lengths empty")
    total_chars = sum(max(1, n) for n in span_lengths)
    raw = [total_audio_s * (max(1, n) / total_chars) for n in span_lengths]
    capped = [max(min_scene_s, d) for d in raw]
    capped_sum = sum(capped)
    if capped_sum <= 0:
        equal = total_audio_s / len(span_lengths)
        return [equal] * len(span_lengths)
    scale = total_audio_s / capped_sum
    return [d * scale for d in capped]


def build_slideshow_mp4(
    *,
    image_paths: list[Path],
    audio_path: Path,
    span_lengths: list[int],
    output_path: Path | None = None,
) -> Path:
    if not image_paths:
        raise ValueError("image_paths empty")
    if len(image_paths) != len(span_lengths):
        raise ValueError("image_paths and span_lengths length mismatch")
    if not audio_path.is_file():
        raise FileNotFoundError(str(audio_path))

    duration = probe_audio_duration_s(audio_path)
    durations = scene_durations_s(span_lengths, duration)
    out = output_path or (audio_path.parent / f"{audio_path.stem}_tale.mp4")

    with tempfile.TemporaryDirectory(prefix="tale_slides_") as tmp:
        tmp_dir = Path(tmp)
        norm_paths: list[Path] = []
        for i, img in enumerate(image_paths):
            norm = tmp_dir / f"slide_{i:02d}.jpg"
            _run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(img),
                    "-vf",
                    _NORMALIZE_VF,
                    "-q:v",
                    "2",
                    str(norm),
                ]
            )
            norm_paths.append(norm)

        concat_list = tmp_dir / "concat.txt"
        lines: list[str] = []
        for path, dur in zip(norm_paths, durations):
            lines.append(f"file '{path}'")
            lines.append(f"duration {dur:.3f}")
        lines.append(f"file '{norm_paths[-1]}'")
        concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

        silent_video = tmp_dir / "slides.mp4"
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(VIDEO_FPS),
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                str(silent_video),
            ]
        )

        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(silent_video),
                "-i",
                str(audio_path),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(out),
            ]
        )

    logger.info(
        "Tale slideshow ready path={} duration_s={:.1f} scenes={}",
        out,
        duration,
        len(image_paths),
    )
    return out
