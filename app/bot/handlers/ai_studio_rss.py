import json
from urllib.parse import urlparse

from app.application.pipeline.rss_monitor import (
    NICHE_LABELS,
    format_keywords_review,
    format_publish_window_label,
    generate_keywords_for_topic,
    normalize_news_rss,
    parse_hhmm,
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
) -> None:
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text="🤖 Подбираю ключевые слова под тему...",
    )
    openai_client = OpenAIService()
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
                "Ночью бот не публикует. Новости ждут утра "
                "(срок свежести — до 24 часов)."
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
                "Одинаковые время = круглосуточно, например `00:00-00:00`."
            ),
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:block:news_rss:filters":
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "🎯 *Тема фильтра*\n\n"
                "Выбери нишу — ИИ предложит ключевые слова include/exclude.\n"
                "Потом можно утвердить или переделать."
            ),
            attachments=[InlineKeyboardBuilder.ai_news_rss_niche_select()],
            fmt="markdown",
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
                )
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
