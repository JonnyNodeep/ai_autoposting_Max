"""Split long TTS scripts into API-sized chunks and concat MP3 parts."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

TTS_MAX_CHARS = 4096


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


def concat_mp3_files(parts: list[Path], output: Path) -> None:
    """Concatenate MP3 files with ffmpeg concat demuxer (stream copy)."""
    if not parts:
        raise ValueError("No MP3 parts to concatenate")
    if len(parts) == 1:
        output.write_bytes(parts[0].read_bytes())
        return

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as listing:
        list_path = Path(listing.name)
        for part in parts:
            escaped = str(part.resolve()).replace("'", "'\\''")
            listing.write(f"file '{escaped}'\n")

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                str(output),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat failed: {(result.stderr or '')[:400]}"
            )
    finally:
        list_path.unlink(missing_ok=True)
