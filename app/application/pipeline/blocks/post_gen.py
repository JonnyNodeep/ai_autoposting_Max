from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext


def _escape_md(text: str) -> str:
    for ch in ("\\", "*", "_", "[", "]", "(", ")", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


def build_subscribe_cta(
    link: str,
    *,
    title: str = "канал",
    personalized: bool = False,
) -> str:
    if personalized:
        return f"\n\n**👉 [Подпишись на {_escape_md(title)}]({link})**"
    return f"\n\n**👉 [Подпишись на канал]({link})**"


def text_with_telegram_cta(
    max_post_text: str,
    *,
    body_without_cta: str,
    add_channel_link: bool,
    max_link: str,
    telegram_link: str | None,
    channel_title: str,
    personalized: bool = False,
) -> str:
    """If TG has its own public link, rebuild CTA; otherwise keep Max post text."""
    tg_link = (telegram_link or "").strip()
    if not add_channel_link or not tg_link:
        return max_post_text
    max_link_norm = (max_link or "").strip()
    if max_link_norm and max_link_norm == tg_link:
        return max_post_text
    return body_without_cta + build_subscribe_cta(
        tg_link,
        title=channel_title or "канал",
        personalized=personalized,
    )


async def _build_image_attachment(ctx: PipelineContext) -> dict[str, Any] | None:
    if not (ctx.image_url or "").strip():
        return None
    if ctx.image_url.startswith("http://") or ctx.image_url.startswith("https://"):
        payload: dict[str, Any] = {"url": ctx.image_url}
    else:
        token = await ctx.max_client.upload_file(ctx.image_url, "image")
        payload = {"token": token}
    return {"type": "image", "payload": payload}


async def _build_audio_attachment(ctx: PipelineContext) -> dict[str, Any] | None:
    if not (ctx.audio_token or (ctx.audio_local_path or "").strip()):
        return None
    token = (ctx.audio_token or "").strip()
    if not token:
        token = await ctx.max_client.upload_file(ctx.audio_local_path, "audio")
        ctx.audio_token = token
    return {"type": "audio", "payload": {"token": token}}


async def _mirror_to_telegram(
    ctx: PipelineContext,
    post_text: str,
    *,
    prefer_audio_with_image: bool = False,
) -> None:
    """Best-effort dual-publish. Never raises — Max post already succeeded."""
    if ctx.target != "channel":
        return
    channel = ctx.channel
    tg_chat_id = getattr(channel, "telegram_chat_id", None) if channel is not None else None
    if not tg_chat_id:
        return
    tg = ctx.telegram_client
    if tg is None or not getattr(tg, "configured", True):
        return
    try:
        video_path = (ctx.video_local_path or "").strip() or None
        audio_path = (ctx.audio_local_path or "").strip() or None
        image_url = (ctx.image_url or "").strip() or None

        if prefer_audio_with_image and image_url and audio_path and not video_path:
            # Mirror as two messages: photo+caption, then audio.
            await tg.publish_post(
                tg_chat_id,
                post_text[:4096],
                image_url=image_url,
            )
            await tg.publish_post(
                tg_chat_id,
                "🎙",
                audio_path=audio_path,
            )
            logger.info(
                f"Telegram mirror ok (image+audio) channel_id={getattr(channel, 'id', None)} "
                f"tg_chat={tg_chat_id}"
            )
            return

        if video_path:
            image_url = None
        elif audio_path:
            image_url = None
        await tg.publish_post(
            tg_chat_id,
            post_text[:4096],
            image_url=image_url,
            video_path=video_path,
            audio_path=audio_path,
        )
        logger.info(
            f"Telegram mirror ok channel_id={getattr(channel, 'id', None)} "
            f"tg_chat={tg_chat_id}"
        )
    except Exception:
        logger.exception(
            f"Telegram mirror failed channel_id={getattr(channel, 'id', None)} "
            f"tg_chat={tg_chat_id} — Max post kept"
        )


def _cleanup_path(path: str, label: str) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        logger.warning(f"Failed to cleanup {label}={path}")


def _cleanup_video_local(ctx: PipelineContext) -> None:
    path = (ctx.video_local_path or "").strip()
    _cleanup_path(path, "video_local_path")
    ctx.video_local_path = ""


def _cleanup_audio_local(ctx: PipelineContext) -> None:
    path = (ctx.audio_local_path or "").strip()
    _cleanup_path(path, "audio_local_path")
    ctx.audio_local_path = ""


def _cleanup_image_local(ctx: PipelineContext) -> None:
    """Remove generated local image after MAX/TG no longer need the file."""
    path = (ctx.image_url or "").strip()
    if not path or path.startswith("http://") or path.startswith("https://"):
        return
    _cleanup_path(path, "image_url")
    ctx.image_url = ""


def _cleanup_local_media(ctx: PipelineContext) -> None:
    _cleanup_video_local(ctx)
    _cleanup_audio_local(ctx)
    _cleanup_image_local(ctx)


async def _send_max(
    ctx: PipelineContext,
    text: str,
    attachments: list[dict[str, Any]] | None,
) -> None:
    if ctx.target == "user":
        if ctx.target_user_id is None:
            return
        await ctx.max_client.send_message_to_user(
            user_id=ctx.target_user_id,
            text=text[:3800],
            attachments=attachments if attachments else None,
            fmt="markdown",
        )
        return

    if ctx.channel is None or not ctx.channel.max_chat_id:
        return
    await ctx.max_client.send_message(
        chat_id=ctx.channel.max_chat_id,
        text=text[:3800],
        attachments=attachments if attachments else None,
        fmt="markdown",
    )


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
            _cleanup_local_media(ctx)
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
            _cleanup_local_media(ctx)
            return

        body_without_cta = post_text
        add_link = bool(config.get("add_channel_link") and ctx.channel_link)
        if add_link:
            post_text = body_without_cta + build_subscribe_cta(
                ctx.channel_link,
                title=ctx.channel_title or "канал",
                personalized=(ctx.target == "user"),
            )

        ctx.post_text = post_text
        has_video = bool(ctx.video_token)
        has_audio = bool(ctx.audio_token or (ctx.audio_local_path or "").strip())
        has_image = bool((ctx.image_url or "").strip())

        try:
            if has_video:
                attachments = [{"type": "video", "payload": {"token": ctx.video_token}}]
                await _send_max(ctx, post_text, attachments)
            elif has_audio and has_image:
                # MAX docs don't list audio+image combo — send two messages.
                image_att = await _build_image_attachment(ctx)
                await _send_max(
                    ctx,
                    post_text,
                    [image_att] if image_att else None,
                )
                audio_att = await _build_audio_attachment(ctx)
                await _send_max(
                    ctx,
                    "🎙",
                    [audio_att] if audio_att else None,
                )
            elif has_audio:
                audio_att = await _build_audio_attachment(ctx)
                await _send_max(
                    ctx,
                    post_text,
                    [audio_att] if audio_att else None,
                )
            elif has_image:
                image_att = await _build_image_attachment(ctx)
                await _send_max(
                    ctx,
                    post_text,
                    [image_att] if image_att else None,
                )
            else:
                await _send_max(ctx, post_text, None)

            if ctx.target == "channel":
                tg_text = text_with_telegram_cta(
                    post_text,
                    body_without_cta=body_without_cta,
                    add_channel_link=add_link,
                    max_link=ctx.channel_link or "",
                    telegram_link=getattr(ctx.channel, "telegram_link", None),
                    channel_title=ctx.channel_title or "канал",
                    personalized=False,
                )
                await _mirror_to_telegram(
                    ctx,
                    tg_text,
                    prefer_audio_with_image=bool(has_audio and has_image and not has_video),
                )
        finally:
            # After MAX upload + optional TG mirror — drop local media files.
            _cleanup_local_media(ctx)
