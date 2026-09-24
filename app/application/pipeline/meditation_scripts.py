"""GPT content for meditation channel: post caption + spoken audio script."""
from __future__ import annotations

from typing import Any

from app.application.pipeline.meditation_post_budget import (
    POST_BODY_MIN_CHARS,
    POST_MAX_PUBLISHED,
)

AUDIO_SCRIPT_MAX_CHARS = 800
LUNCH_AUDIO_SCRIPT_MAX_CHARS = 2800
POST_MAX_CHARS = POST_MAX_PUBLISHED

GENDER_NEUTRAL_TEXT_RULES = (
    "- Тексты гендерно нейтральные: подходят и женщинам, и мужчинам\n"
    "- Без обращений по полу: не «дорогая», «милая», «подруга», «богиня», "
    "«сильная женщина», «для женщин», «брат», «друг мой»\n"
    "- Без форм рода: не «готова», «расслабилась», «открыла глаза», "
    "«спокойна», «сильная», «любима» и мужских зеркал («готов», «спокоен»)\n"
    "- Инструкции — императив («сделай вдох», «закрой глаза») или «вы» "
    "(«закройте глаза», «вы готовы»)\n"
    "- Аффирмации от «я» — глаголы в настоящем («я принимаю», «я создаю»); "
    "вместо «я спокойна/спокоен» пиши «я выбираю спокойствие», «во мне сила»"
)


def _audio_script_max_chars(slot_kind: str) -> int:
    if (slot_kind or "").strip().lower() == "lunch":
        return LUNCH_AUDIO_SCRIPT_MAX_CHARS
    return AUDIO_SCRIPT_MAX_CHARS


def _slot_labels(slot_kind: str) -> tuple[str, str]:
    kind = (slot_kind or "").strip().lower()
    if kind == "morning":
        return (
            "утренняя аффirmация / материализация мыслей",
            "spoken-word аффirmацию для озвучки спокойным голосом под тихую мелодию",
        )
    if kind == "lunch":
        return (
            "дыхательная практика ~5 минут",
            "полную пошаговую инструкцию дыхательной практики на русском для озвучки "
            "(с паузами «…», реальное время на вдох/выдох, не песня)",
        )
    return (
        "вечерняя инструментальная медитация (только музыка, без голоса)",
        "короткое описание медитации без текста для озвучки (только музыка)",
    )


def truncate_post(text: str, max_chars: int = POST_MAX_CHARS) -> str:
    """Hard-limit post caption length, prefer breaking at sentence/word boundaries."""
    body = (text or "").strip()
    if len(body) <= max_chars:
        return body
    cut = body[:max_chars]
    for sep in (". ", ".\n", "! ", "!\n", "? ", "?\n", "\n\n", "\n", " "):
        idx = cut.rfind(sep)
        min_keep = int(max_chars * 0.55)
        if idx >= min_keep:
            chunk = cut[: idx + len(sep.rstrip())].strip()
            if chunk:
                return chunk
    trimmed = cut.rstrip()
    if trimmed.endswith((".", "!", "?")):
        return trimmed[:max_chars]
    suffix = "…"
    if len(trimmed) + len(suffix) > max_chars:
        trimmed = trimmed[: max_chars - len(suffix)]
    return trimmed + suffix


def _opening_line_hint(slot_kind: str, topic: str) -> str:
    kind = (slot_kind or "").strip().lower()
    title = (topic or "").strip()
    if kind == "morning":
        return (
            f"- Первая строка ОБЯЗАТЕЛЬНО заканчивается на «уже здесь!» или «уже здесь.»\n"
            f"- Пример: ✨ Материализация мыслей уже здесь!  или  ✨ «{title}» уже здесь!\n"
            f"- Следом (по желанию): Сегодня — практика «{title}» с 1–2 emoji"
        )
    if kind == "lunch":
        return (
            f"- Первая строка ОБЯЗАТЕЛЬНО: 🌿 Дыхательная практика «{title}» уже здесь."
        )
    return f"- Первая строка ОБЯЗАТЕЛЬНО: 🕯️ Медитация «{title}» уже здесь."


def _post_prompt_rules(
    slot_kind: str,
    *,
    topic: str,
    emoji_hint: str,
    max_post_chars: int,
) -> str:
    kind = (slot_kind or "").strip().lower()
    opening = _opening_line_hint(slot_kind, topic)
    lines = [
        f"- Строго не больше {max_post_chars} символов (только текст поста, без подписки)",
        opening,
        f"- {emoji_hint}",
        "- Тёплый дружелюбный тон — заботливое приглашение, не сухая инструкция",
        GENDER_NEUTRAL_TEXT_RULES,
        "- Без хэштегов, без «поделитесь», без «впереди», без блока подписки",
        "- В тексте POST не упоминай голос, озвучку, вокал, пение, инструктора",
    ]
    if kind == "evening":
        lines.extend(
            [
                "- Только инструментальная медитация: без голоса и без вокала",
                "- Не перечисляй инструменты и звуки (чаши, колокольчики, BPM и т.п.)",
                "- После первой строки: 1–2 предложения атмосферы + строка с 🎧 про наушники",
            ]
        )
    else:
        lines.append(
            "- После первой строки: 1–2 тёплых предложения о практике + строка с 🎧 про наушники"
        )
    return "\n".join(lines)


def _post_example(slot_kind: str, topic: str) -> str:
    kind = (slot_kind or "").strip().lower()
    title = (topic or "Тема").strip()
    if kind == "morning":
        return (
            f"✨ Материализация мыслей уже здесь!\n\n"
            f"Сегодня — практика «{title}» 🚪✨\n\n"
            f"Наденьте наушники, закройте глаза и позвольте себе новые возможности 💫"
        )
    if kind == "lunch":
        return (
            f"🌿 Дыхательная практика «{title}» уже здесь.\n\n"
            f"Сделайте паузу и мягко верните себе спокойствие 💫\n\n"
            f"🎧 Наденьте наушники и позвольте дыханию стать вашей опорой."
        )
    return (
        f"🕯️ Медитация «{title}» уже здесь.\n\n"
        f"Тихий образ покоя и мягкий свет — пространство, где можно отпустить день 🌙\n\n"
        f"🎧 Наденьте наушники, закройте глаза и подарите себе минуты тишины."
    )


def _finalize_post(
    post_text: str,
    display_title: str,
    *,
    bold_headings: bool,
    max_post_chars: int,
) -> str:
    post = truncate_post((post_text or "").strip(), max_chars=max_post_chars)
    if post:
        return post
    if bold_headings:
        return truncate_post(f"**{display_title}**", max_chars=max_post_chars)
    return truncate_post(display_title, max_chars=max_post_chars)


async def generate_meditation_content(
    openai_client: Any,
    *,
    brief: str,
    channel_title: str,
    topic: str,
    slot_kind: str,
    bold_headings: bool = True,
    use_emoji: bool = True,
    max_post_chars: int | None = None,
) -> tuple[str, str, str]:
    """Return (post_text, audio_script, display_title)."""
    display_title = (topic or "").strip()
    if not display_title:
        raise ValueError("Topic is required for meditation content")

    body_max = max(POST_BODY_MIN_CHARS, int(max_post_chars or POST_MAX_CHARS))

    slot_label, script_kind = _slot_labels(slot_kind)
    emoji_hint = (
        "Обязательно 2–3 уместных emoji (в первой строке, в тексте и 🎧 у строки про наушники)"
        if use_emoji
        else "без emoji"
    )
    post_rules = _post_prompt_rules(
        slot_kind,
        topic=display_title,
        emoji_hint=emoji_hint,
        max_post_chars=body_max,
    )
    example = _post_example(slot_kind, display_title)

    warm_system = (
        "Ты автор постов медитационного канала. Пиши по-русски тепло, дружелюбно и спокойно — "
        "как заботливое приглашение слушателю. Тексты гендерно нейтральные: без женского "
        "или мужского рода в обращениях, подходят и женщинам, и мужчинам. "
        "Сохраняй фирменный стиль канала."
    )

    if slot_kind == "evening":
        system_prompt = warm_system + " Только музыка — без голоса в тексте поста."
        user_prompt = (
            f"Канал: «{channel_title}»\n"
            f"Формат: {slot_label}\n"
            f"Тема практики: «{display_title}»\n\n"
            f"Бриф редактора (для атмосферы, не копируй дословно):\n"
            f"{(brief or '').strip()[:3500]}\n\n"
            f"Напиши пост для канала.\n"
            f"{post_rules}\n\n"
            f"Пример стиля (не копируй дословно, сохрани структуру и «уже здесь»):\n"
            f"{example}\n\n"
            f"- Ответ: только текст поста, без пояснений"
        )
        post_text = await openai_client.generate_text(
            prompt=user_prompt,
            system_prompt=system_prompt,
        )
        return (
            _finalize_post(
                post_text,
                display_title,
                bold_headings=bold_headings,
                max_post_chars=body_max,
            ),
            "",
            display_title,
        )

    system_prompt = warm_system + " POST и SCRIPT без привязки к полу слушателя."
    script_max = _audio_script_max_chars(slot_kind)
    user_prompt = (
        f"Канал: «{channel_title}»\n"
        f"Формат: {slot_label}\n"
        f"Тема: «{display_title}»\n\n"
        f"Бриф редактора:\n{(brief or '').strip()[:3500]}\n\n"
        f"Верни два блока строго в формате:\n"
        f"POST:\n<текст поста для канала>\n"
        f"{post_rules}\n\n"
        f"Пример стиля POST (не копируй дословно, сохрани «уже здесь»):\n"
        f"{example}\n\n"
        f"SCRIPT:\n<{script_kind}, до {script_max} символов, "
        f"короткие фразы, без markdown, для озвучки Suno; для дыхания используй «…» "
        f"или [пауза N сек] где нужно время>\n"
        f"{GENDER_NEUTRAL_TEXT_RULES}\n"
    )
    raw = await openai_client.generate_text(
        prompt=user_prompt,
        system_prompt=system_prompt,
    )
    post_text, audio_script = _parse_post_script(raw)
    audio_script = audio_script[:script_max].strip()
    post_text = _finalize_post(
        post_text,
        display_title,
        bold_headings=bold_headings,
        max_post_chars=body_max,
    )
    return (post_text, audio_script, display_title)


def _parse_post_script(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "", ""
    upper = text.upper()
    post_marker = "POST:"
    script_marker = "SCRIPT:"
    post_start = upper.find(post_marker)
    script_start = upper.find(script_marker)
    if post_start >= 0 and script_start > post_start:
        post_body = text[post_start + len(post_marker) : script_start].strip()
        script_body = text[script_start + len(script_marker) :].strip()
        return post_body, script_body
    return text, ""
