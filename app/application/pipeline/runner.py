from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.auth.feature_access import audio_allowed, drive_allowed, video_allowed
from app.application.pipeline.blocks.registry import BlockRegistry, default_registry
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.generate_post import TopicDedupExhausted, generate_post_text
from app.application.pipeline.normalize import normalize_blocks_config, resolve_post_brief
from app.application.pipeline.recent_topics import (
    fetch_recent_post_topics,
    topic_from_post_text,
)
from app.application.pipeline.topic_queue import get_topic_queue_from_post_cfg, pop_topic
from app.application.pipeline.upload_cleanup import cleanup_pipeline_uploads


def _style_profile_dict(channel: Any) -> dict[str, Any] | None:
    if channel is None:
        return None
    style = getattr(channel, "style_profile", None)
    if style is None:
        return None
    if isinstance(style, dict):
        return style
    to_dict = getattr(style, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        return data if isinstance(data, dict) else None
    return None


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


def _channel_owner_db_id(ctx: PipelineContext) -> int | None:
    channel = ctx.channel
    if channel is None:
        return None
    raw = getattr(channel, "owner_id", None)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class PipelineRunner:
    def __init__(self, registry: BlockRegistry | None = None) -> None:
        self._registry = registry or default_registry

    async def run(self, ctx: PipelineContext, blocks_config: Any) -> PipelineContext:
        from app.application.admin.billing_context import billing_user, get_billing_user_id

        # Prefer outer binding (e.g. handler already set billing); else channel owner.
        owner_db_id = get_billing_user_id() or _channel_owner_db_id(ctx)
        with billing_user(owner_db_id):
            return await self._run_impl(ctx, blocks_config)

    async def _run_impl(self, ctx: PipelineContext, blocks_config: Any) -> PipelineContext:
        try:
            return await self._run_blocks(ctx, blocks_config)
        finally:
            cleanup_pipeline_uploads(ctx)

    async def _run_blocks(self, ctx: PipelineContext, blocks_config: Any) -> PipelineContext:
        v2 = normalize_blocks_config(blocks_config)
        ctx.meta["pipeline_schedule"] = v2.get("schedule") or {}

        # Publish-time logo watermark flag lives on image_gen config.
        add_watermark = False
        for step in v2["steps"]:
            if step.get("type") == "image_gen":
                add_watermark = bool((step.get("config") or {}).get("add_watermark", False))
                break
        ctx.meta["add_watermark"] = add_watermark

        story_enabled = any(
            s.get("type") == "story_gen" and s.get("enabled") for s in v2["steps"]
        )

        # Shared topic queue lives on post_gen; story_gen consumes it at publish time.
        for step in v2["steps"]:
            if step.get("type") == "post_gen":
                ctx.meta["shared_topic_queue"] = get_topic_queue_from_post_cfg(
                    step.get("config") or {}
                )
                break
        else:
            ctx.meta.setdefault("shared_topic_queue", [])

        # Pre-seed post text so image_prompt mode=from_post/from_topic can run before post_gen publishes.
        # When story_gen is on, it fills caption + story_script in the main loop.
        if not story_enabled and not (ctx.post_text or "").strip():
            try:
                await self._preseed_post_text(ctx, v2)
            except TopicDedupExhausted as e:
                logger.warning(
                    f"Topic dedup exhausted channel={e.channel_title!r} "
                    f"attempts={e.attempts} rejected={e.rejected_topics!r} "
                    f"run_id={ctx.run_id}"
                )
                await self._alert_topic_dedup(ctx, e)
                if isinstance(ctx.meta, dict):
                    ctx.meta["publish_skipped"] = "topic_dedup"
                return ctx

        owner_id = _owner_max_user_id(ctx)

        story_cfg: dict[str, Any] = {}
        tts_cfg: dict[str, Any] = {}
        for step in v2["steps"]:
            if step.get("type") == "story_gen" and step.get("enabled"):
                story_cfg = dict(step.get("config") or {})
            if step.get("type") == "tts_gen" and step.get("enabled"):
                tts_cfg = dict(step.get("config") or {})

        sunor_tale_video = False
        if story_enabled and tts_cfg:
            from app.application.pipeline.tts_voices import TTS_PROVIDER_SUNOR

            fmt = str(story_cfg.get("format") or "fairy_tale").strip() or "fairy_tale"
            provider = str(tts_cfg.get("provider") or "").strip().lower()
            sunor_tale_video = fmt in ("fairy_tale", "bedtime") and provider == TTS_PROVIDER_SUNOR

        for step in v2["steps"]:
            block_type = step["type"]
            if sunor_tale_video and block_type in (
                "image_prompt",
                "image_gen",
                "video_gen",
            ):
                logger.debug(
                    f"Skipping {block_type} — sunor tale video path run_id={ctx.run_id}"
                )
                continue
            if block_type == "video_gen" and not video_allowed(owner_id):
                logger.debug(
                    f"Skipping video_gen — not whitelisted owner={owner_id} "
                    f"run_id={ctx.run_id}"
                )
                continue
            if block_type == "drive_video" and not drive_allowed(owner_id):
                logger.debug(
                    f"Skipping drive_video — not whitelisted owner={owner_id} "
                    f"run_id={ctx.run_id}"
                )
                continue
            if block_type in ("tts_gen", "story_gen", "sunor_gen") and not audio_allowed(owner_id):
                logger.debug(
                    f"Skipping {block_type} — not whitelisted owner={owner_id} "
                    f"run_id={ctx.run_id}"
                )
                continue

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

            # Keep in-memory post_gen queue in sync after story consumes a topic.
            if (
                block_type == "story_gen"
                and isinstance(ctx.meta, dict)
                and ctx.meta.get("topic_queue_popped")
                and ctx.meta.get("topic_queue_block") == "post_gen"
            ):
                remaining = list(ctx.meta.get("topic_queue_remaining") or [])
                ctx.meta["shared_topic_queue"] = remaining
                for s in v2["steps"]:
                    if s.get("type") != "post_gen":
                        continue
                    cfg = dict(s.get("config") or {})
                    cfg["topic_queue"] = remaining
                    s["config"] = cfg
                    break

        return ctx

    async def _alert_topic_dedup(
        self, ctx: PipelineContext, exc: TopicDedupExhausted
    ) -> None:
        owner_id = _owner_max_user_id(ctx)
        if not owner_id or ctx.max_client is None:
            return
        text = (
            f"Не смог найти новую тему для «{exc.channel_title}» "
            f"после {exc.attempts} попыток. Слот пропущен, дубль не опубликован."
        )
        try:
            await ctx.max_client.send_message_to_user(user_id=owner_id, text=text)
        except Exception as e:
            logger.warning(
                f"Topic dedup alert failed owner={owner_id} run_id={ctx.run_id}: {e}"
            )

    async def _alert_topic_queue_exhausted(self, ctx: PipelineContext) -> None:
        owner_id = _owner_max_user_id(ctx)
        if not owner_id or ctx.max_client is None:
            return
        title = (ctx.channel_title or "").strip() or "канал"
        text = (
            f"Темы для «{title}» закончились. "
            f"Последнюю уже использовал, дальше иду по общему брифу."
        )
        try:
            await ctx.max_client.send_message_to_user(user_id=owner_id, text=text)
        except Exception as e:
            logger.warning(
                f"Topic queue exhausted alert failed owner={owner_id} "
                f"run_id={ctx.run_id}: {e}"
            )

    async def _preseed_post_text(self, ctx: PipelineContext, v2: dict[str, Any]) -> None:
        news_item = ctx.meta.get("news_item") if isinstance(ctx.meta, dict) else None
        if isinstance(news_item, dict) and (news_item.get("title") or news_item.get("summary")):
            for step in v2["steps"]:
                if step.get("type") != "post_gen" or not step.get("enabled"):
                    continue
                cfg = step.get("config") or {}
                await ctx.notify("📋 Готовлю пост по новости...")
                chat_id = getattr(ctx.channel, "max_chat_id", None) if ctx.channel else None
                recent_topics = await fetch_recent_post_topics(ctx.max_client, chat_id)
                brief = (cfg.get("user_input") or "").strip()
                style_profile = _style_profile_dict(ctx.channel)
                ctx.post_text, post_topic = await generate_post_text(
                    ctx.openai_client,
                    brief,
                    ctx.channel_title or "",
                    bold_headings=bool(cfg.get("bold_headings", True)),
                    use_emoji=bool(cfg.get("use_emoji", True)),
                    comments_enabled=bool(cfg.get("comments_enabled", False)),
                    recent_topics=recent_topics,
                    news_item=news_item,
                    style_profile=style_profile,
                )
                ctx.meta["post_topic"] = post_topic
                logger.info(
                    f"Pipeline post_gen news: generated len={len(ctx.post_text)} "
                    f"run_id={ctx.run_id}"
                )
                return
            return

        schedule = v2.get("schedule") or {}
        slot_time = None
        if isinstance(ctx.meta, dict):
            raw_slot = ctx.meta.get("slot_time")
            if raw_slot is not None:
                slot_time = str(raw_slot).strip() or None

        for step in v2["steps"]:
            if step.get("type") != "post_gen" or not step.get("enabled"):
                continue
            cfg = step.get("config") or {}
            mode = cfg.get("mode", "ai")

            if mode == "ai":
                brief = resolve_post_brief(schedule, cfg, slot_time)
                if brief:
                    await ctx.notify("📋 Генерирую текст поста...")
                    chat_id = getattr(ctx.channel, "max_chat_id", None) if ctx.channel else None
                    recent_topics = await fetch_recent_post_topics(ctx.max_client, chat_id)

                    # Consume topic queue only on real channel publishes (not Studio tests).
                    queued_topic: str | None = None
                    if getattr(ctx, "target", None) == "channel":
                        queued_topic, remaining = pop_topic(
                            get_topic_queue_from_post_cfg(cfg)
                        )
                        if queued_topic:
                            ctx.meta["topic_queue_popped"] = True
                            ctx.meta["topic_queue_remaining"] = remaining
                            ctx.meta["topic_queue_used"] = queued_topic
                            ctx.meta["topic_queue_block"] = "post_gen"
                            exhausted = len(remaining) == 0
                            ctx.meta["topic_queue_exhausted"] = exhausted
                            # Keep in-memory cfg in sync for this run's later steps.
                            cfg["topic_queue"] = remaining
                            step["config"] = cfg
                            logger.info(
                                f"Pipeline post_gen ai: using queued topic "
                                f"remaining={len(remaining)} run_id={ctx.run_id}"
                            )
                            if exhausted:
                                await self._alert_topic_queue_exhausted(ctx)

                    logger.info(
                        f"Pipeline post_gen ai: generating from brief "
                        f"len={len(brief)} slot_time={slot_time!r} "
                        f"recent_topics={len(recent_topics)} "
                        f"queued_topic={bool(queued_topic)} run_id={ctx.run_id}"
                    )
                    ctx.post_text, post_topic = await generate_post_text(
                        ctx.openai_client,
                        brief,
                        ctx.channel_title or "",
                        bold_headings=bool(cfg.get("bold_headings", True)),
                        use_emoji=bool(cfg.get("use_emoji", True)),
                        comments_enabled=bool(cfg.get("comments_enabled", False)),
                        recent_topics=recent_topics,
                        approved_topic=queued_topic,
                    )
                    ctx.meta["post_topic"] = post_topic
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
                ctx.meta["post_topic"] = topic_from_post_text(seeded)
            return
