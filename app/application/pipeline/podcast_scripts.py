"""GPT content for podcast channels: informative post + spoken audio script."""
from __future__ import annotations

from typing import Any, Literal

from app.application.pipeline.meditation_scripts import truncate_post
from app.application.pipeline.podcast_presets import PODCAST_NICHES

PodcastNiche = Literal["psychology", "money", "biohacking", "earnings"]

POST_MAX_CHARS = 1100
AUDIO_SCRIPT_MAX_CHARS = 2800
AUDIO_SCRIPT_MIN_CHARS = 1200


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


def _niche_system(niche: PodcastNiche) -> str:
    if niche == "money":
        return (
            "Ты автор канала про деньги и решения. Пиши по-русски уверенно, по делу, "
            "без хайпа и обещаний лёгких денег. Пост — лаконичный, но по сути; "
            "скрипт подкаста — как уверенный наставник в голосовом сообщении."
        )
    if niche == "biohacking":
        return (
            "Ты автор канала про биохакинг и здоровье. Пиши по-русски ясно, конкретно, "
            "научно аккуратно, без хайпа и чудо-средств. Пост — лаконичный, но по сути; "
            "скрипт подкаста — как чёткий эксперт-практик в голосовом сообщении: "
            "живой последовательный рассказ, без шепота и без длинных пауз."
        )
    if niche == "earnings":
        return (
            "Ты автор канала про идеи для заработка. Пиши по-русски энергично, с поддержкой, "
            "практично, без хайпа «лёгких денег» и без обещаний. Пост — лаконичный, но по сути; "
            "скрипт подкаста — как мотивирующий коуч в голосовом сообщении: "
            "живой последовательный рассказ, без шепота и без длинных пауз."
        )
    return (
        "Ты автор канала про психологию без лишнего. Пиши по-русски тепло, ясно, "
        "без воды и диагнозов. Пост — лаконичный, но по сути; "
        "скрипт подкаста — как спокойный чёткий психолог в голосовом сообщении: "
        "живой последовательный рассказ, без шепота и без длинных пауз."
    )


def _post_rules(*, use_emoji: bool, max_post_chars: int) -> str:
    emoji = (
        "Обязательно 3–6 уместных emoji в заголовке и пунктах — не перебор"
        if use_emoji
        else "без emoji"
    )
    return (
        f"- Строго не больше {max_post_chars} символов (только текст поста)\n"
        f"- {emoji}\n"
        "- Лаконичный пост для телефона: раскрывает суть темы, не статья и не пустой тизер\n"
        "- Структура: короткий заголовок, 1 короткое предложение вступления, затем 3–5 чётких пунктов\n"
        "- Markdown: **жирные** акценты в заголовке/пунктах, пустые строки между блоками\n"
        "- Приятно читать с телефона: короткие абзацы, не сплошной текст\n"
        "- Без хэштегов, без ссылок, без «подпишись», без «поделитесь» в POST\n"
        "- В POST не пиши про озвучку, подкаст, голос, Suno — это добавит система при наличии аудио"
    )


def _script_rules(*, channel_title: str, script_max: int) -> str:
    title = (channel_title or "канал").strip() or "канал"
    return (
        f"- Разговорный скрипт для озвучки, до {script_max} символов "
        f"(ориентир {AUDIO_SCRIPT_MIN_CHARS}–{script_max}, ~4–5 минут речи)\n"
        "- Чистая проза: без markdown, без списков с символами, без эмодзи\n"
        "- Раскрой тему глубже, чем POST: примеры, нюансы, мягкие выводы\n"
        "- Короткие фразы, естественный темп живого рассказа; не читай пост дословно\n"
        "- Без длинных медитативных пауз и без «суггестивного» растягивания\n"
        "- НЕ песня: без куплетов, припевов, рифм «под бит»\n"
        f"- В самом конце 1–2 мягкие фразы: упомяни канал «{title}» "
        f"(можно остаться с нами / если откликнулось — загляни в «{title}») "
        f"и коротко предложи переслать тому, кому сейчас это нужно — без навязчивости, "
        f"без крика «подпишись!!!»"
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


async def generate_podcast_content(
    openai_client: Any,
    *,
    brief: str,
    channel_title: str,
    topic: str,
    niche: PodcastNiche = "psychology",
    bold_headings: bool = True,
    use_emoji: bool = True,
    max_post_chars: int | None = None,
) -> tuple[str, str, str]:
    """Return (post_text, audio_script, display_title)."""
    display_title = (topic or "").strip()
    if not display_title:
        raise ValueError("Topic is required for podcast content")
    if niche not in PODCAST_NICHES:
        niche = "psychology"

    body_max = max(400, int(max_post_chars or POST_MAX_CHARS))
    script_max = AUDIO_SCRIPT_MAX_CHARS

    system_prompt = _niche_system(niche)
    user_prompt = (
        f"Канал: «{channel_title}»\n"
        f"Ниша: {niche}\n"
        f"Тема выпуска: «{display_title}»\n\n"
        f"Бриф редактора:\n{(brief or '').strip()[:3500]}\n\n"
        f"Верни два блока строго в формате:\n"
        f"POST:\n<информативный пост для канала>\n"
        f"{_post_rules(use_emoji=use_emoji, max_post_chars=body_max)}\n\n"
        f"SCRIPT:\n<script for spoken podcast narration>\n"
        f"{_script_rules(channel_title=channel_title, script_max=script_max)}\n"
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
