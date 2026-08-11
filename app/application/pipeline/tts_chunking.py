"""Split long TTS scripts into API-sized chunks and concat audio parts."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

TTS_MAX_CHARS = 4096
TTS_MAX_CHARS_MINI = 1600
TTS_MP3_BITRATE = "320k"
TTS_CROSSFADE_MS = 50
TTS_LOUDNORM = "loudnorm=I=-16:LRA=11:TP=-1.5"


def max_chars_for_model(model: str | None) -> int:
    """Return safe input char limit for the given TTS model."""
    name = (model or "").strip().lower()
    if name in ("speechkit", "yandex", "yandex-speechkit"):
        from app.application.pipeline.tts_voices import SPEECHKIT_MAX_CHARS

        return SPEECHKIT_MAX_CHARS
    if name.startswith("gpt-4o-mini-tts") or "mini-tts" in name:
        return TTS_MAX_CHARS_MINI
    return TTS_MAX_CHARS


def _audio_filter_chain(*filters: str) -> str:
    """Join non-empty ffmpeg audio filters with commas."""
    return ",".join(f for f in filters if f)


def _mp3_encode_args(*, af: str | None = None) -> list[str]:
    """Common lame 320k encode args, optionally with -af chain."""
    args: list[str] = []
    filt = af or TTS_LOUDNORM
    if filt:
        args.extend(["-af", filt])
    args.extend(["-codec:a", "libmp3lame", "-b:a", TTS_MP3_BITRATE])
    return args


def chunk_tts_text(text: str, max_chars: int = TTS_MAX_CHARS) -> list[str]:
    """Split text into chunks <= max_chars, preferring paragraphs then sentences."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    if not paragraphs:
        paragraphs = [cleaned]

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for para in paragraphs:
        if len(para) <= max_chars:
            candidate = f"{current}\n\n{para}".strip() if current else para
            if len(candidate) <= max_chars:
                current = candidate
            else:
                flush()
                current = para
            continue

        # Oversized paragraph: split by sentences.
        flush()
        sentences = re.split(r"(?<=[.!?…])\s+", para)
        buf = ""
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(sent) > max_chars:
                if buf:
                    chunks.append(buf.strip())
                    buf = ""
                for i in range(0, len(sent), max_chars):
                    chunks.append(sent[i : i + max_chars])
                continue
            candidate = f"{buf} {sent}".strip() if buf else sent
            if len(candidate) <= max_chars:
                buf = candidate
            else:
                if buf:
                    chunks.append(buf.strip())
                buf = sent
        if buf:
            chunks.append(buf.strip())

    flush()
    return chunks


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {(result.stderr or '')[:400]}")


def _encode_mp3(input_path: Path, output: Path) -> None:
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            *_mp3_encode_args(af=TTS_LOUDNORM),
            str(output),
        ]
    )


def _acrossfade_filter(n_inputs: int, fade_sec: float) -> str:
    """Build filter_complex chaining acrossfade for n_inputs (>=2), then loudnorm."""
    if n_inputs < 2:
        raise ValueError("acrossfade needs at least 2 inputs")
    d = f"{fade_sec:.3f}".rstrip("0").rstrip(".")
    if n_inputs == 2:
        fade = f"[0:a][1:a]acrossfade=d={d}:c1=tri:c2=tri[joined]"
    else:
        parts: list[str] = []
        parts.append(f"[0:a][1:a]acrossfade=d={d}:c1=tri:c2=tri[a0]")
        for i in range(2, n_inputs):
            prev = f"a{i - 2}"
            out = f"a{i - 1}"
            if i == n_inputs - 1:
                parts.append(f"[{prev}][{i}:a]acrossfade=d={d}:c1=tri:c2=tri[joined]")
            else:
                parts.append(f"[{prev}][{i}:a]acrossfade=d={d}:c1=tri:c2=tri[{out}]")
        fade = ";".join(parts)
    return f"{fade};[joined]{TTS_LOUDNORM}"


def concat_audio_to_mp3(
    parts: list[Path],
    output: Path,
    *,
    fade_ms: int = TTS_CROSSFADE_MS,
) -> None:
    """Concatenate WAV/audio parts with acrossfade, loudnorm, and MP3 320k."""
    if not parts:
        raise ValueError("No audio parts to concatenate")
    output.parent.mkdir(parents=True, exist_ok=True)
    fade_ms = max(0, min(int(fade_ms), 500))
    fade_sec = fade_ms / 1000.0

    if len(parts) == 1:
        _encode_mp3(parts[0], output)
        return

    if fade_ms <= 0:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as listing:
            list_path = Path(listing.name)
            for part in parts:
                escaped = str(part.resolve()).replace("'", "'\\''")
                listing.write(f"file '{escaped}'\n")
        try:
            _run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    *_mp3_encode_args(af=TTS_LOUDNORM),
                    str(output),
                ]
            )
        finally:
            list_path.unlink(missing_ok=True)
        return

    cmd: list[str] = ["ffmpeg", "-y"]
    for part in parts:
        cmd.extend(["-i", str(part)])
    cmd.extend(
        [
            "-filter_complex",
            _acrossfade_filter(len(parts), fade_sec),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            TTS_MP3_BITRATE,
            str(output),
        ]
    )
    _run_ffmpeg(cmd)


def concat_mp3_files(parts: list[Path], output: Path) -> None:
    """Backward-compatible alias — re-encodes to MP3 320k with acrossfade."""
    concat_audio_to_mp3(parts, output)
