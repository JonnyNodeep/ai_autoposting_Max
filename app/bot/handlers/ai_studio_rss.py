import json
from urllib.parse import urlparse

from app.application.auth.feature_access import premium_invite_message, rss_allowed
from app.application.pipeline.rss_monitor import (
    NICHE_LABELS,
    format_keywords_review,
    format_publish_window_label,
    format_rss_keyword_lists_text,
    generate_keywords_for_topic,
    keyword_edit_line_preview,
    normalize_news_rss,
    parse_hhmm,
    parse_keywords_edit_text,
    parse_publish_window_text,
    resolve_site_add,
)
from app.bot.ai_studio_text_input import claim_text_input
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService

from app.bot.handlers.ai_studio_entry import REDIS_TTL, _session_expired, _show_blocks
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline

KW_REVIEW_TTL = 1800


def _looks_like_url(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _normalize_feed_url(text: str) -> str:
    raw = (text or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    return raw


def _kw_review_key(user_id: int) -> str:
    return f"ai_rss_kw_review:{user_id}"


def _format_keywords_lists(include: list[str], exclude: list[str]) -> str:
    return format_rss_keyword_lists_text(include, exclude)


def _keywords_edit_prompt(include: list[str], exclude: list[str]) -> str:
    inc_n = len(include)
    exc_n = len(exclude)
    joined_len = len(", ".join(include + exclude))
    if inc_n + exc_n > 12 or joined_len > 400:
        return (
            "✏️ *Ручное редактирование фильтра*\n\n"
            "Пришли одним сообщением в формате:\n\n"
            "`+:`\n"
            "слово1\n"
            "слово2\n"
            "`-:`\n"
            "исключ1\n\n"
            f"Сейчас в фильтре: +{inc_n} / −{exc_n} слов.\n"
            "Пустой `+:` или `-:` очищает список.\n"
            "Если указать только одну секцию — вторая останется как была."
        )
    inc_line = keyword_edit_line_preview(include)
    exc_line = keyword_edit_line_preview(exclude)
    return (
        "✏️ *Ручное редактирование фильтра*\n\n"
        "Пришли одним сообщением (можно править списки ниже):\n\n"
        f"`+: {inc_line}`\n"
        f"`-: {exc_line}`\n\n"
        "Пустой `+:` или `-:` очищает список.\n"
        "Если указать только одну строку — вторая останется как была."
    )


async def _prompt_keywords_manual_edit(
    max_user_id: int,
    max_client,
    *,
    include: list[str],
    exclude: list[str],
    back_callback: str = "ai:block:news_rss:filters",
) -> None:
    redis = await get_redis()
    await claim_text_input(redis, max_user_id, "rss_keywords_edit", "1", REDIS_TTL)
    builder = InlineKeyboardBuilder()
    builder.row(("Назад", back_callback))
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=_keywords_edit_prompt(include, exclude),
        attachments=[builder.build()],
        fmt="markdown",
    )


async def _show_rss_menu(max_user_id: int, max_client, state: dict) -> None:
    block = normalize_news_rss((state.get("blocks") or {}).get("news_rss"))
    feeds = list(block.get("feeds") or [])
    sites = list(block.get("sites") or [])
    enabled = "вкл" if block.get("enabled") else "выкл"
    niche = block.get("niche") or ""
    niche_label = NICHE_LABELS.get(niche, "не выбрана") if niche else "не выбрана"
    inc = block.get("include_keywords") or []
    exc = block.get("exclude_keywords") or []
    lines = [
        "📰 *RSS / сайты*",
        "",
        f"Статус: {enabled}",
        f"Лент: {len(feeds)} · Сайтов: {len(sites)}",
        f"Опрос: каждые {block.get('poll_interval_minutes', 5)} мин",
        f"Окно: {format_publish_window_label(block['publish_from_msk'], block['publish_until_msk'])}",
        f"Тема/фильтр: {niche_label}",
        f"Слова: +{len(inc)} / −{len(exc)}",
        "",
        "При запуске бот мониторит ленты и сайты и публикует подходящие новости в окне МСК.",
        "Расписание по времени при этом отключается.",
    ]
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text="\n".join(lines),
        attachments=[InlineKeyboardBuilder.ai_news_rss_menu(block)],
        fmt="markdown",
    )


async def _run_keyword_generation(
    max_user_id: int,
    max_client,
    *,
    niche: str,
    topic_brief: str = "",
    channel_title: str = "",
    channel_topic: str = "",
    session=None,
) -> None:
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text="🤖 Подбираю ключевые слова под тему...",
    )
    openai_client = OpenAIService()
    from app.application.admin.billing_context import billing_user_for_max_id

    if session is not None:
        async with billing_user_for_max_id(session, max_user_id):
            result = await generate_keywords_for_topic(
                openai_client,
                niche=niche,
                topic_brief=topic_brief,
                channel_title=channel_title,
                channel_topic=channel_topic,
            )
    else:
        result = await generate_keywords_for_topic(
            openai_client,
            niche=niche,
            topic_brief=topic_brief,
            channel_title=channel_title,
            channel_topic=channel_topic,
        )
    payload = {
        "niche": niche,
        "topic_brief": topic_brief,
        "include": result["include"],
        "exclude": result["exclude"],
        "reason": result.get("reason") or "",
        "source": result.get("source") or "ai",
    }
    redis = await get_redis()
    await redis.setex(_kw_review_key(max_user_id), KW_REVIEW_TTL, json.dumps(payload, ensure_ascii=False))
    text = format_keywords_review(
        niche=niche,
        include=list(payload["include"]),
        exclude=list(payload["exclude"]),
        reason=str(payload["reason"]),
    )
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=text,
        attachments=[InlineKeyboardBuilder.ai_news_rss_keywords_review()],
        fmt="markdown",
    )


async def handle_rss_callback(callback_data: str, max_user_id: int, max_client, channel_repo, session) -> bool:
    is_rss_cb = (
        callback_data == "ai:edit:news_rss"
        or callback_data.startswith("ai:block:news_rss:")
    )
    if not is_rss_cb:
        return False
    if not rss_allowed(max_user_id):
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=premium_invite_message("RSS и новости"),
            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
        )
        return True

    if callback_data == "ai:edit:news_rss":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        if "news_rss" not in (state.get("blocks") or {}):
            await fsm.set_block_data(max_user_id, "news_rss", {})
            state = await fsm.get_state(max_user_id)
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})
        await _show_rss_menu(max_user_id, max_client, state)
        return True

    if callback_data == "ai:block:news_rss:toggle":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        block = normalize_news_rss((state.get("blocks") or {}).get("news_rss"))
        enabling = not bool(block.get("enabled"))
        if enabling:
            await fsm.set_block_data(
                max_user_id, "news_rss", {"enabled": True, "mode": "on_new"}
            )
            await fsm.set_block_data(max_user_id, "schedule", {"enabled": False})
        else:
            await fsm.set_block_data(max_user_id, "news_rss", {"enabled": False})
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_rss_menu(max_user_id, max_client, state)
        return True

    if callback_data == "ai:block:news_rss:add":
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "rss_feed", "1", REDIS_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Назад", "ai:edit:news_rss"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Пришли ссылку на RSS-ленту (http/https).",
            attachments=[builder.build()],
        )
        return True

    if callback_data == "ai:block:news_rss:add_site":
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "rss_site", "1", REDIS_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Назад", "ai:edit:news_rss"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "Пришли ссылку на раздел сайта (http/https).\n"
                "Сначала поищу RSS на странице; если нет — буду читать список новостей."
            ),
            attachments=[builder.build()],
        )
        return True

    if callback_data.startswith("ai:block:news_rss:del_site:"):
        idx = int(callback_data.split(":")[4])
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        sites = list(
            normalize_news_rss((state.get("blocks") or {}).get("news_rss")).get("sites") or []
        )
        if 0 <= idx < len(sites):
            sites.pop(idx)
            await fsm.set_block_data(max_user_id, "news_rss", {"sites": sites})
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_rss_menu(max_user_id, max_client, state)
        return True

    if callback_data.startswith("ai:block:news_rss:del:"):
        idx = int(callback_data.split(":")[4])
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        feeds = list(
            normalize_news_rss((state.get("blocks") or {}).get("news_rss")).get("feeds") or []
        )
        if 0 <= idx < len(feeds):
            feeds.pop(idx)
            await fsm.set_block_data(max_user_id, "news_rss", {"feeds": feeds})
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_rss_menu(max_user_id, max_client, state)
        return True

    if callback_data.startswith("ai:block:news_rss:interval:"):
        mins = int(callback_data.split(":")[4])
        if mins not in (2, 5, 10):
            mins = 5
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        await fsm.set_block_data(max_user_id, "news_rss", {"poll_interval_minutes": mins})
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_rss_menu(max_user_id, max_client, state)
        return True

    if callback_data in ("ai:block:news_rss:rate", "ai:block:news_rss:spacing"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        block = normalize_news_rss((state.get("blocks") or {}).get("news_rss"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "⏳ *Интервал между новостями*\n\n"
                "Минимальная пауза между публикациями в канал.\n"
                "Если за один опрос нашлось несколько новостей — "
                "остальные встают в очередь и выходят по одной.\n\n"
                "«Сразу (пачкой)» — как раньше, до 10 за один опрос."
            ),
            attachments=[InlineKeyboardBuilder.ai_news_rss_spacing_select(block)],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:news_rss:spacing_set:") or callback_data.startswith(
        "ai:block:news_rss:rate_set:"
    ):
        raw = callback_data.split(":")[4]
        try:
            spacing = int(raw)
        except ValueError:
            spacing = 15
        from app.application.pipeline.rss_monitor import RSS_PUBLISH_INTERVAL_PRESETS

        allowed = {0, *RSS_PUBLISH_INTERVAL_PRESETS}
        if spacing not in allowed:
            spacing = 15
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        await fsm.set_block_data(
            max_user_id, "news_rss", {"publish_interval_minutes": spacing}
        )
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_rss_menu(max_user_id, max_client, state)
        return True

    if callback_data == "ai:block:news_rss:window":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        block = normalize_news_rss((state.get("blocks") or {}).get("news_rss"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "🕐 *Окно публикаций (МСК)*\n\n"
                "Новости публикуются только в выбранном интервале.\n"
                "«Без окна (круглосуточно)» — в любое время суток.\n"
                "Одинаковые края своего диапазона (например `00:00-00:00`) "
                "тоже отключают окно."
            ),
            attachments=[InlineKeyboardBuilder.ai_news_rss_window_select(block)],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:news_rss:window_set:"):
        # ai:block:news_rss:window_set:09-00:22-00
        parts = callback_data.split(":")
        if len(parts) < 6:
            return True
        from_raw = parts[4].replace("-", ":")
        until_raw = parts[5].replace("-", ":")
        from_msk = parse_hhmm(from_raw, default="09:00")
        until_msk = parse_hhmm(until_raw, default="22:00")
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        await fsm.set_block_data(
            max_user_id,
            "news_rss",
            {"publish_from_msk": from_msk, "publish_until_msk": until_msk},
        )
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_rss_menu(max_user_id, max_client, state)
        return True

    if callback_data == "ai:block:news_rss:window_custom":
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "rss_window", "1", REDIS_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Назад", "ai:block:news_rss:window"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "✏️ *Своё окно (МСК)*\n\n"
                "Пришли диапазон в формате `09:00-22:00`.\n"
                "Одинаковые время = без окна (круглосуточно), "
                "например `00:00-00:00`."
            ),
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:block:news_rss:filters":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        block = normalize_news_rss((state.get("blocks") or {}).get("news_rss"))
        inc = list(block.get("include_keywords") or [])
        exc = list(block.get("exclude_keywords") or [])
        has_keywords = bool(inc or exc)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "🎯 *Тема / фильтр*\n\n"
                f"{_format_keywords_lists(inc, exc)}\n\n"
                "Можно править слова вручную или подобрать заново через ИИ."
            ),
            attachments=[
                InlineKeyboardBuilder.ai_news_rss_filters_menu(has_keywords=has_keywords)
            ],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:block:news_rss:kw:pick_ai":
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "🎯 *Тема фильтра*\n\n"
                "Выбери нишу — ИИ предложит ключевые слова include/exclude.\n"
                "Потом можно утвердить, переделать или править вручную."
            ),
            attachments=[InlineKeyboardBuilder.ai_news_rss_niche_select()],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:block:news_rss:kw:show":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        block = normalize_news_rss((state.get("blocks") or {}).get("news_rss"))
        niche = block.get("niche") or ""
        text = format_keywords_review(
            niche=niche or "custom",
            include=list(block.get("include_keywords") or []),
            exclude=list(block.get("exclude_keywords") or []),
            reason="Текущий сохранённый фильтр.",
        )
        builder = InlineKeyboardBuilder()
        builder.row(("✏️ Править вручную", "ai:block:news_rss:kw:edit_manual"))
        builder.row(("🤖 Подобрать заново", "ai:block:news_rss:kw:pick_ai"))
        builder.row(("Назад", "ai:block:news_rss:filters"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=text,
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:block:news_rss:kw:edit_manual":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        include: list[str] = []
        exclude: list[str] = []
        redis = await get_redis()
        raw = await redis.get(_kw_review_key(max_user_id))
        if raw:
            payload = json.loads(raw)
            include = list(payload.get("include") or [])
            exclude = list(payload.get("exclude") or [])
        else:
            block = normalize_news_rss((state.get("blocks") or {}).get("news_rss"))
            include = list(block.get("include_keywords") or [])
            exclude = list(block.get("exclude_keywords") or [])
        await _prompt_keywords_manual_edit(
            max_user_id,
            max_client,
            include=include,
            exclude=exclude,
        )
        return True

    if callback_data.startswith("ai:block:news_rss:niche:"):
        niche = callback_data.split(":")[4]
        if niche not in NICHE_LABELS:
            return True
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        if niche == "custom":
            redis = await get_redis()
            await claim_text_input(redis, max_user_id, "rss_topic_brief", "1", REDIS_TTL)
            builder = InlineKeyboardBuilder()
            builder.row(("Назад", "ai:block:news_rss:filters"))
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "Опиши тему канала своими словами.\n"
                    "Например: «новости ИИ в медицине для врачей»"
                ),
                attachments=[builder.build()],
            )
            return True

        channel = (
            await channel_repo.get_by_id(state["channel_id"])
            if state.get("channel_id")
            else None
        )
        await _run_keyword_generation(
            max_user_id,
            max_client,
            niche=niche,
            topic_brief=NICHE_LABELS.get(niche, niche),
            channel_title=(channel.title if channel else "") or "",
            channel_topic=(channel.topic if channel else "") or "",
            session=session,
        )
        return True

    if callback_data == "ai:block:news_rss:kw:approve":
        redis = await get_redis()
        raw = await redis.get(_kw_review_key(max_user_id))
        if not raw:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Черновик фильтра истёк. Выбери тему заново.",
                attachments=[InlineKeyboardBuilder.ai_news_rss_niche_select()],
            )
            return True
        payload = json.loads(raw)
        await redis.delete(_kw_review_key(max_user_id))
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        await fsm.set_block_data(
            max_user_id,
            "news_rss",
            {
                "enabled": True,
                "mode": "on_new",
                "niche": payload.get("niche") or "",
                "topic_brief": payload.get("topic_brief") or "",
                "include_keywords": payload.get("include") or [],
                "exclude_keywords": payload.get("exclude") or [],
                "keywords_source": payload.get("source") or "ai",
            },
        )
        await fsm.set_block_data(max_user_id, "schedule", {"enabled": False})
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Фильтр сохранён. Новости без нужных слов публиковаться не будут.",
        )
        await _show_rss_menu(max_user_id, max_client, state)
        return True

    if callback_data == "ai:block:news_rss:kw:regen":
        redis = await get_redis()
        raw = await redis.get(_kw_review_key(max_user_id))
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        niche = "general"
        brief = ""
        if raw:
            payload = json.loads(raw)
            niche = payload.get("niche") or "general"
            brief = payload.get("topic_brief") or ""
        channel = (
            await channel_repo.get_by_id(state["channel_id"])
            if state.get("channel_id")
            else None
        )
        await _run_keyword_generation(
            max_user_id,
            max_client,
            niche=niche,
            topic_brief=brief or NICHE_LABELS.get(niche, niche),
            channel_title=(channel.title if channel else "") or "",
            channel_topic=(channel.topic if channel else "") or "",
            session=session,
        )
        return True

    if callback_data == "ai:block:news_rss:kw:edit_brief":
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "rss_topic_brief", "1", REDIS_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Назад", "ai:block:news_rss:filters"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Напиши новое описание темы — подберу слова заново.",
            attachments=[builder.build()],
        )
        return True

    return False


async def handle_rss_message(max_user_id: int, message_text: str, redis) -> bool:
    if not rss_allowed(max_user_id):
        return False

    window_wait = await redis.get(f"ai_rss_window_wait:{max_user_id}")
    if window_wait:
        await redis.delete(f"ai_rss_window_wait:{max_user_id}")
        async with async_session_factory() as session:
            max_client = MaxAPIHTTPClient()
            try:
                parsed = parse_publish_window_text(message_text)
                if parsed is None:
                    await claim_text_input(redis, max_user_id, "rss_window", "1", REDIS_TTL)
                    builder = InlineKeyboardBuilder()
                    builder.row(("Назад", "ai:block:news_rss:window"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Не понял. Напиши так: `09:00-22:00` (МСК).",
                        attachments=[builder.build()],
                        fmt="markdown",
                    )
                    return True

                from_msk, until_msk = parsed
                fsm = AIStudioFSM()
                state = await fsm.get_state(max_user_id)
                if not state:
                    await _session_expired(max_user_id, max_client)
                    return True
                await fsm.set_block_data(
                    max_user_id,
                    "news_rss",
                    {"publish_from_msk": from_msk, "publish_until_msk": until_msk},
                )
                state = await fsm.get_state(max_user_id)
                await sync_active_pipeline(session, state)
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=(
                        f"Окно сохранено: "
                        f"*{format_publish_window_label(from_msk, until_msk)}*."
                    ),
                    fmt="markdown",
                )
                await _show_rss_menu(max_user_id, max_client, state)
                return True
            finally:
                await max_client.close()

    brief_wait = await redis.get(f"ai_rss_topic_brief_wait:{max_user_id}")
    if brief_wait:
        await redis.delete(f"ai_rss_topic_brief_wait:{max_user_id}")
        async with async_session_factory() as session:
            max_client = MaxAPIHTTPClient()
            channel_repo = SQLAlchemyChannelRepository(session)
            try:
                brief = (message_text or "").strip()
                if len(brief) < 3:
                    await claim_text_input(redis, max_user_id, "rss_topic_brief", "1", REDIS_TTL)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Слишком коротко. Опиши тему чуть подробнее.",
                    )
                    return True
                fsm = AIStudioFSM()
                state = await fsm.get_state(max_user_id)
                if not state:
                    await _session_expired(max_user_id, max_client)
                    return True
                channel = (
                    await channel_repo.get_by_id(state["channel_id"])
                    if state.get("channel_id")
                    else None
                )
                await _run_keyword_generation(
                    max_user_id,
                    max_client,
                    niche="custom",
                    topic_brief=brief,
                    channel_title=(channel.title if channel else "") or "",
                    channel_topic=(channel.topic if channel else "") or "",
                    session=session,
                )
                return True
            finally:
                await max_client.close()

    keywords_wait = await redis.get(f"ai_rss_keywords_edit_wait:{max_user_id}")
    if keywords_wait:
        await redis.delete(f"ai_rss_keywords_edit_wait:{max_user_id}")
        async with async_session_factory() as session:
            max_client = MaxAPIHTTPClient()
            try:
                parsed = parse_keywords_edit_text(message_text, allow_plain_include=True)
                if parsed is None:
                    await claim_text_input(
                        redis, max_user_id, "rss_keywords_edit", "1", REDIS_TTL
                    )
                    builder = InlineKeyboardBuilder()
                    builder.row(("Назад", "ai:block:news_rss:filters"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Не понял формат. Пришли так:\n\n"
                            "`+: слово1, слово2`\n"
                            "`-: слово3, слово4`"
                        ),
                        attachments=[builder.build()],
                        fmt="markdown",
                    )
                    return True

                new_inc, new_exc = parsed
                fsm = AIStudioFSM()
                state = await fsm.get_state(max_user_id)
                if not state:
                    await _session_expired(max_user_id, max_client)
                    return True

                block = normalize_news_rss((state.get("blocks") or {}).get("news_rss"))
                # Prefer draft from AI review if present, else saved block
                cur_inc = list(block.get("include_keywords") or [])
                cur_exc = list(block.get("exclude_keywords") or [])
                niche = block.get("niche") or "custom"
                topic_brief = block.get("topic_brief") or ""
                raw_review = await redis.get(_kw_review_key(max_user_id))
                if raw_review:
                    payload = json.loads(raw_review)
                    cur_inc = list(payload.get("include") or cur_inc)
                    cur_exc = list(payload.get("exclude") or cur_exc)
                    niche = payload.get("niche") or niche
                    topic_brief = payload.get("topic_brief") or topic_brief
                    await redis.delete(_kw_review_key(max_user_id))

                include = new_inc if new_inc is not None else cur_inc
                exclude = new_exc if new_exc is not None else cur_exc

                await fsm.set_block_data(
                    max_user_id,
                    "news_rss",
                    {
                        "enabled": True,
                        "mode": "on_new",
                        "niche": niche or "custom",
                        "topic_brief": topic_brief,
                        "include_keywords": include,
                        "exclude_keywords": exclude,
                        "keywords_source": "manual",
                    },
                )
                await fsm.set_block_data(max_user_id, "schedule", {"enabled": False})
                state = await fsm.get_state(max_user_id)
                await sync_active_pipeline(session, state)
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=(
                        "Фильтр сохранён вручную.\n\n"
                        f"{_format_keywords_lists(include, exclude)}"
                    ),
                    fmt="markdown",
                )
                await _show_rss_menu(max_user_id, max_client, state)
                return True
            finally:
                await max_client.close()

    waiting_site = await redis.get(f"ai_rss_site_wait:{max_user_id}")
    if waiting_site:
        await redis.delete(f"ai_rss_site_wait:{max_user_id}")
        async with async_session_factory() as session:
            max_client = MaxAPIHTTPClient()
            try:
                if not _looks_like_url(message_text):
                    await claim_text_input(redis, max_user_id, "rss_site", "1", REDIS_TTL)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Это не похоже на ссылку. Пришли URL вида https://example.com/news",
                    )
                    return True

                url = _normalize_feed_url(message_text)
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Проверяю сайт (ищу RSS или список новостей)...",
                )
                result = await resolve_site_add(url)

                fsm = AIStudioFSM()
                state = await fsm.get_state(max_user_id)
                if not state:
                    await _session_expired(max_user_id, max_client)
                    return True

                block = normalize_news_rss((state.get("blocks") or {}).get("news_rss"))
                feeds = list(block.get("feeds") or [])
                sites = list(block.get("sites") or [])
                if result.mode == "feed":
                    if result.stored_url not in feeds:
                        feeds.append(result.stored_url)
                    await fsm.set_block_data(
                        max_user_id,
                        "news_rss",
                        {"feeds": feeds, "enabled": True, "mode": "on_new"},
                    )
                else:
                    if result.stored_url not in sites:
                        sites.append(result.stored_url)
                    await fsm.set_block_data(
                        max_user_id,
                        "news_rss",
                        {"sites": sites, "enabled": True, "mode": "on_new"},
                    )
                await fsm.set_block_data(max_user_id, "schedule", {"enabled": False})
                state = await fsm.get_state(max_user_id)
                await sync_active_pipeline(session, state)
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=result.message,
                    fmt="markdown",
                )
                await _show_rss_menu(max_user_id, max_client, state)
                return True
            finally:
                await max_client.close()

    waiting = await redis.get(f"ai_rss_feed_wait:{max_user_id}")
    if not waiting:
        return False

    await redis.delete(f"ai_rss_feed_wait:{max_user_id}")

    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        channel_repo = SQLAlchemyChannelRepository(session)
        try:
            if not _looks_like_url(message_text):
                await claim_text_input(redis, max_user_id, "rss_feed", "1", REDIS_TTL)
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Это не похоже на ссылку. Пришли URL вида https://example.com/rss",
                )
                return True

            url = _normalize_feed_url(message_text)
            from app.application.pipeline.rss_monitor import fetch_feed

            items = await fetch_feed(url)
            warn = (
                f"Ок, лента читается ({len(items)} записей)."
                if items
                else "Ленту не удалось прочитать сейчас (или пустая). Ссылку сохранил."
            )

            fsm = AIStudioFSM()
            state = await fsm.get_state(max_user_id)
            if not state:
                await _session_expired(max_user_id, max_client)
                return True

            feeds = list(
                normalize_news_rss((state.get("blocks") or {}).get("news_rss")).get("feeds")
                or []
            )
            if url not in feeds:
                feeds.append(url)
            await fsm.set_block_data(
                max_user_id,
                "news_rss",
                {"feeds": feeds, "enabled": True, "mode": "on_new"},
            )
            await fsm.set_block_data(max_user_id, "schedule", {"enabled": False})
            state = await fsm.get_state(max_user_id)
            await sync_active_pipeline(session, state)
            await max_client.send_message_to_user(user_id=max_user_id, text=warn)
            await _show_rss_menu(max_user_id, max_client, state)
            return True
        finally:
            await max_client.close()
