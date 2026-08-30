from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.application.pipeline.context import PipelineContext
from app.infrastructure.services.openai_client import UPLOAD_DIR


def _escape_md(text: str) -> str:
    for ch in ("\\", "*", "_", "[", "]", "(", ")", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


SHARE_CTA_AUDIO = (
    "\n\nПоделитесь с друзьями — пусть и у них будет добрая сказка перед сном"
)


def build_subscribe_cta(
    link: str,
    *,
    title: str = "канал",
    personalized: bool = False,
) -> str:
    if personalized:
        return f"\n\n**👉 [Подпишись на {_escape_md(title)}]({link})**"
    return f"\n\n**👉 [Подпишись на канал]({link})**"


def build_share_cta_audio(post_text: str) -> str:
    """Append share CTA for audio fairy-tale posts unless already present."""
    body = (post_text or "").rstrip()
    if not body:
        return body
    lower = body.lower()
    if "поделит" in lower or "поделись" in lower:
        return body
    return body + SHARE_CTA_AUDIO


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


def _wants_logo_watermark(ctx: PipelineContext) -> bool:
    if not bool(ctx.meta.get("add_watermark")):
        return False
    logo = ""
    if ctx.channel is not None:
        logo = (getattr(ctx.channel, "logo_path", None) or "").strip()
    if not logo:
        logger.warning(
            f"add_watermark=True but no logo_path "
            f"channel_id={getattr(ctx.channel, 'id', None)} run_id={ctx.run_id}"
        )
        return False
    if not Path(logo).is_file():
        logger.warning(
            f"add_watermark=True but logo file missing path={logo} "
            f"channel_id={getattr(ctx.channel, 'id', None)} run_id={ctx.run_id}"
        )
        return False
    return True


def _logo_path(ctx: PipelineContext) -> str:
    return (getattr(ctx.channel, "logo_path", None) or "").strip()


async def _local_image_path(ctx: PipelineContext) -> str | None:
    """Resolve ctx.image_url to a local file path (download HTTP URLs to temp)."""
    raw = (ctx.image_url or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            resp = await client.get(raw)
            resp.raise_for_status()
        suffix = Path(raw.split("?", 1)[0]).suffix or ".jpg"
        if suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            suffix = ".jpg"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / f"wm_src_{uuid.uuid4().hex[:12]}{suffix}"
        dest.write_bytes(resp.content)
        return str(dest)
    if Path(raw).is_file():
        return raw
    return None


def _watermark_image_to_temp(src: str, logo: str) -> str:
    from app.infrastructure.services.media_watermark import apply_logo_image

    dest = Path(tempfile.gettempdir()) / f"wm_img_{uuid.uuid4().hex[:12]}.png"
    return apply_logo_image(src, logo, str(dest))


def _watermark_video_to_temp(src: str, logo: str) -> str:
    from app.infrastructure.services.media_watermark import apply_logo_video

    suffix = Path(src).suffix or ".mp4"
    dest = Path(tempfile.gettempdir()) / f"wm_vid_{uuid.uuid4().hex[:12]}{suffix}"
    return apply_logo_video(src, logo, str(dest))


async def _attachment_from_image_path(
    ctx: PipelineContext, path_or_url: str
) -> dict[str, Any] | None:
    raw = (path_or_url or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return {"type": "image", "payload": {"url": raw}}
    token = await ctx.max_client.upload_file(raw, "image")
    return {"type": "image", "payload": {"token": token}}


async def _video_token_for_delivery(
    ctx: PipelineContext,
    *,
    temp_files: list[str],
) -> str | None:
    local = (ctx.video_local_path or "").strip()
    publish = local
    if _wants_logo_watermark(ctx) and local and Path(local).is_file():
        try:
            publish = _watermark_video_to_temp(local, _logo_path(ctx))
            temp_files.append(publish)
        except Exception:
            logger.exception(
                f"Video logo watermark failed run_id={ctx.run_id}; publishing clean"
            )
            publish = local
    if publish and (
        publish != local or not (ctx.video_token or "").strip()
    ):
        return await ctx.max_client.upload_file(publish, "video")
    token = (ctx.video_token or "").strip()
    if token:
        return token
    if publish and Path(publish).is_file():
        return await ctx.max_client.upload_file(publish, "video")
    return None


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
    image_path: str | None = None,
    video_path: str | None = None,
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
        audio_path = (ctx.audio_local_path or "").strip() or None
        image_url = image_path if image_path is not None else ((ctx.image_url or "").strip() or None)
        vid = video_path if video_path is not None else ((ctx.video_local_path or "").strip() or None)

        if prefer_audio_with_image and image_url and audio_path and not vid:
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

        if vid:
            image_url = None
        elif audio_path:
            image_url = None
        await tg.publish_post(
            tg_chat_id,
            post_text[:4096],
            image_url=image_url,
            video_path=vid,
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
    if isinstance(ctx.meta, dict):
        ctx.meta["published"] = True


def _cleanup_temps(paths: list[str]) -> None:
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass


def _has_publishable_video(ctx: PipelineContext) -> bool:
    return bool(
        (ctx.video_token or "").strip() or (ctx.video_local_path or "").strip()
    )


async def _maybe_apply_tale_post_brief(
    ctx: PipelineContext,
    config: dict[str, Any],
) -> None:
    """Regenerate post caption from post_gen brief for fairy-tale video publishes."""
    if not _has_publishable_video(ctx):
        return
    if str(config.get("mode") or "ai").strip().lower() != "ai":
        return

    from app.application.pipeline.generate_post import generate_tale_post_caption
    from app.application.pipeline.normalize import resolve_post_brief
    from app.application.pipeline.tale_video import TaleScript

    schedule = ctx.meta.get("pipeline_schedule") if isinstance(ctx.meta, dict) else None
    slot_time = str(ctx.meta.get("slot_time") or "").strip() if isinstance(ctx.meta, dict) else ""
    brief = resolve_post_brief(schedule, config, slot_time or None).strip()
    if not brief:
        return
    if ctx.openai_client is None:
        logger.warning(
            f"post_gen tale brief skipped: no openai_client run_id={ctx.run_id}"
        )
        return

    script = TaleScript.from_meta(
        ctx.meta.get("tale_script") if isinstance(ctx.meta, dict) else None
    )
    tale_title = ""
    tale_caption = (ctx.post_text or "").strip()
    story_excerpt = (ctx.story_script or "").strip()
    if script is not None:
        tale_title = script.title
        tale_caption = tale_caption or script.caption
        story_excerpt = story_excerpt or script.story

    if isinstance(ctx.meta, dict):
        tale_title = tale_title or str(ctx.meta.get("tale_title") or "").strip()

    try:
        caption = await generate_tale_post_caption(
            ctx.openai_client,
            brief,
            ctx.channel_title or "",
            tale_title=tale_title,
            tale_caption=tale_caption,
            story_excerpt=story_excerpt,
            bold_headings=bool(config.get("bold_headings", True)),
            use_emoji=bool(config.get("use_emoji", True)),
            comments_enabled=bool(config.get("comments_enabled", False)),
        )
    except Exception:
        logger.exception(
            f"post_gen tale brief generation failed run_id={ctx.run_id}; "
            f"keeping story caption"
        )
        return

    if caption.strip():
        ctx.post_text = caption.strip()
        logger.info(
            f"post_gen tale: caption from brief len={len(ctx.post_text)} "
            f"run_id={ctx.run_id}"
        )


class PostGenBlock:
    type_id = "post_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        temp_files: list[str] = []
        try:
            await self._execute(ctx, config, temp_files=temp_files)
        finally:
            _cleanup_temps(temp_files)

    async def _execute(
        self,
        ctx: PipelineContext,
        config: dict[str, Any],
        *,
        temp_files: list[str],
    ) -> None:
        if not config.get("enabled"):
            if ctx.target == "user" and (
                ctx.video_token or (ctx.video_local_path or "").strip()
            ) and ctx.target_user_id is not None:
                token = await _video_token_for_delivery(ctx, temp_files=temp_files)
                if token:
                    await ctx.max_client.send_message_to_user(
                        user_id=ctx.target_user_id,
                        text="🎬",
                        attachments=[{"type": "video", "payload": {"token": token}}],
                        fmt="markdown",
                    )
            return

        await _maybe_apply_tale_post_brief(ctx, config)

        has_video = bool(
            (ctx.video_token or "").strip() or (ctx.video_local_path or "").strip()
        )
        has_audio = bool(ctx.audio_token or (ctx.audio_local_path or "").strip())
        has_image = bool((ctx.image_url or "").strip())

        post_text = (ctx.post_text or "").strip() or (config.get("generated_post") or "").strip()
        if not post_text:
            if ctx.target == "user" and has_video and ctx.target_user_id is not None:
                token = await _video_token_for_delivery(ctx, temp_files=temp_files)
                if token:
                    await ctx.max_client.send_message_to_user(
                        user_id=ctx.target_user_id,
                        text="🎬",
                        attachments=[{"type": "video", "payload": {"token": token}}],
                        fmt="markdown",
                    )
            elif not (ctx.target == "channel" and has_video):
                return

        if has_audio:
            post_text = build_share_cta_audio(post_text)

        body_without_cta = post_text
        add_link = bool(config.get("add_channel_link") and ctx.channel_link)
        if add_link:
            post_text = body_without_cta + build_subscribe_cta(
                ctx.channel_link,
                title=ctx.channel_title or "канал",
                personalized=(ctx.target == "user"),
            )

        ctx.post_text = post_text

        publish_image: str | None = None
        publish_video: str | None = None

        if has_video:
            local_video = (ctx.video_local_path or "").strip()
            if _wants_logo_watermark(ctx) and local_video and Path(local_video).is_file():
                try:
                    publish_video = _watermark_video_to_temp(local_video, _logo_path(ctx))
                    temp_files.append(publish_video)
                except Exception:
                    logger.exception(
                        f"Video logo watermark failed run_id={ctx.run_id}; "
                        f"publishing clean"
                    )
                    publish_video = local_video or None
            else:
                publish_video = local_video or None

            token = (ctx.video_token or "").strip()
            if publish_video and (
                publish_video != local_video or not token
            ):
                token = await ctx.max_client.upload_file(publish_video, "video")
            if token:
                await _send_max(
                    ctx,
                    post_text,
                    [{"type": "video", "payload": {"token": token}}],
                )
        elif has_audio and has_image:
            if _wants_logo_watermark(ctx):
                try:
                    local = await _local_image_path(ctx)
                    if local:
                        if local != (ctx.image_url or "").strip():
                            temp_files.append(local)
                        publish_image = _watermark_image_to_temp(local, _logo_path(ctx))
                        temp_files.append(publish_image)
                except Exception:
                    logger.exception(
                        f"Image logo watermark failed run_id={ctx.run_id}; "
                        f"publishing clean"
                    )
            image_att = await _attachment_from_image_path(
                ctx, publish_image or (ctx.image_url or "").strip()
            )
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
            if publish_image is None:
                publish_image = (ctx.image_url or "").strip() or None
        elif has_audio:
            audio_att = await _build_audio_attachment(ctx)
            await _send_max(
                ctx,
                post_text,
                [audio_att] if audio_att else None,
            )
        elif has_image:
            if _wants_logo_watermark(ctx):
                try:
                    local = await _local_image_path(ctx)
                    if local:
                        if local != (ctx.image_url or "").strip():
                            temp_files.append(local)
                        publish_image = _watermark_image_to_temp(local, _logo_path(ctx))
                        temp_files.append(publish_image)
                except Exception:
                    logger.exception(
                        f"Image logo watermark failed run_id={ctx.run_id}; "
                        f"publishing clean"
                    )
            image_att = await _attachment_from_image_path(
                ctx, publish_image or (ctx.image_url or "").strip()
            )
            await _send_max(
                ctx,
                post_text,
                [image_att] if image_att else None,
            )
            if publish_image is None:
                publish_image = (ctx.image_url or "").strip() or None
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
                image_path=publish_image,
                video_path=publish_video,
            )
