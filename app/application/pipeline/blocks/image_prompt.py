from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext
from app.application.pipeline.normalize import mix_slot_image_addon, resolve_slot_image_addon
from app.application.pipeline.recent_topics import (
    fetch_recent_post_topics,
    topic_from_post_text,
)

DEFAULT_FROM_POST_INSTRUCTION = "Сгенерируй картинку для этого поста"

DEFAULT_FROM_TOPIC_INSTRUCTION = "Сгенерируй картинку по этой теме"

DEFAULT_FROM_NEWS_INSTRUCTION = (
    "Сгенерируй фотореалистичную иллюстрацию к этой новости. "
    "Без текста, логотипов СМИ, водярок и коллажей. "
    "Реалистичная сцена по фактам новости."
)


def _visual_style_from_ctx(ctx: PipelineContext) -> str:
    channel = ctx.channel
    if channel is None:
        return ""
    style = getattr(channel, "style_profile", None)
    if style is None:
        return ""
    if isinstance(style, dict):
        return (style.get("visual_style") or "").strip()
    return (getattr(style, "visual_style", None) or "").strip()


def _append_visual_style(prompt: str, visual_style: str) -> str:
    if not visual_style:
        return prompt
    return (
        f"{prompt.rstrip()}\n\n"
        f"Визуальный стиль канала (обязательно соблюдай): {visual_style}"
    )


def _should_use_visual_style(config: dict[str, Any]) -> bool:
    """Default: on for from_post/from_topic/from_news, off for fixed/ai (protects card prompts)."""
    if "use_visual_style" in config:
        return bool(config["use_visual_style"])
    return config.get("mode", "ai") in ("from_post", "from_topic", "from_news")


def _resolve_post_topic(ctx: PipelineContext) -> str:
    meta = ctx.meta if isinstance(ctx.meta, dict) else {}
    topic = str(meta.get("post_topic") or "").strip()
    if topic:
        return topic
    return topic_from_post_text(ctx.post_text or "")


def _append_recent_avoid(prompt: str, topics: list[str]) -> str:
    if not topics:
        return prompt
    listed = "\n".join(f"- {t}" for t in topics[:25])
    return (
        f"{prompt.rstrip()}\n\n"
        f"Недавно в канале уже были такие мотивы/сцены:\n{listed}\n"
        f"НЕ повторяй их (ни то же животное, ни ту же локацию/композицию). "
        f"Выбери другую категорию и свежую сцену."
    )


def _news_item_from_ctx(ctx: PipelineContext) -> dict[str, Any] | None:
    meta = ctx.meta if isinstance(ctx.meta, dict) else {}
    news = meta.get("news_item")
    return news if isinstance(news, dict) else None


def _slot_image_addon_from_ctx(ctx: PipelineContext) -> str:
    meta = ctx.meta if isinstance(ctx.meta, dict) else {}
    schedule = meta.get("pipeline_schedule")
    if not isinstance(schedule, dict):
        schedule = {}
    raw_slot = meta.get("slot_time")
    slot_time = str(raw_slot).strip() if raw_slot is not None else ""
    return resolve_slot_image_addon(schedule, slot_time or None)


class ImagePromptBlock:
    """Loads image prompt into context. Does not call image APIs."""

    type_id = "image_prompt"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        mode = config.get("mode", "ai")
        use_vs = _should_use_visual_style(config)
        visual_style = _visual_style_from_ctx(ctx) if use_vs else ""

        if mode == "from_news":
            if not config.get("enabled"):
                return
            news = _news_item_from_ctx(ctx)
            if not news:
                logger.warning(
                    f"Pipeline image_prompt from_news: no news_item, "
                    f"skipping run_id={ctx.run_id}"
                )
                return
            image_url = (news.get("image_url") or "").strip()
            if image_url:
                ctx.meta["image_source"] = "news"
                ctx.image_prompt = ""
                logger.info(
                    f"Pipeline image_prompt from_news: using source image "
                    f"run_id={ctx.run_id}"
                )
                return

            title = (news.get("title") or "").strip()
            summary = (news.get("summary") or "").strip()[:800]
            instruction = (
                config.get("instruction") or DEFAULT_FROM_NEWS_INSTRUCTION
            ).strip()
            parts = [instruction]
            if title:
                parts.append(f"Заголовок: {title}")
            if summary:
                parts.append(f"Суть: {summary}")
            prompt = "\n\n".join(parts)
            ctx.meta["image_source"] = "ai"
            ctx.image_prompt = _append_visual_style(prompt, visual_style)
            logger.info(
                f"Pipeline image_prompt from_news: AI fallback "
                f"prompt_len={len(ctx.image_prompt)} run_id={ctx.run_id}"
            )
            return

        if mode == "from_topic":
            if not config.get("enabled"):
                return
            topic = _resolve_post_topic(ctx)
            if not topic:
                logger.warning(
                    f"Pipeline image_prompt from_topic: empty topic, "
                    f"skipping run_id={ctx.run_id}"
                )
                return
            instruction = (
                config.get("instruction") or DEFAULT_FROM_TOPIC_INSTRUCTION
            ).strip()
            prompt = f"{instruction}\n\n{topic}" if instruction else topic
            prompt = mix_slot_image_addon(prompt, _slot_image_addon_from_ctx(ctx))
            ctx.meta["image_source"] = "ai"
            ctx.image_prompt = _append_visual_style(prompt, visual_style)
            logger.info(
                f"Pipeline image_prompt from_topic: prompt_len={len(ctx.image_prompt)} "
                f"visual_style={'yes' if visual_style else 'no'} run_id={ctx.run_id}"
            )
            return

        if mode == "from_post":
            if not config.get("enabled"):
                return
            post = (ctx.post_text or "").strip()
            if not post:
                logger.warning(
                    f"Pipeline image_prompt from_post: empty post_text, "
                    f"skipping run_id={ctx.run_id}"
                )
                return
            instruction = (
                config.get("instruction") or DEFAULT_FROM_POST_INSTRUCTION
            ).strip()
            prompt = f"{instruction}\n\n{post}" if instruction else post
            prompt = mix_slot_image_addon(prompt, _slot_image_addon_from_ctx(ctx))
            ctx.meta["image_source"] = "ai"
            ctx.image_prompt = _append_visual_style(prompt, visual_style)
            logger.info(
                f"Pipeline image_prompt from_post: prompt_len={len(ctx.image_prompt)} "
                f"visual_style={'yes' if visual_style else 'no'} run_id={ctx.run_id}"
            )
            return

        prompt = (config.get("generated_prompt") or "").strip()
        if not prompt:
            return

        prompt = _append_visual_style(prompt, visual_style)

        # Rotate motifs for fixed/AI card-style prompts using channel history.
        chat_id = getattr(ctx.channel, "max_chat_id", None) if ctx.channel else None
        recent = await fetch_recent_post_topics(ctx.max_client, chat_id)
        prompt = _append_recent_avoid(prompt, recent)

        ctx.meta["image_source"] = "ai"
        ctx.image_prompt = prompt
        logger.info(
            f"Pipeline image_prompt: mode={mode} visual_style={'yes' if visual_style else 'no'} "
            f"recent_topics={len(recent)} prompt_len={len(prompt)} run_id={ctx.run_id}"
        )
