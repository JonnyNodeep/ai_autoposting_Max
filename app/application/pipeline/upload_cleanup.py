"""Remove ephemeral files from uploads/ after pipeline use."""

from __future__ import annotations

from pathlib import Path

from app.application.pipeline.context import PipelineContext
from app.infrastructure.services.openai_client import UPLOAD_DIR


_PERSISTENT_SUBDIRS = ("logos", "meditations")


def is_ephemeral_upload(path: str) -> bool:
    """True for local files under UPLOAD_DIR, excluding persistent subdirs."""
    raw = (path or "").strip()
    if not raw or raw.startswith("http://") or raw.startswith("https://"):
        return False
    try:
        resolved = Path(raw).resolve()
        root = UPLOAD_DIR.resolve()
        resolved.relative_to(root)
    except (ValueError, OSError):
        return False
    for name in _PERSISTENT_SUBDIRS:
        persistent = (root / name).resolve()
        try:
            resolved.relative_to(persistent)
            return False
        except ValueError:
            continue
    return True


def safe_unlink_upload(path: str) -> None:
    if not is_ephemeral_upload(path):
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_pipeline_uploads(ctx: PipelineContext) -> None:
    for raw in (ctx.image_url, ctx.video_local_path, ctx.audio_local_path):
        safe_unlink_upload(raw)
