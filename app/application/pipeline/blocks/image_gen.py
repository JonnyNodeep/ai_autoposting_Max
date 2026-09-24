from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext

NO_TEXT_SUFFIX = (
    "Без текста, букв, логотипов и надписей на изображении."
)

LANDSCAPE_16_9_SUFFIX = (
    "Wide horizontal landscape composition, 16:9 cinematic widescreen framing."
)


def _with_no_text_suffix(prompt: str) -> str:
    base = (prompt or "").rstrip()
    if not base:
        return NO_TEXT_SUFFIX
    if NO_TEXT_SUFFIX.casefold() in base.casefold():
        return base
    return f"{base}\n\n{NO_TEXT_SUFFIX}"


def _with_landscape_suffix(prompt: str) -> str:
    base = (prompt or "").rstrip()
    if not base:
        return LANDSCAPE_16_9_SUFFIX
    lower = base.casefold()
    if "16:9" in lower or "widescreen" in lower or "landscape composition" in lower:
        return base
    return f"{base}\n\n{LANDSCAPE_16_9_SUFFIX}"


class ImageGenBlock:
    """Generate image from ctx.image_prompt, or reuse news source photo."""

    type_id = "image_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        meta = ctx.meta if isinstance(ctx.meta, dict) else {}
        news = meta.get("news_item") if isinstance(meta.get("news_item"), dict) else {}
        source = (meta.get("image_source") or "").strip()

        if source == "news":
            image_url = (news.get("image_url") or "").strip()
            if image_url.startswith("http://") or image_url.startswith("https://"):
                ctx.image_url = image_url
                logger.info(
                    f"Pipeline image_gen: using news image "
                    f"url_preview={image_url[:120]} run_id={ctx.run_id}"
                )
                await self._maybe_preview(ctx, config, prompt="(фото из новости)")
                return
            logger.warning(
                f"Pipeline image_gen: image_source=news but no usable url, "
                f"falling back to AI run_id={ctx.run_id}"
            )

        prompt = ctx.image_prompt
        if not prompt:
            return

        allow_text = bool(config.get("allow_text", True))
        if not allow_text:
            prompt = _with_no_text_suffix(prompt)

        aspect_ratio = str(config.get("aspect_ratio") or "1:1").strip()
        schedule = ctx.meta.get("pipeline_schedule") if isinstance(ctx.meta, dict) else {}
        if (
            isinstance(schedule, dict)
            and schedule.get("meditation_pipeline")
            and aspect_ratio == "1:1"
        ):
            aspect_ratio = "16:9"
        if aspect_ratio == "16:9":
            prompt = _with_landscape_suffix(prompt)

        from app.application.pipeline.image_sizes import resolve_image_size

        image_size = resolve_image_size(
            aspect_ratio,
            explicit_size=str(config.get("size") or ""),
        )

        await ctx.notify("🧪 Генерирую изображение...")
        logger.info(
            f"Pipeline image_gen: prompt_len={len(prompt)} "
            f"allow_text={allow_text} aspect_ratio={aspect_ratio} size={image_size} "
            f"run_id={ctx.run_id}"
        )
        image_url = await ctx.openai_client.generate_image(prompt=prompt, size=image_size)
        ctx.image_url = image_url or ""
        logger.info(
            f"Pipeline image_gen: url_preview={(ctx.image_url[:120] if ctx.image_url else 'empty')}"
        )
        await self._maybe_preview(ctx, config, prompt=prompt)

    async def _maybe_preview(
        self, ctx: PipelineContext, config: dict[str, Any], *, prompt: str
    ) -> None:
        if ctx.target != "user" or not ctx.image_url or ctx.target_user_id is None:
            return

        attachments: list[dict[str, Any]] = []
        if ctx.image_url.startswith("http://") or ctx.image_url.startswith("https://"):
            payload = {"url": ctx.image_url}
        else:
            token = await ctx.max_client.upload_file(ctx.image_url, "image")
            payload = {"token": token}
        attachments.append({"type": "image", "payload": payload})

        model = config.get("model", "")
        model_label = ctx.meta.get("image_model_name") or model
        text = (
            f"🧪 *Тест — {ctx.channel_title}*\n\n"
            f"Модель: {model_label}\n"
            f"Промпт:\n`{prompt[:300]}`"
        )
        extra = ctx.meta.get("preview_keyboard")
        if extra:
            attachments.append(extra)

        await ctx.max_client.send_message_to_user(
            user_id=ctx.target_user_id,
            text=text,
            attachments=attachments,
            fmt="markdown",
        )
