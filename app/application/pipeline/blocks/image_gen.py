from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext


class ImageGenBlock:
    """Generate image from ctx.image_prompt (legacy: runs whenever prompt is set)."""

    type_id = "image_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        prompt = ctx.image_prompt
        if not prompt:
            return

        await ctx.notify("🧪 Генерирую изображение...")
        logger.info(
            f"Pipeline image_gen: prompt_len={len(prompt)} run_id={ctx.run_id}"
        )
        channel_link = None
        if not ctx.meta.get("skip_image_watermark"):
            channel_link = ctx.channel_link or None
        image_url = await ctx.openai_client.generate_image(
            prompt=prompt,
            channel_link=channel_link,
        )
        ctx.image_url = image_url or ""
        logger.info(
            f"Pipeline image_gen: url_preview={(ctx.image_url[:120] if ctx.image_url else 'empty')}"
        )

        if ctx.target != "user" or not ctx.image_url or ctx.target_user_id is None:
            return

        # Test mode: preview image to the owner
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
