from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext
from app.application.pipeline.sunor_service import (
    SunorGenerationError,
    generate_sunor_track,
)


class SunorGenBlock:
    type_id = "sunor_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        if not config.get("enabled"):
            return

        story_script = (ctx.story_script or "").strip()
        prompt_source = str(config.get("prompt_source") or "config").strip().lower()
        if prompt_source == "story_gen" and not story_script:
            logger.warning(f"sunor_gen skipped: empty story_script run_id={ctx.run_id}")
            return

        await ctx.notify("🎵 Генерирую через Sunor API…")
        try:
            result = await generate_sunor_track(
                config,
                story_script=story_script,
                on_progress=ctx.notify,
            )
        except SunorGenerationError as exc:
            logger.error(f"sunor_gen failed run_id={ctx.run_id}: {exc}")
            raise

        ctx.audio_local_path = result.path
        ctx.audio_token = ""
        if result.image_url:
            ctx.image_url = result.image_url
        if isinstance(ctx.meta, dict):
            ctx.meta["sunor_task_id"] = result.task_id
            ctx.meta["sunor_clip_id"] = result.clip_id
            if result.title and not (ctx.post_text or "").strip():
                ctx.post_text = result.title[:500]
        logger.info(
            f"sunor_gen done path={result.path} clip_id={result.clip_id} "
            f"run_id={ctx.run_id}"
        )
