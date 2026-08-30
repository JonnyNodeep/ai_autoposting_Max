from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from app.application.auth.feature_access import drive_allowed
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.drive_monitor import normalize_drive_video
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.drive_published_repository import (
    SQLADrivePublishedRepository,
)
from app.infrastructure.services.google_drive_client import download_file, list_videos
from app.infrastructure.services.openai_client import UPLOAD_DIR


def _owner_max_user_id(ctx: PipelineContext) -> int | None:
    if not isinstance(ctx.meta, dict):
        return None
    raw = ctx.meta.get("owner_max_user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class DriveVideoBlock:
    type_id = "drive_video"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        if not config.get("enabled"):
            return

        cfg = normalize_drive_video(config)
        folder_id = str(cfg.get("folder_id") or "").strip()
        if not folder_id:
            return

        owner_id = _owner_max_user_id(ctx)
        if not drive_allowed(owner_id):
            logger.debug(
                f"Skipping drive_video — not whitelisted owner={owner_id} "
                f"run_id={ctx.run_id}"
            )
            return

        channel_id = getattr(ctx.channel, "id", None) if ctx.channel else None
        if channel_id is None:
            logger.warning(f"drive_video skipped — no channel run_id={ctx.run_id}")
            return

        try:
            all_videos = await list_videos(folder_id)
        except Exception as exc:
            logger.exception(f"drive_video list failed run_id={ctx.run_id}: {exc}")
            if owner_id and ctx.max_client is not None:
                try:
                    await ctx.max_client.send_message_to_user(
                        user_id=owner_id,
                        text=f"Не удалось прочитать папку Google Drive: {exc}",
                    )
                except Exception:
                    pass
            raise

        async with async_session_factory() as session:
            drive_repo = SQLADrivePublishedRepository(session)
            published_ids = await drive_repo.get_published_file_ids(int(channel_id))

        unpublished = [v for v in all_videos if v.file_id not in published_ids]
        remaining = len(unpublished)
        threshold = int(cfg.get("low_stock_threshold") or 5)
        notified_at = cfg.get("low_stock_notified_at_remaining")

        if (
            remaining <= threshold
            and remaining > 0
            and notified_at != remaining
            and owner_id
            and ctx.max_client is not None
        ):
            title = (ctx.channel_title or "").strip() or "канал"
            try:
                await ctx.max_client.send_message_to_user(
                    user_id=owner_id,
                    text=(
                        f"В папке Google Drive для канала «{title}» "
                        f"осталось {remaining} видео. Загрузите новые."
                    ),
                )
            except Exception as exc:
                logger.warning(
                    f"drive low-stock DM failed owner={owner_id} run_id={ctx.run_id}: {exc}"
                )
            if isinstance(ctx.meta, dict):
                ctx.meta["drive_low_stock_notify"] = remaining

        if remaining == 0:
            if isinstance(ctx.meta, dict):
                ctx.meta["publish_skipped"] = "drive_empty"
            if owner_id and ctx.max_client is not None:
                title = (ctx.channel_title or "").strip() or "канал"
                try:
                    await ctx.max_client.send_message_to_user(
                        user_id=owner_id,
                        text=(
                            f"Нет видео для публикации в канал «{title}». "
                            f"Добавьте файлы в папку Google Drive."
                        ),
                    )
                except Exception:
                    pass
            return

        if notified_at is not None and remaining > threshold:
            if isinstance(ctx.meta, dict):
                ctx.meta["drive_low_stock_reset"] = True

        video = unpublished[0]
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(video.name).suffix or ".mp4"
        local_path = UPLOAD_DIR / f"drive_{uuid.uuid4().hex[:12]}{suffix}"
        await download_file(video.file_id, local_path)

        ctx.video_local_path = str(local_path)
        ctx.post_text = str(cfg.get("fixed_caption") or "").strip()
        if isinstance(ctx.meta, dict):
            ctx.meta["drive_file_id"] = video.file_id
            ctx.meta["drive_file_name"] = video.name
