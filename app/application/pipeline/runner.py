from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.blocks.registry import BlockRegistry, default_registry
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.generate_post import generate_post_text
from app.application.pipeline.normalize import normalize_blocks_config
from app.application.pipeline.recent_topics import fetch_recent_post_topics


class PipelineRunner:
    def __init__(self, registry: BlockRegistry | None = None) -> None:
        self._registry = registry or default_registry

    async def run(self, ctx: PipelineContext, blocks_config: Any) -> PipelineContext:
        v2 = normalize_blocks_config(blocks_config)

        # If video follows, watermark only the video — avoid double slug on cards.
        video_enabled = any(
            s.get("type") == "video_gen" and s.get("enabled") for s in v2["steps"]
        )
        ctx.meta["skip_image_watermark"] = bool(video_enabled)

        # Pre-seed post text so image_prompt mode=from_post can run before post_gen publishes.
        if not (ctx.post_text or "").strip():
            await self._preseed_post_text(ctx, v2)

        for step in v2["steps"]:
            block_type = step["type"]
            block = self._registry.get(block_type)
            if block is None:
                logger.warning(f"Unknown pipeline block type={block_type}, skipping")
                continue

            # Merge enabled into config so blocks can self-gate (video/post).
            config = {"enabled": step.get("enabled", False), **(step.get("config") or {})}

            # Legacy parity: image_prompt / image_gen run whenever present in steps
            # (old scheduler ignored their enabled flags and used prompt if set).
            # video_gen / post_gen honor enabled inside their execute().
            try:
                await block.execute(ctx, config)
            except Exception:
                logger.exception(
                    f"Block {block_type} failed run_id={ctx.run_id}"
                )
                raise

        return ctx

    async def _preseed_post_text(self, ctx: PipelineContext, v2: dict[str, Any]) -> None:
        for step in v2["steps"]:
            if step.get("type") != "post_gen" or not step.get("enabled"):
                continue
            cfg = step.get("config") or {}
            mode = cfg.get("mode", "ai")

            if mode == "ai":
                brief = (cfg.get("user_input") or "").strip()
                if brief:
                    await ctx.notify("📋 Генерирую текст поста...")
                    chat_id = getattr(ctx.channel, "max_chat_id", None) if ctx.channel else None
                    recent_topics = await fetch_recent_post_topics(ctx.max_client, chat_id)
                    logger.info(
                        f"Pipeline post_gen ai: generating from brief "
                        f"len={len(brief)} recent_topics={len(recent_topics)} "
                        f"run_id={ctx.run_id}"
                    )
                    ctx.post_text = await generate_post_text(
                        ctx.openai_client,
                        brief,
                        ctx.channel_title or "",
                        bold_headings=bool(cfg.get("bold_headings", True)),
                        use_emoji=bool(cfg.get("use_emoji", True)),
                        comments_enabled=bool(cfg.get("comments_enabled", False)),
                        recent_topics=recent_topics,
                    )
                    logger.info(
                        f"Pipeline post_gen ai: generated len={len(ctx.post_text)} "
                        f"run_id={ctx.run_id}"
                    )
                    return
                logger.warning(
                    f"Pipeline post_gen ai: empty user_input, "
                    f"falling back to generated_post run_id={ctx.run_id}"
                )

            seeded = (cfg.get("generated_post") or "").strip()
            if seeded:
                ctx.post_text = seeded
            return
