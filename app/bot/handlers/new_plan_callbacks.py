import json

from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.handlers.channel_setup_flow import REDIS_TTL, _show_slot_time_picker
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient


def register_new_plan_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["setupplan:", "newplan:", "customplan:", "planfreq:", "plantime:"])
    async def on_new_plan_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {}) or update.get("user", {}) or update.get("message", {}).get("sender", {})
        max_user_id = user_data.get("user_id")

        if not callback_data or not max_user_id:
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            channel_repo = SQLAlchemyChannelRepository(session)
            subscription_repo = SQLAlchemySubscriptionRepository(session)
            max_client = MaxAPIHTTPClient()

            user = await user_repo.get_by_max_user_id(max_user_id) if max_user_id else None
            user_id = user.id if user else None

            channels_count = await channel_repo.count_by_owner(user_id) if user_id else 0
            subscription = await subscription_repo.get_active_by_user(user_id) if user_id else None
            channels_limit = subscription.channels_limit if subscription else 0

            async def _owns_channel(channel_id: int) -> bool:
                if not user_id:
                    return False
                channel = await channel_repo.get_by_id(channel_id)
                return bool(channel and channel.owner_id == user_id)

            try:
                if callback_data.startswith("newplan:ai:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        return
                    await _start_plan_flow(channel_id, max_user_id, max_client)

                elif callback_data.startswith("newplan:search:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        return
                    await _start_plan_flow(channel_id, max_user_id, max_client, search_enabled=True)

                elif callback_data.startswith("newplan:custom:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        return
                    redis_local = await get_redis()
                    await redis_local.setex(f"custom_plan:{max_user_id}", REDIS_TTL, str(channel_id))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Сначала выбери длительность плана:",
                        attachments=[InlineKeyboardBuilder()
                            .row(("7 дней", f"customplan:days:{channel_id}:7"))
                            .row(("14 дней", f"customplan:days:{channel_id}:14"))
                            .row(("30 дней", f"customplan:days:{channel_id}:30"))
                            .row(("90 дней", f"customplan:days:{channel_id}:90"))
                            .row(("Отмена", "main_menu"))
                            .build()],
                    )

                elif callback_data.startswith("customplan:days:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[2])
                    days = int(parts[3])
                    redis_local = await get_redis()
                    await redis_local.setex(f"custom_plan:{max_user_id}", REDIS_TTL, f"{channel_id}:{days}")
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Отправь список тем (каждая с новой строки):",
                        attachments=[InlineKeyboardBuilder()
                            .row(("Отмена", "main_menu"))
                            .build()],
                    )

                elif callback_data.startswith("newplan:start:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        return
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери способ создания плана:",
                        attachments=[InlineKeyboardBuilder.plan_creation_method(channel_id)],
                    )

                elif callback_data.startswith("setupplan:days:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[2])
                    days = int(parts[3])
                    redis_local = await get_redis()
                    search_str = await redis_local.get(f"newplan_search:{max_user_id}")
                    search_enabled = search_str == "true" if search_str else False
                    await redis_local.delete(f"newplan_search:{max_user_id}")

                    prefs = {
                        "channel_id": channel_id,
                        "days": days,
                        "subscribe_cta": False,
                        "share_cta": False,
                        "comments_enabled": False,
                        "search_enabled": search_enabled,
                        "show_sources": False,
                        "review_enabled": False,
                    }
                    await redis_local.setex(f"content_plan_prefs:{max_user_id}", REDIS_TTL, json.dumps(prefs))

                    builder = InlineKeyboardBuilder()
                    for label, key in [("3 раза в день", "3x_day"), ("2 раза в день", "2x_day"), ("1 раз в день", "daily"),
                                        ("2 раза в неделю", "2x_week"), ("1 раз в неделю", "weekly")]:
                        builder.row((label, f"planfreq:{channel_id}:{days}:{key}"))
                    builder.row(("На главную", "main_menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери частоту публикаций:",
                        attachments=[builder.build()],
                    )

                elif callback_data.startswith("planfreq:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[1])
                    days = int(parts[2])
                    freq_key = parts[3]
                    redis_local = await get_redis()
                    await redis_local.setex(f"planflow_freq:{max_user_id}", REDIS_TTL,
                        json.dumps({"channel_id": channel_id, "days": days, "freq": freq_key}))

                    ch = await channel_repo.get_by_id(channel_id)
                    ch_title = ch.title if ch else ""
                    if freq_key in ("2x_day", "3x_day"):
                        slots = {"2x_day": 2, "3x_day": 3}[freq_key]
                        await redis_local.setex(f"setup_slots:{max_user_id}", REDIS_TTL,
                            json.dumps({"ch_id": channel_id, "slot": 0, "total": slots, "times": [], "flow": "plan"}))
                        await _show_slot_time_picker(max_client, max_user_id, channel_id, 0, slots)
                    else:
                        builder = InlineKeyboardBuilder()
                        builder.row(("12:00 МСК", f"plantime:{channel_id}:{days}:12"), ("15:00 МСК", f"plantime:{channel_id}:{days}:15"))
                        builder.row(("18:00 МСК", f"plantime:{channel_id}:{days}:18"), ("21:00 МСК", f"plantime:{channel_id}:{days}:21"))
                        builder.row(("🕐 Своё время", f"plantime:custom:{channel_id}:{days}"))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"{ch_title} — в какое время публиковать посты?",
                            attachments=[builder.build()],
                        )

                elif callback_data.startswith("plantime:custom:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[2])
                    days = int(parts[3])
                    redis_local = await get_redis()
                    await redis_local.setex(f"plantime_custom:{max_user_id}", REDIS_TTL,
                        json.dumps({"channel_id": channel_id, "days": days}))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Напиши время в формате ЧЧ:ММ (по Москве).\nНапример: 14:30",
                    )

                elif callback_data.startswith("plantime:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[2])
                    days = int(parts[3])
                    hour_msk = int(parts[4])
                    hour_utc = (hour_msk - 3) % 24
                    await _finish_plan_flow(max_user_id, channel_id, days, f"{hour_utc:02d}:00", max_client)

            except Exception:
                logger.exception(f"Error handling callback: {callback_data}")
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Произошла ошибка. Попробуй ещё раз позже.",
                    attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                )

            await max_client.close()
            await session.commit()


async def _start_plan_flow(channel_id: int, max_user_id: int, max_client, search_enabled: bool = False):
    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
    from app.infrastructure.redis.client import get_redis as _get_redis
    import json as json_mod

    async with async_session_factory() as session:
        ch_repo = SQLAlchemyChannelRepository(session)
        ch = await ch_repo.get_by_id(channel_id)
        if not ch:
            return
        redis_local = await _get_redis()
        await redis_local.setex(f"newplan_search:{max_user_id}", 600, str(search_enabled).lower())
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"*{ch.title}* — новый контент-план\n\nВыбери период:",
            attachments=[InlineKeyboardBuilder()
                .row(("7 дней", f"setupplan:days:{channel_id}:7"))
                .row(("14 дней", f"setupplan:days:{channel_id}:14"))
                .row(("30 дней", f"setupplan:days:{channel_id}:30"))
                .row(("90 дней", f"setupplan:days:{channel_id}:90"))
                .row(("Отмена", "main_menu"))
                .build()],
            fmt="markdown",
        )


async def _finish_plan_flow(max_user_id, channel_id, days, time_str, max_client, freq_key=None, times_list=None):
    from app.infrastructure.redis.client import get_redis as _get_redis
    from app.bot.handlers.content_plan import _settings_text

    redis_local = await _get_redis()

    prefs_raw = await redis_local.get(f"content_plan_prefs:{max_user_id}")
    if not prefs_raw:
        return
    prefs = json.loads(prefs_raw)

    if not freq_key:
        flow_data = await redis_local.get(f"planflow_freq:{max_user_id}")
        if flow_data:
            fd = json.loads(flow_data)
            freq_key = fd.get("freq", "daily")
            await redis_local.delete(f"planflow_freq:{max_user_id}")
    if not freq_key:
        freq_key = "daily"

    if time_str:
        prefs["default_time"] = time_str
    if times_list:
        prefs["default_times"] = times_list
    prefs["frequency"] = freq_key

    await redis_local.setex(f"content_plan_prefs:{max_user_id}", REDIS_TTL, json.dumps(prefs))
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=_settings_text(prefs),
        attachments=[InlineKeyboardBuilder.plan_settings(prefs)],
        fmt="markdown",
    )
