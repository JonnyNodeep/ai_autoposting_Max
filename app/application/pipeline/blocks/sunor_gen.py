from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.audio_naming import rename_audio_path
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.normalize import resolve_slot_sunor_preset
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

        meta = ctx.meta if isinstance(ctx.meta, dict) else {}
        schedule = meta.get("pipeline_schedule") if isinstance(meta.get("pipeline_schedule"), dict) else {}
        slot_time = str(meta.get("slot_time") or "").strip() or None
        merged_cfg = resolve_slot_sunor_preset(schedule, slot_time, config)

        topic_title = str(
            meta.get("display_title") or meta.get("post_topic") or merged_cfg.get("title") or ""
        ).strip()
        audio_script = str(meta.get("audio_script") or "").strip()

        if topic_title:
            merged_cfg["title"] = topic_title[:120]
            if str(merged_cfg.get("music_mode") or "") == "instrumental":
                tags = str(merged_cfg.get("tags") or "").strip()
                theme = f"meditation theme: {topic_title}"
                merged_cfg["tags"] = f"{tags}, {theme}" if tags else theme

        await ctx.notify("🎵 Генерирую через Sunor API…")
        try:
            result = await generate_sunor_track(
                merged_cfg,
                story_script=story_script,
                audio_script=audio_script,
                topic_title=topic_title,
                on_progress=ctx.notify,
            )
        except SunorGenerationError as exc:
            logger.error(f"sunor_gen failed run_id={ctx.run_id}: {exc}")
            raise

        final_path = result.path
        if topic_title:
            final_path = rename_audio_path(result.path, topic_title)

        ctx.audio_local_path = final_path
        ctx.audio_token = ""
        if result.image_url:
            ctx.image_url = result.image_url
        if isinstance(ctx.meta, dict):
            ctx.meta["sunor_task_id"] = result.task_id
            ctx.meta["sunor_clip_id"] = result.clip_id
            if topic_title and not (ctx.post_text or "").strip():
                ctx.post_text = topic_title[:500]
        logger.info(
            f"sunor_gen done path={final_path} clip_id={result.clip_id} "
            f"run_id={ctx.run_id}"
        )
