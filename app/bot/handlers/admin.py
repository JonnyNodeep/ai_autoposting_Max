import asyncio

from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.usage_stats_repository import UsageStatsRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_costs_client import OpenAICostsClient
from app.config import settings

_FREQ_NAMES = {
    "daily": "1×/день",
    "2x_day": "2×/день",
    "3x_day": "3×/день",
    "2x_week": "2×/нед",
    "weekly": "1×/нед",
}

_PERIOD_LABELS = {1: "сутки", 7: "неделю", 30: "месяц"}


def register_admin_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["admin:"])
    async def on_admin_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {}) or update.get("user", {})
        max_user_id_raw = user_data.get("user_id") or user_data.get("id") or user_data.get("userId")
        try:
            max_user_id = int(max_user_id_raw) if max_user_id_raw is not None else 0
        except (TypeError, ValueError):
            max_user_id = 0

        if not callback_data or not max_user_id:
            return

        if max_user_id != settings.admin.max_user_id:
            return

        async with async_session_factory() as session:
            max_client = MaxAPIHTTPClient()
            user_repo = SQLAlchemyUserRepository(session)
            channel_repo = SQLAlchemyChannelRepository(session)
            sub_repo = SQLAlchemySubscriptionRepository(session)
            stats_repo = UsageStatsRepository(session)
            _ = (user_repo, sub_repo)

            try:
                if callback_data == "admin:stats":
                    stats = await stats_repo.get_stats()
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"*📊 Статистика*\n\n"
                            f"Пользователей: {stats['total_users']}\n"
                            f"Подписок: {stats['active_subscriptions']} "
                            f"(Solo: {stats['by_tier']['solo']}, "
                            f"Creator: {stats['by_tier']['creator']}, "
                            f"Studio: {stats['by_tier']['studio']})\n"
                            f"Каналов: {stats['total_channels']}\n"
                            f"Постов всего: {stats['total_posts']} (опубл: {stats['published_posts']})\n"
                            f"Постов за неделю: {stats['posts_this_week']}\n"
                            f"Платежей: {stats['month_payments']}/мес\n"
                            f"Доход: {stats['month_revenue']}₽/мес"
                        ),
                        attachments=[_admin_menu()],
                        fmt="markdown",
                    )

                elif callback_data == "admin:users":
                    users = await stats_repo.get_all_users(20)
                    lines = []
                    builder = InlineKeyboardBuilder()
                    for u in users:
                        active = "✅" if u.is_active else "🚫"
                        lines.append(f"{active} {u.first_name or '?'} ({u.username or 'id:' + str(u.max_user_id)})")
                    builder.row(("Назад в админку", "admin:menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="*Пользователи:*\n\n" + "\n".join(lines),
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data == "admin:subscriptions":
                    subs = await stats_repo.get_all_subscriptions(20)
                    lines = []
                    builder = InlineKeyboardBuilder()
                    for s in subs:
                        lines.append(f"user_{s.user_id}: {s.tier} — {s.status} до {s.expires_at.strftime('%d.%m') if s.expires_at else '?'}")
                    builder.row(("Назад в админку", "admin:menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="*Подписки:*\n\n" + "\n".join(lines),
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data == "admin:channels":
                    channels = await channel_repo.get_all()
                    pipe_repo = SQLAPipelineRunRepository(session)
                    active_runs = {
                        run.channel_id: run for run in await pipe_repo.get_all_active()
                    }
                    lines = [f"*📡 Каналы и расписание* ({len(channels)})", ""]
                    if not channels:
                        lines.append("Каналов пока нет.")
                    else:
                        for ch in channels:
                            lines.append(_format_channel_schedule_line(ch, active_runs.get(ch.id)))
                    builder = InlineKeyboardBuilder()
                    builder.row(("Назад в админку", "admin:menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="\n".join(lines),
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data == "admin:channels_stats" or callback_data.startswith("admin:channels_stats:"):
                    days = 1
                    if callback_data.startswith("admin:channels_stats:"):
                        try:
                            days = int(callback_data.rsplit(":", 1)[-1])
                        except ValueError:
                            days = 1
                    if days not in (1, 7, 30):
                        days = 1
                    text = await _format_channels_stats(channel_repo, stats_repo, max_client, days)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=text,
                        attachments=[_channels_stats_menu(days)],
                        fmt="markdown",
                    )

                elif callback_data == "admin:logs":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Логи доступны через API: GET /api/admin/logs\n(в разработке)",
                        attachments=[_admin_menu()],
                    )

                elif callback_data == "admin:costs" or callback_data.startswith("admin:costs:"):
                    days = 30
                    if callback_data.startswith("admin:costs:"):
                        try:
                            days = int(callback_data.rsplit(":", 1)[-1])
                        except ValueError:
                            days = 30
                    if days not in (1, 7, 30):
                        days = 30
                    text = await _format_openai_costs(stats_repo, days)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=text,
                        attachments=[_costs_menu(days)],
                        fmt="markdown",
                    )

                elif callback_data == "admin:menu":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="*🛠 Админ-панель*",
                        attachments=[_admin_menu()],
                        fmt="markdown",
                    )

            except Exception:
                logger.exception(f"Error handling admin callback: {callback_data}")

            await max_client.close()


async def _sum_participants(max_client: MaxAPIHTTPClient, chat_ids: list[int]) -> tuple[int, int]:
    """Return (total_subscribers, channels_ok)."""
    if not chat_ids:
        return 0, 0

    sem = asyncio.Semaphore(8)

    async def one(chat_id: int) -> tuple[int, bool]:
        async with sem:
            try:
                chat = await max_client.get_chat(chat_id)
                return int(chat.get("participants_count") or 0), True
            except Exception:
                logger.warning(f"Failed to fetch participants_count for chat_id={chat_id}")
                return 0, False

    results = await asyncio.gather(*(one(cid) for cid in chat_ids))
    total = sum(count for count, _ in results)
    ok = sum(1 for _, success in results if success)
    return total, ok


async def _format_channels_stats(channel_repo, stats_repo, max_client, days: int) -> str:
    channels = await channel_repo.get_all()
    period = _PERIOD_LABELS.get(days, f"{days} дн.")
    events = await stats_repo.get_member_event_counts(days)
    by_channel = await stats_repo.get_member_event_counts_by_channel(days, limit=8)

    chat_ids = [ch.max_chat_id for ch in channels if ch.max_chat_id]
    total_subs, ok = await _sum_participants(max_client, chat_ids)

    lines = [
        f"*📡 Подписчики каналов*",
        "",
        f"Каналов: {len(channels)}",
        f"Всего подписчиков: {total_subs:,}".replace(",", " "),
    ]
    if ok < len(chat_ids):
        lines.append(f"(данные MAX API: {ok}/{len(chat_ids)} каналов)")

    lines.extend(
        [
            "",
            f"*За {period}:*",
            f"+{events['joined']} подписалось",
            f"−{events['left']} отписалось",
            f"Нетто: {events['net']:+d}",
        ]
    )

    if by_channel:
        lines.append("")
        lines.append("*Топ по приросту:*")
        for row in by_channel[:5]:
            lines.append(
                f"• {row['title']}: {row['net']:+d} (+{row['joined']}/−{row['left']})"
            )
    else:
        lines.append("")
        lines.append("_Пока нет событий подписки/отписки за период._")

    return "\n".join(lines)


async def _format_openai_costs(stats_repo: UsageStatsRepository, days: int) -> str:
    period = _PERIOD_LABELS.get(days, f"{days} дн.")
    costs_client = OpenAICostsClient()
    if costs_client.configured:
        try:
            costs = await costs_client.get_costs(days)
            lines = [
                f"*💰 Расходы OpenAI (за {period})*",
                "",
                f"Стоимость: ${costs['total_cost']:.2f}",
                f"Источник: Costs API",
            ]
            by_item = costs.get("by_line_item") or {}
            if by_item:
                lines.append("")
                lines.append("*По категориям:*")
                for name, value in by_item.items():
                    lines.append(f"• {name}: ${value:.2f}")
            return "\n".join(lines)
        except Exception:
            logger.exception("OpenAI Costs API failed, falling back to generation_logs")

    costs = await stats_repo.get_openai_costs(days)
    return (
        f"*💰 Расходы OpenAI (за {period})*\n\n"
        f"Операций: {costs['total_operations']}\n"
        f"Токенов: {costs['total_tokens']:,}\n"
        f"Оценка: ${costs['total_cost']:.2f}\n"
        f"_Источник: локальные логи (Admin API key недоступен)_"
    )


def _utc_times_to_msk(times: list[str] | None) -> str:
    if not times:
        return ""
    msk_times = []
    for t in times:
        parts = str(t).split(":")
        try:
            h = (int(parts[0]) + 3) % 24
        except (ValueError, IndexError):
            continue
        m = parts[1] if len(parts) > 1 else "00"
        msk_times.append(f"{h:02d}:{m}")
    return ", ".join(msk_times)


def _format_channel_schedule_line(channel, run) -> str:
    title = channel.title or f"id:{channel.id}"
    if not run:
        freq = channel.content_frequency
        freq_label = _FREQ_NAMES.get(freq, freq) if freq else "—"
        return f"• {title} — ⏸ пайплайн выкл ({freq_label})"

    blocks = run.blocks_config or {}
    rss = blocks.get("news_rss") or {}
    schedule = blocks.get("schedule") or {}
    rss_on = bool(rss.get("enabled")) and bool(rss.get("feeds"))
    times = list(run.times or schedule.get("times") or [])
    sched_on = bool(times) or bool(schedule.get("enabled"))

    parts: list[str] = []
    if rss_on:
        interval = rss.get("poll_interval_minutes", 5)
        feeds_n = len(list(rss.get("feeds") or []))
        parts.append(f"RSS · {feeds_n} лент / {interval} мин")
    if sched_on and times:
        freq = run.frequency or schedule.get("frequency") or "daily"
        freq_label = _FREQ_NAMES.get(freq, freq)
        msk = _utc_times_to_msk(times)
        parts.append(f"{freq_label} · {msk} МСК" if msk else freq_label)
    elif sched_on and not times:
        freq = run.frequency or schedule.get("frequency") or channel.content_frequency
        freq_label = _FREQ_NAMES.get(freq, freq) if freq else "—"
        parts.append(f"{freq_label} · время не задано")

    detail = " · ".join(parts) if parts else "без триггера"
    return f"• {title} — 🟢 {detail}"


def _period_buttons(prefix: str, active_days: int) -> list[tuple[str, str]]:
    labels = ((1, "Сутки"), (7, "Неделя"), (30, "Месяц"))
    buttons = []
    for days, label in labels:
        mark = "✓ " if days == active_days else ""
        buttons.append((f"{mark}{label}", f"{prefix}:{days}"))
    return buttons


def _channels_stats_menu(active_days: int = 1) -> dict:
    builder = InlineKeyboardBuilder()
    builder.row(*_period_buttons("admin:channels_stats", active_days))
    builder.row(("Назад в админку", "admin:menu"))
    return builder.build()


def _costs_menu(active_days: int = 30) -> dict:
    builder = InlineKeyboardBuilder()
    builder.row(*_period_buttons("admin:costs", active_days))
    builder.row(("Назад в админку", "admin:menu"))
    return builder.build()


def _admin_menu() -> dict:
    return (
        InlineKeyboardBuilder()
        .row(("📊 Статистика", "admin:stats"))
        .row(("👥 Пользователи", "admin:users"))
        .row(("📋 Подписки", "admin:subscriptions"))
        .row(("📡 Каналы и расписание", "admin:channels"))
        .row(("📈 Подписчики каналов", "admin:channels_stats"))
        .row(("💰 Расходы OpenAI", "admin:costs"))
        .row(("📝 Логи", "admin:logs"))
        .row(("На главную", "main_menu"))
        .build()
    )
