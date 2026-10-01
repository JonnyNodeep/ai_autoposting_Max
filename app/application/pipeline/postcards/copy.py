"""Postcard copy helpers: poem system prompt and hard length trim."""
from __future__ import annotations

import re

from app.application.pipeline.postcards.topics import PostcardTopic

MAX_POEM_CHARS = 350


def postcard_poem_system_prompt(*, channel_title: str = "") -> str:
    channel = (channel_title or "").strip() or "канал открыток"
    return (
        f"Ты автор тёплых поздравительных открыток для канала «{channel}». "
        "Напиши короткое поздравление в виде стиха на русском языке. "
        f"Жёсткий лимит: не больше {MAX_POEM_CHARS} символов вместе с пробелами и переносами. "
        "Без призывов подписаться, без ссылок, без хэштегов, без CTA, без рекламы. "
        "Без заголовков в markdown. Только сам стих, тёплый и искренний тон. "
        "Можно лёгкие emoji, но не больше двух."
    )


def postcard_poem_user_prompt(topic: PostcardTopic) -> str:
    return (
        f"Тема: {topic.title}\n"
        f"Надпись на открытке: {topic.short_label}\n"
        f"Сезонное настроение: {topic.season_hint}\n"
        f"Слот: {topic.slot_role}\n\n"
        f"Напиши стих-поздравление не длиннее {MAX_POEM_CHARS} символов."
    )


def postcard_image_instruction(topic: PostcardTopic) -> str:
    return (
        "Премиальная живая открытка, которую хочется переслать: дорогая иллюстрация, "
        "аккуратная композиция, мягкий свет, высокое качество печати. "
        "Сцена строго по теме открытки и надписи — не общие «уютные» клише. "
        "Можно изображать людей и лица. "
        "Животных и питомцев добавляй только если это прямо следует из темы; "
        "не ставь кошек и других животных как случайный милый декор. "
        f"Сезон и атмосфера: {topic.season_hint}. "
        f"На открытке обязательно напиши кириллицей красивым понятным читаемым шрифтом "
        f"точно эту фразу: «{topic.short_label}». "
        "Текст крупный, ровный, без ошибок и без лишних букв."
    )


def trim_poem(text: str, max_chars: int = MAX_POEM_CHARS) -> str:
    """Trim poem to max_chars, preferring whole lines when possible."""
    raw = (text or "").strip()
    if not raw:
        return ""
    # Drop accidental markdown fences / labels
    raw = re.sub(r"^```(?:\w+)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()
    if len(raw) <= max_chars:
        return raw

    lines = [ln.rstrip() for ln in raw.splitlines() if ln.strip()]
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join(kept + [line]) if kept else line
        if len(candidate) <= max_chars:
            kept.append(line)
            continue
        break
    if kept:
        return "\n".join(kept).strip()

    # Single long line: hard cut at last space before limit
    cut = raw[:max_chars]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip()


async def generate_postcard_poem(
    openai_client: object,
    topic: PostcardTopic,
    *,
    channel_title: str = "",
) -> str:
    """Generate postcard poem via OpenAI-compatible client.generate_text."""
    system = postcard_poem_system_prompt(channel_title=channel_title)
    user = postcard_poem_user_prompt(topic)
    generate_text = getattr(openai_client, "generate_text", None)
    if generate_text is None:
        return trim_poem(topic.short_label)
    text = await generate_text(system, user)  # type: ignore[misc]
    return trim_poem(str(text or ""))
