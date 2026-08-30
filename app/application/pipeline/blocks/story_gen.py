from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext
from app.application.pipeline.recent_topics import topic_from_post_text
from app.application.pipeline.topic_queue import pop_topic

_CHARS_PER_MINUTE = 950  # ~at TTS speed 0.85 wall-clock

_JSON_FIELD_RE = re.compile(
    r'"(caption|story)"\s*:\s*"((?:[^"\\]|\\.)*)"',
    re.DOTALL,
)


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _unescape_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return (
            value.replace(r"\"", '"')
            .replace(r"\n", "\n")
            .replace(r"\t", "\t")
            .replace(r"\\", "\\")
        )


def _looks_like_json_blob(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    head = t[:80].lstrip()
    if head.startswith("{") or head.startswith("```"):
        return True
    if '"caption"' in head or '"story"' in head:
        return True
    return False


def _extract_json_fields_regex(raw: str) -> dict[str, str]:
    """Best-effort field extract when full json.loads fails (truncated / bad escapes)."""
    out: dict[str, str] = {}
    text = raw or ""
    for match in _JSON_FIELD_RE.finditer(text):
        key = match.group(1)
        out[key] = _unescape_json_string(match.group(2)).strip()

    # Truncated story: "story": "....   (no closing quote)
    if "story" not in out:
        m = re.search(r'"story"\s*:\s*"(.*)\Z', text, re.DOTALL)
        if m:
            chunk = m.group(1)
            # Drop trailing incomplete escape / JSON crumbs.
            chunk = re.sub(r'\\$', "", chunk)
            chunk = re.sub(r'"\s*,?\s*"?caption".*$', "", chunk, flags=re.I | re.DOTALL)
            chunk = re.sub(r'"\s*\}\s*$', "", chunk)
            recovered = _unescape_json_string(chunk).strip()
            if recovered:
                out["story"] = recovered

    if "caption" not in out:
        m = re.search(r'"caption"\s*:\s*"(.*?)(?:"\s*,|"\s*\}|\Z)', text, re.DOTALL)
        if m:
            recovered = _unescape_json_string(m.group(1)).strip()
            # Reject if we clearly swallowed the story field.
            if recovered and '"story"' not in recovered[:40]:
                # Trim at story key if greedy
                if '", "story"' in recovered or '","story"' in recovered:
                    recovered = re.split(r'"\s*,\s*"story"', recovered, maxsplit=1)[0]
                out["caption"] = recovered.strip().rstrip('"').strip()

    return out


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = _strip_code_fences(raw or "")
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    fields = _extract_json_fields_regex(text)
    return fields or None


def _clean_caption(caption: str, story: str) -> str:
    cap = (caption or "").strip()
    if not cap or _looks_like_json_blob(cap):
        prose = (story or "").strip()
        if prose and not _looks_like_json_blob(prose):
            first = prose.split("\n", 1)[0].strip()
            return first[:180] if first else ""
        return ""
    return cap[:500]


def _clean_story(story: str, raw: str) -> str:
    text = (story or "").strip()
    if text and not _looks_like_json_blob(text):
        return text
    fallback = _strip_code_fences(raw or "")
    if fallback and not _looks_like_json_blob(fallback):
        return fallback
    # Last resort: regex story only (may be truncated but better than JSON leak).
    fields = _extract_json_fields_regex(raw or "")
    recovered = (fields.get("story") or "").strip()
    if recovered and not _looks_like_json_blob(recovered):
        return recovered
    return ""


def _target_chars(minutes: int) -> int:
    m = max(2, min(int(minutes or 5), 12))
    return int(m * _CHARS_PER_MINUTE)


async def generate_fairy_tale(
    openai_client: Any,
    *,
    brief: str,
    channel_title: str,
    topic: str | None = None,
    target_minutes: int = 5,
    age_range: str = "3-6",
    story_format: str = "bedtime",
) -> tuple[str, str]:
    """Return (caption, story_script)."""
    minutes = max(2, min(int(target_minutes or 5), 12))
    chars = _target_chars(minutes)
    age = (age_range or "3-6").strip() or "3-6"
    fmt = (story_format or "fairy_tale").strip() or "fairy_tale"
    if fmt == "bedtime":
        fmt = "fairy_tale"

    system_prompt = (
        "Ты — автор добрых детских аудиосказок для засыпания. "
        "Отвечай ТОЛЬКО валидным JSON-объектом без markdown-ограждений: "
        '{"caption":"...","story":"..."}. '
        "caption — короткий анонс для поста в канал (2–5 предложений, можно с эмодзи). "
        "В caption НЕ пиши призывы подписаться или поделиться — это добавит система. "
        "story — полный текст сказки для озвучки: чистая проза без markdown, "
        "без эмодзи, без заголовков # и без CTA/ссылок. "
        "Внутри строк JSON экранируй кавычки как \\\" и переносы как \\n. "
        "Сюжет спокойный, тёплый, без страшилок, насилия и жести. "
        "Язык: русский."
    )
    topic_line = f"Тема сказки: «{topic}».\n" if (topic or "").strip() else ""
    user_prompt = (
        f"Канал: «{channel_title or 'Аудиосказки'}».\n"
        f"Возраст слушателей: {age}.\n"
        f"Формат: fairy_tale / bedtime (для засыпания).\n"
        f"Целевая длительность озвучки: около {minutes} минут "
        f"(примерно {chars} символов в поле story, допустимо ±15%).\n"
        f"{topic_line}"
        f"Бриф / правила канала:\n«{(brief or '').strip()[:3500] or 'Добрые сказки на ночь'}»\n\n"
        f"Напиши новую оригинальную сказку и короткий caption."
    )
    raw = await openai_client.generate_text(
        prompt=user_prompt, system_prompt=system_prompt
    )
    data = _extract_json_object(raw) or {}
    story = _clean_story(str(data.get("story") or ""), raw)
    caption = _clean_caption(str(data.get("caption") or ""), story)
    if not story:
        logger.warning("generate_fairy_tale: failed to recover story from model output")
    return caption, story


class StoryGenBlock:
    type_id = "story_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        if not config.get("enabled"):
            return

        mode = (config.get("mode") or "ai").strip() or "ai"
        if mode == "fixed":
            story = (
                (config.get("generated_story") or "").strip()
                or (ctx.story_script or "").strip()
            )
            caption = (
                (config.get("generated_caption") or "").strip()
                or (ctx.post_text or "").strip()
            )
            if story:
                ctx.story_script = story
            if caption:
                ctx.post_text = caption
            elif story:
                ctx.post_text = story.split("\n", 1)[0][:180]
            if (ctx.post_text or "").strip() and not (ctx.meta.get("post_topic") or "").strip():
                ctx.meta["post_topic"] = topic_from_post_text(ctx.post_text)
            return

        # Already seeded (e.g. runner preseed) — keep.
        if (ctx.story_script or "").strip() and (ctx.post_text or "").strip():
            if not (ctx.meta.get("post_topic") or "").strip():
                ctx.meta["post_topic"] = topic_from_post_text(ctx.post_text)
            return

        brief = (config.get("user_input") or "").strip()
        try:
            target_minutes = int(config.get("target_minutes") or 5)
        except (TypeError, ValueError):
            target_minutes = 5
        age_range = str(config.get("age_range") or "3-6")
        story_format = str(config.get("format") or "fairy_tale")

        queued_topic: str | None = None
        if getattr(ctx, "target", None) == "channel":
            shared = []
            if isinstance(ctx.meta, dict):
                shared = list(ctx.meta.get("shared_topic_queue") or [])
            queued_topic, remaining = pop_topic(shared)
            if queued_topic:
                ctx.meta["topic_queue_popped"] = True
                ctx.meta["topic_queue_remaining"] = remaining
                ctx.meta["topic_queue_used"] = queued_topic
                ctx.meta["topic_queue_block"] = "post_gen"
                ctx.meta["shared_topic_queue"] = remaining
                exhausted = len(remaining) == 0
                ctx.meta["topic_queue_exhausted"] = exhausted
                if exhausted:
                    owner_raw = ctx.meta.get("owner_max_user_id") if isinstance(ctx.meta, dict) else None
                    try:
                        owner_id = int(owner_raw) if owner_raw is not None else None
                    except (TypeError, ValueError):
                        owner_id = None
                    if owner_id and ctx.max_client is not None:
                        title = (ctx.channel_title or "").strip() or "канал"
                        try:
                            await ctx.max_client.send_message_to_user(
                                user_id=owner_id,
                                text=(
                                    f"Темы для «{title}» закончились. "
                                    f"Последнюю уже использовал, дальше иду по общему брифу."
                                ),
                            )
                        except Exception as e:
                            logger.warning(f"story topic queue alert failed: {e}")

        await ctx.notify("📖 Придумываю сказку...")
        topic_for_gpt = (queued_topic or "").strip() or brief or ctx.channel_title or ""
        fmt = (story_format or "fairy_tale").strip() or "fairy_tale"
        if fmt == "bedtime":
            fmt = "fairy_tale"

        # Fairy-tale / Sunor video path: script + scenes (hard ≤4500, gpt-5.4).
        if fmt == "fairy_tale":
            from app.application.pipeline.tale_video import generate_tale_script

            composed = topic_for_gpt
            if brief and queued_topic:
                composed = f"{queued_topic}\n\nБриф канала:\n{brief}"
            elif brief and not queued_topic:
                composed = brief
            script = await generate_tale_script(topic=composed)
            ctx.story_script = script.story
            ctx.post_text = script.caption or script.story.split("\n", 1)[0][:180]
            ctx.meta["tale_script"] = script.to_meta()
            ctx.meta["tale_title"] = script.title
            ctx.meta["post_topic"] = (queued_topic or "").strip() or topic_from_post_text(
                ctx.post_text
            )
            logger.info(
                f"story_gen tale_video caption_len={len(ctx.post_text)} "
                f"story_len={len(ctx.story_script)} scenes={len(script.scenes)} "
                f"run_id={ctx.run_id}"
            )
            return

        # Legacy podcast / non-fairy path (OpenAI TTS text only).
        _ = (target_minutes, age_range)
        caption, story = await generate_fairy_tale(
            ctx.openai_client,
            brief=brief,
            channel_title=ctx.channel_title or "",
            topic=queued_topic,
            target_minutes=target_minutes,
            age_range=age_range,
            story_format=story_format,
        )
        if not story:
            logger.warning(f"story_gen empty story run_id={ctx.run_id}")
            return
        ctx.story_script = story
        ctx.post_text = caption or story.split("\n", 1)[0][:180]
        ctx.meta["post_topic"] = (queued_topic or "").strip() or topic_from_post_text(
            ctx.post_text
        )
        logger.info(
            f"story_gen done caption_len={len(ctx.post_text)} "
            f"story_len={len(ctx.story_script)} run_id={ctx.run_id}"
        )
