from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.usage_stats_repository import UsageStatsRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.config import settings


def register_admin_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["admin:"])
    async def on_admin_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {}) or update.get("user", {})
        max_user_id = user_data.get("user_id") or 0

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
            _ = (user_repo, channel_repo, sub_repo)

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

                elif callback_data == "admin:logs":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Логи доступны через API: GET /api/admin/logs\n(в разработке)",
                        attachments=[_admin_menu()],
                    )

                elif callback_data == "admin:costs":
                    costs = await stats_repo.get_openai_costs(30)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"*💰 Расходы OpenAI (30 дней)*\n\n"
                            f"Операций: {costs['total_operations']}\n"
                            f"Токенов: {costs['total_tokens']:,}\n"
                            f"Стоимость: ${costs['total_cost']:.2f}"
                        ),
                        attachments=[_admin_menu()],
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


def _admin_menu() -> dict:
    return (
        InlineKeyboardBuilder()
        .row(("📊 Статистика", "admin:stats"))
        .row(("👥 Пользователи", "admin:users"))
        .row(("📋 Подписки", "admin:subscriptions"))
        .row(("💰 Расходы OpenAI", "admin:costs"))
        .row(("📝 Логи", "admin:logs"))
        .row(("На главную", "main_menu"))
        .build()
    )
