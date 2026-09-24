"""Persist meditation audio and cover under uploads/meditations/."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from app.application.pipeline.audio_naming import safe_filename_from_topic
from app.application.pipeline.context import PipelineContext
from app.infrastructure.services.openai_client import UPLOAD_DIR

MEDITATIONS_SUBDIR = "meditations"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_SLOT_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def is_meditation_pipeline(ctx: PipelineContext) -> bool:
    if not isinstance(ctx.meta, dict):
        return False
    schedule = ctx.meta.get("pipeline_schedule")
    if not isinstance(schedule, dict):
        return False
    return bool(schedule.get("meditation_pipeline"))


def meditations_root() -> Path:
    return UPLOAD_DIR / MEDITATIONS_SUBDIR


def asset_slug(topic: str) -> str:
    name = safe_filename_from_topic(topic or "", suffix="")
    slug = re.sub(r"[\s_]+", "-", name.strip()).strip("-").lower()
    return slug or "audio"


def _channel_id(ctx: PipelineContext) -> str:
    raw = getattr(ctx.channel, "id", None) if ctx.channel is not None else None
    if raw is None:
        return "unknown"
    text = str(raw).strip()
    return text or "unknown"


def _topic(ctx: PipelineContext) -> str:
    meta = ctx.meta if isinstance(ctx.meta, dict) else {}
    for key in ("display_title", "post_topic", "topic_queue_used"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    return (ctx.post_text or "").strip().split("\n", 1)[0].strip()


def _slot_time(ctx: PipelineContext) -> str:
    meta = ctx.meta if isinstance(ctx.meta, dict) else {}
    return str(meta.get("slot_time") or "").strip()


def _slot_stamp(slot_time: str, *, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    date = current.strftime("%Y%m%d")
    match = _SLOT_HHMM_RE.fullmatch(slot_time.strip()) if slot_time else None
    if match:
        hhmm = f"{int(match.group(1)):02d}{match.group(2)}"
        return f"{date}-{hhmm}"
    return current.strftime("%Y%m%d-%H%M")


def _dest_dir(ctx: PipelineContext) -> Path:
    slug = asset_slug(_topic(ctx))
    stamp = _slot_stamp(_slot_time(ctx))
    return meditations_root() / _channel_id(ctx) / f"{stamp}_{slug}"


def _under_meditations(path: Path) -> bool:
    try:
        path.resolve().relative_to(meditations_root().resolve())
        return True
    except (ValueError, OSError):
        return False


def _rel_upload_path(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(UPLOAD_DIR.resolve())
        return str(Path("uploads") / rel).replace("\\", "/")
    except (ValueError, OSError):
        return str(path)


def _cover_name(source: str) -> str:
    suffix = Path(source.split("?", 1)[0]).suffix.lower()
    if suffix not in _IMAGE_EXTS:
        suffix = ".png"
    return f"cover{suffix}"


def _move_file(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() == src.resolve():
        return dest
    if dest.exists():
        dest.unlink()
    src.replace(dest)
    return dest


def _local_file(raw: str) -> Path | None:
    text = (raw or "").strip()
    if not text or text.startswith("http://") or text.startswith("https://"):
        return None
    path = Path(text)
    if path.is_file():
        return path
    return None


async def persist_meditation_assets(ctx: PipelineContext) -> Path | None:
    """Move meditation audio/image into uploads/meditations/. No-op otherwise."""
    if not is_meditation_pipeline(ctx):
        return None

    audio_raw = (ctx.audio_local_path or "").strip()
    image_raw = (ctx.image_url or "").strip()
    local_audio = _local_file(audio_raw)
    local_image = _local_file(image_raw)
    remote_image = image_raw.startswith("http://") or image_raw.startswith("https://")
    if local_audio is None and local_image is None and not remote_image:
        return None

    dest_dir = _dest_dir(ctx)
    dest_dir.mkdir(parents=True, exist_ok=True)

    audio_path: Path | None = None
    if local_audio is not None:
        if _under_meditations(local_audio):
            audio_path = local_audio
        else:
            suffix = local_audio.suffix or ".mp3"
            audio_path = _move_file(
                local_audio,
                dest_dir / f"{asset_slug(_topic(ctx))}{suffix}",
            )
        ctx.audio_local_path = str(audio_path)

    image_path: Path | None = None
    if local_image is not None:
        if _under_meditations(local_image):
            image_path = local_image
        else:
            image_path = _move_file(local_image, dest_dir / _cover_name(str(local_image)))
        ctx.image_url = str(image_path)
    elif remote_image:
        from app.application.pipeline.tale_video import download_url_to_file

        image_path = dest_dir / _cover_name(image_raw)
        await download_url_to_file(image_raw, image_path)
        ctx.image_url = str(image_path)

    meta = {
        "channel_id": getattr(ctx.channel, "id", None) if ctx.channel is not None else None,
        "slot_time": _slot_time(ctx),
        "topic": _topic(ctx),
        "run_id": ctx.run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "audio": _rel_upload_path(audio_path) if audio_path else None,
        "image": _rel_upload_path(image_path) if image_path else None,
    }
    (dest_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Meditation assets persisted dir={} audio={} image={} run_id={}",
        dest_dir,
        meta["audio"],
        meta["image"],
        ctx.run_id,
    )
    return dest_dir
