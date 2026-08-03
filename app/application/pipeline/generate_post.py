from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.recent_topics import topic_from_post_text

MAX_TOPIC_ATTEMPTS = 15


class TopicDedupExhausted(Exception):
    """Raised when no unique topic is found within MAX_TOPIC_ATTEMPTS."""

    def __init__(
        self,
        *,
        channel_title: str,
        attempts: int,
        rejected_topics: list[str],
    ) -> None:
        self.channel_title = channel_title
        self.attempts = attempts
        self.rejected_topics = list(rejected_topics)
        super().__init__(
            f"Topic dedup exhausted after {attempts} attempts "
            f"for channel={channel_title!r}"
        )


async def generate_post_text(
    openai_client: Any,
    brief: str,
    channel_title: str,
    *,
    bold_headings: bool = True,
    use_emoji: bool = True,
    comments_enabled: bool = False,
    recent_topics: list[str] | None = None,
    news_item: dict[str, Any] | None = None,
    style_profile: dict[str, Any] | None = None,
    approved_topic: str | None = None,
) -> tuple[str, str]:
    """Generate a channel post from a brief, or strictly from news_item facts.

    Returns ``(post_text, topic)`` where topic is the approved/locked topic,
    news title, or the first line of the generated post as fallback.
    """
    if news_item:
        text = await _generate_news_post(
            openai_client,
            channel_title,
            news_item,
            bold_headings=bold_headings,
            use_emoji=use_emoji,
            comments_enabled=comments_enabled,
            recent_topics=recent_topics,
            editorial_brief=(brief or "").strip(),
            style_profile=style_profile,
        )
        topic = topic_from_post_text(news_item.get("title") or "") or topic_from_post_text(
            text
        )
        return text, topic

    topics = [t.strip() for t in (recent_topics or []) if t and t.strip()]
    style = _PostStyle(
        bold_headings=bold_headings,
        use_emoji=use_emoji,
        comments_enabled=comments_enabled,
    )

    locked = (approved_topic or "").strip()
    if locked:
        text = await _write_post_for_topic(
            openai_client,
            brief,
            channel_title,
            approved_topic=locked,
            recent_topics=topics,
            rejected_topics=[],
            style=style,
        )
        return text, locked

    if not topics:
        text = await _write_post_for_topic(
            openai_client,
            brief,
            channel_title,
            approved_topic="",
            recent_topics=[],
            rejected_topics=[],
            style=style,
        )
        return text, topic_from_post_text(text)

    rejected: list[str] = []
    approved = ""
    for attempt in range(1, MAX_TOPIC_ATTEMPTS + 1):
        candidate = await _propose_topic(
            openai_client,
            brief,
            channel_title,
            recent_topics=topics,
            rejected_topics=rejected,
            use_emoji=use_emoji,
        )
        if not candidate:
            logger.warning(
                f"Topic dedup: empty topic proposal attempt={attempt}/"
                f"{MAX_TOPIC_ATTEMPTS} channel={channel_title!r}"
            )
            continue

        if await _is_topic_duplicate(openai_client, candidate, topics):
            logger.warning(
                f"Topic dedup: duplicate attempt={attempt}/{MAX_TOPIC_ATTEMPTS} "
                f"topic={candidate!r} channel={channel_title!r}"
            )
            if candidate not in rejected:
                rejected.append(candidate)
            continue

        approved = candidate
        logger.info(
            f"Topic dedup: approved topic={approved!r} "
            f"attempt={attempt} channel={channel_title!r}"
        )
        break
    else:
        raise TopicDedupExhausted(
            channel_title=channel_title,
            attempts=MAX_TOPIC_ATTEMPTS,
            rejected_topics=rejected,
        )

    if not approved:
        raise TopicDedupExhausted(
            channel_title=channel_title,
            attempts=MAX_TOPIC_ATTEMPTS,
            rejected_topics=rejected,
        )

    text = await _write_post_for_topic(
        openai_client,
        brief,
        channel_title,
        approved_topic=approved,
        recent_topics=topics,
        rejected_topics=rejected,
        style=style,
    )
    return text, approved


class _PostStyle:
    __slots__ = ("bold_headings", "use_emoji", "comments_enabled")

    def __init__(
        self,
        *,
        bold_headings: bool,
        use_emoji: bool,
        comments_enabled: bool,
    ) -> None:
        self.bold_headings = bold_headings
        self.use_emoji = use_emoji
        self.comments_enabled = comments_enabled


def _build_avoid_block(
    topics: list[str],
    *,
    rejected_topics: list[str] | None = None,
) -> str:
    rejected = [t.strip() for t in (rejected_topics or []) if t and t.strip()]
    if not topics and not rejected:
        return ""
    parts: list[str] = []
    if topics:
        listed = "\n".join(f"- {t}" for t in topics[:25])
        parts.append(
            f"Уже недавно публиковались темы/рецепты:\n{listed}\n\n"
            f"НЕ повторяй их: ни те же блюда/темы/предметы, ни близкие вариации "
            f"той же проблемы или главного объекта "
            f"(например «прилипчивый ребёнок» и «ребёнок липнет к маме», "
            f"или два поста про зеркала в интерьере — это дубли). "
            f"Придумай совершенно другую тему.\n"
        )
    if rejected:
        listed_rej = "\n".join(f"- {t}" for t in rejected[:25])
        parts.append(
            f"Уже отвергнуты в этой сессии (тоже не используй):\n{listed_rej}\n"
            f"Выбери ДРУГУЮ тему.\n"
        )
    return "\n".join(parts) + "\n"


async def _propose_topic(
    openai_client: Any,
    brief: str,
    channel_title: str,
    *,
    recent_topics: list[str],
    rejected_topics: list[str],
    use_emoji: bool,
) -> str:
    avoid = _build_avoid_block(recent_topics, rejected_topics=rejected_topics)
    emoji_rule = (
        "- Можно начать с одного уместного эмодзи"
        if use_emoji
        else "- Без эмодзи"
    )
    system_prompt = (
        "Ты придумываешь темы постов для каналов. "
        "Ответь ТОЛЬКО одной строкой — заголовком темы, без пояснений."
    )
    user_prompt = (
        f"Придумай НОВУЮ тему поста для канала «{channel_title}» "
        f"по этому брифу/правилам:\n\n"
        f"«{brief}»\n\n"
        f"{avoid}"
        f"Правила:\n"
        f"- Язык: русский\n"
        f"- Одна строка: яркий заголовок темы\n"
        f"- Тема должна быть другой, не вариацией уже опубликованных\n"
        f"{emoji_rule}\n"
        f"- Ответ — ТОЛЬКО заголовок, без текста поста и без пояснений"
    )
    result = await openai_client.generate_text(
        prompt=user_prompt, system_prompt=system_prompt
    )
    return topic_from_post_text(result or "")


async def _write_post_for_topic(
    openai_client: Any,
    brief: str,
    channel_title: str,
    *,
    approved_topic: str,
    recent_topics: list[str],
    rejected_topics: list[str],
    style: _PostStyle,
) -> str:
    system_prompt = (
        "Ты — профессиональный копирайтер и автор контента для каналов MAX. "
        "Твоя задача — каждый раз писать новый интересный, вовлекающий пост "
        "по брифу пользователя. Не копируй предыдущие посты дословно."
    )

    bold_rule = (
        "- Заголовок и подзаголовки оформи жирным markdown: **текст**"
        if style.bold_headings
        else "- Не используй жирное выделение для заголовков и подзаголовков"
    )
    emoji_rule = (
        "- Используй заметно больше уместных эмодзи в заголовках и по тексту "
        "(секции, списки, CTA), но без перебора и без спама каждым словом"
        if style.use_emoji
        else "- Не используй эмодзи"
    )
    if style.comments_enabled:
        cta_rule = (
            "- CTA в конце ОБЯЗАТЕЛЬНО: призыв поделиться с друзьями "
            "(например «поделитесь с друзьями, если пригодилось»); "
            "можно также предложить реакции, сохранить, написать в комментариях"
        )
    else:
        cta_rule = (
            "- Комментарии в канале НЕ подключены: НЕ задавай вопросов читателям, "
            "НЕ проси ничего написать / ответить в комментариях / оставить отзыв текстом. "
            "- CTA в конце ОБЯЗАТЕЛЬНО: призыв поделиться с друзьями "
            "(например «поделитесь с друзьями, если рецепт пригодился»); "
            "плюс реакции, если понравилось — например «ставьте реакции, если понравилось»; "
            "можно сохранить / подписаться"
        )

    avoid = _build_avoid_block(recent_topics, rejected_topics=rejected_topics)
    topic_lock = ""
    if approved_topic:
        topic_lock = (
            f"Тема поста (ОБЯЗАТЕЛЬНО соблюдать, заголовок про неё):\n"
            f"{approved_topic}\n\n"
        )

    user_prompt = (
        f"Напиши пост для канала «{channel_title}» по этому брифу/правилам:\n\n"
        f"«{brief}»\n\n"
        f"{topic_lock}"
        f"{avoid}"
        f"Правила:\n"
        f"- Язык: русский\n"
        f"- Каждый раз придумывай новый уникальный пост по брифу\n"
        f"- Заголовок: яркий, привлекающий внимание\n"
        f"- Текст: информативный, полезный, с фактами и примерами\n"
        f"{cta_rule}\n"
        f"- Длина: 600-2000 символов\n"
        f"{bold_rule}\n"
        f"{emoji_rule}\n"
        f"- Ответ — ТОЛЬКО готовый пост, без пояснений"
    )
    result = await openai_client.generate_text(
        prompt=user_prompt, system_prompt=system_prompt
    )
    return (result or "").strip()


async def _is_topic_duplicate(
    openai_client: Any,
    candidate_topic: str,
    recent_topics: list[str],
) -> bool:
    """Return True only on a clear DUPLICATE verdict; fail-open otherwise."""
    candidate = (candidate_topic or "").strip()
    topics = [t.strip() for t in recent_topics if t and t.strip()]
    if not candidate or not topics:
        return False

    listed = "\n".join(f"- {t}" for t in topics[:25])
    system_prompt = (
        "Ты проверяешь уникальность темы поста для канала. "
        "Ответь одним словом: DUPLICATE или OK."
    )
    user_prompt = (
        f"Новая тема (заголовок):\n{candidate}\n\n"
        f"Недавние темы в канале:\n{listed}\n\n"
        f"DUPLICATE — если новая тема про то же самое или близкая вариация "
        f"(тот же предмет, та же проблема, тот же главный объект; "
        f"синонимы и перефраз тоже дубль).\n"
        f"OK — если тема действительно другая.\n"
        f"Ответ — только DUPLICATE или OK."
    )
    try:
        raw = await openai_client.generate_text(
            prompt=user_prompt, system_prompt=system_prompt
        )
    except Exception as e:
        logger.warning(f"Topic dedup judge failed (fail-open): {e}")
        return False

    cleaned = (raw or "").strip().casefold().replace("*", "").replace("`", "")
    first = cleaned.split()[0] if cleaned.split() else ""
    # Strip trailing punctuation: "duplicate.", "duplicate:"
    first = first.rstrip(".,:;!")
    return first == "duplicate"


def _format_style_block(style_profile: dict[str, Any] | None) -> str:
    if not isinstance(style_profile, dict):
        return ""
    tone = str(style_profile.get("tone") or "").strip()
    audience = str(style_profile.get("audience") or "").strip()
    custom = str(style_profile.get("custom_prompt") or "").strip()[:800]
    lines: list[str] = []
    if tone:
        lines.append(f"- Тон: {tone}")
    if audience:
        lines.append(f"- Аудитория: {audience}")
    if custom:
        lines.append(f"- Доп. стиль: {custom}")
    if not lines:
        return ""
    return "Стиль канала:\n" + "\n".join(lines) + "\n\n"


async def _generate_news_post(
    openai_client: Any,
    channel_title: str,
    news_item: dict[str, Any],
    *,
    bold_headings: bool,
    use_emoji: bool,
    comments_enabled: bool,
    recent_topics: list[str] | None,
    editorial_brief: str = "",
    style_profile: dict[str, Any] | None = None,
) -> str:
    system_prompt = (
        "Ты — новостной редактор канала MAX. Пиши пост строго по фактам новости. "
        "Ничего не выдумывай. Если фактов мало — пиши коротко. "
        "Стиль и редакционные правила влияют только на подачу, не на факты."
    )
    bold_rule = (
        "- Заголовок оформи жирным markdown: **текст**"
        if bold_headings
        else "- Не используй жирное выделение"
    )
    emoji_rule = "- Можно 1–3 уместных эмодзи" if use_emoji else "- Не используй эмодзи"
    cta_rule = (
        "- В конце можно мягко предложить реакцию или комментарий"
        if comments_enabled
        else "- Комментарии выключены: не проси писать в комментариях; можно реакции / поделиться"
    )
    title = (news_item.get("title") or "").strip()
    summary = (news_item.get("summary") or "").strip()
    url = (news_item.get("url") or "").strip()
    published = (news_item.get("published_at") or "").strip()
    topics = [t.strip() for t in (recent_topics or []) if t and t.strip()]
    avoid = ""
    if topics:
        listed = "\n".join(f"- {t}" for t in topics[:15])
        avoid = f"Недавно уже было:\n{listed}\n\nСделай акцент иначе, но факты те же.\n\n"

    style_block = _format_style_block(style_profile)
    brief = (editorial_brief or "").strip()[:1500]
    brief_block = ""
    if brief:
        brief_block = (
            f"Редакционные правила автора (только подача, не новые факты):\n"
            f"{brief}\n\n"
        )

    user_prompt = (
        f"Напиши новостной пост для канала «{channel_title}».\n\n"
        f"ФАКТЫ НОВОСТИ (единственный источник правды — приоритет выше стиля):\n"
        f"Заголовок: {title}\n"
        f"Дата: {published or 'не указана'}\n"
        f"Кратко: {summary[:2000]}\n"
        f"URL: {url}\n\n"
        f"{style_block}"
        f"{brief_block}"
        f"{avoid}"
        f"Правила:\n"
        f"- Язык: русский\n"
        f"- Только факты из блока «ФАКТЫ НОВОСТИ»; стиль/бриф не добавляют событий\n"
        f"- В конце укажи источник ссылкой, если URL есть\n"
        f"- Длина: 400-1600 символов\n"
        f"{bold_rule}\n"
        f"{emoji_rule}\n"
        f"{cta_rule}\n"
        f"- Ответ — ТОЛЬКО готовый пост, без пояснений"
    )
    result = await openai_client.generate_text(prompt=user_prompt, system_prompt=system_prompt)
    return result.strip()
