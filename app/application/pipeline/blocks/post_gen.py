from __future__ import annotations

from typing import Any

from app.application.pipeline.context import PipelineContext


def _escape_md(text: str) -> str:
    for ch in ("\\", "*", "_", "[", "]", "(", ")", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


class PostGenBlock:
    type_id = "post_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        if not config.get("enabled"):
            # Legacy test: if only video was produced, still deliver video to user
            if ctx.target == "user" and ctx.video_token and ctx.target_user_id is not None:
                await ctx.max_client.send_message_to_user(
                    user_id=ctx.target_user_id,
                    text="🎬",
                    attachments=[{"type": "video", "payload": {"token": ctx.video_token}}],
                    fmt="markdown",
                )
            return

        post_text = (ctx.post_text or "").strip() or (config.get("generated_post") or "").strip()
        if not post_text:
            if ctx.target == "user" and ctx.video_token and ctx.target_user_id is not None:
                await ctx.max_client.send_message_to_user(
                    user_id=ctx.target_user_id,
                    text="🎬",
                    attachments=[{"type": "video", "payload": {"token": ctx.video_token}}],
                    fmt="markdown",
                )
            return

        if config.get("add_channel_link") and ctx.channel_link:
            title = ctx.channel_title or "канал"
            if ctx.target == "user":
                post_text += f"\n\n**👉 [Подпишись на {_escape_md(title)}]({ctx.channel_link})**"
            else:
                post_text += f"\n\n**👉 [Подпишись на канал]({ctx.channel_link})**"

        attachments: list[dict[str, Any]] = []
        if ctx.video_token:
            attachments.append({"type": "video", "payload": {"token": ctx.video_token}})
        elif ctx.image_url:
            if ctx.image_url.startswith("http://") or ctx.image_url.startswith("https://"):
                payload: dict[str, Any] = {"url": ctx.image_url}
            else:
                token = await ctx.max_client.upload_file(ctx.image_url, "image")
                payload = {"token": token}
            attachments.append({"type": "image", "payload": payload})

        ctx.post_text = post_text

        if ctx.target == "user":
            if ctx.target_user_id is None:
                return
            await ctx.max_client.send_message_to_user(
                user_id=ctx.target_user_id,
                text=post_text[:3800],
                attachments=attachments if attachments else None,
                fmt="markdown",
            )
            return

        if ctx.channel is None or not ctx.channel.max_chat_id:
            return

        await ctx.max_client.send_message(
            chat_id=ctx.channel.max_chat_id,
            text=post_text[:3800],
            attachments=attachments if attachments else None,
            fmt="markdown",
        )
