from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext
from app.application.pipeline.recent_topics import fetch_recent_post_topics

DEFAULT_FROM_POST_INSTRUCTION = "Сгенерируй картинку для этого поста"


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
    """Default: on for from_post, off for fixed/ai (protects card prompts)."""
    if "use_visual_style" in config:
        return bool(config["use_visual_style"])
    return config.get("mode", "ai") == "from_post"


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


class ImagePromptBlock:
    """Loads image prompt into context. Does not call image APIs."""

    type_id = "image_prompt"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        mode = config.get("mode", "ai")
        use_vs = _should_use_visual_style(config)
        visual_style = _visual_style_from_ctx(ctx) if use_vs else ""

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

        ctx.image_prompt = prompt
        logger.info(
            f"Pipeline image_prompt: mode={mode} visual_style={'yes' if visual_style else 'no'} "
            f"recent_topics={len(recent)} prompt_len={len(prompt)} run_id={ctx.run_id}"
        )
