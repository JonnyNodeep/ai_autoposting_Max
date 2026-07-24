from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.usage_stats_repository import UsageStatsRepository
from app.infrastructure.repositories.content_repository import (
    SQLAContentPlanRepository,
    SQLAContentTopicRepository,
    SQLAContentPostRepository,
)
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService
from app.application.content.generate_content import GeneratePostUseCase, GenerateImageForPostUseCase
from app.domain.entities.content_plan import PlanStatus
from app.config import settings


TESTPOST_DEDUP_TTL_SECONDS = 24 * 60 * 60


def register_admin_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK)
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

                elif callback_data == "admin:plans":
                    channels = await channel_repo.get_all()
                    active_channels = []
                    for ch in channels:
                        plan_repo = SQLAContentPlanRepository(session)
                        plans = await plan_repo.get_by_channel(ch.id)
                        if [p for p in plans if p.status != PlanStatus.COMPLETED]:
                            active_channels.append(ch)

                    if not active_channels:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет каналов с активными планами.",
                            attachments=[_admin_menu()],
                        )
                        return

                    builder = InlineKeyboardBuilder()
                    for ch in active_channels:
                        builder.row((f"{ch.title[:40]}", f"admin:plan_channel:{ch.id}"))
                    builder.row(("Назад в админку", "admin:menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="*Каналы с активными планами:*",
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("admin:plan_channel:"):
                    ch_id = int(callback_data.split(":")[2])
                    ch = await channel_repo.get_by_id(ch_id)
                    if not ch:
                        return
                    plan_repo = SQLAContentPlanRepository(session)
                    plans = await plan_repo.get_by_channel(ch_id)
                    active_plans = [p for p in plans if p.status != PlanStatus.COMPLETED]
                    if not active_plans:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"Нет активных планов в канале *{ch.title}*.",
                            attachments=[_admin_menu()],
                            fmt="markdown",
                        )
                        return
                    builder = InlineKeyboardBuilder()
                    for p in active_plans:
                        builder.row((f"{p.duration_days} дн. (от {p.created_at.strftime('%d.%m') if p.created_at else '?'})", f"admin:plan:{p.id}"))
                    builder.row(("Назад к каналам", "admin:plans"))
                    builder.row(("Назад в админку", "admin:menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"*Планы канала «{ch.title}»:*",
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("admin:plan:"):
                    plan_id = int(callback_data.split(":")[2])
                    topic_repo = SQLAContentTopicRepository(session)
                    topics = await topic_repo.get_by_plan(plan_id)
                    if not topics:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="В этом плане нет тем.",
                            attachments=[_admin_menu()],
                        )
                        return
                    from app.domain.entities.content_post import PostStatus
                    from app.infrastructure.models.content_post import ContentPostModel
                    from sqlalchemy import select
                    topic_ids = [t.id for t in topics]
                    published_topic_ids: set[int] = set()
                    if topic_ids:
                        stmt = select(ContentPostModel.topic_id).where(
                            ContentPostModel.topic_id.in_(topic_ids),
                            ContentPostModel.status == PostStatus.PUBLISHED.value,
                        )
                        result = await session.execute(stmt)
                        published_topic_ids = {row[0] for row in result.fetchall()}

                    plan_repo = SQLAContentPlanRepository(session)
                    plan = await plan_repo.get_by_id(plan_id)
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None

                    lines = [f"*Канал:* {ch.title if ch else '?'}", f"*Длительность:* {plan.duration_days} дн.\n", "*Темы:*"]
                    for i, t in enumerate(topics):
                        status_icon = "✅" if t.id in published_topic_ids else "⏳"
                        lines.append(f"{status_icon} {i+1}. {t.topic[:60]}")
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="\n".join(lines),
                        attachments=[_admin_menu()],
                        fmt="markdown",
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

                elif callback_data == "admin:testpost":
                    user = await user_repo.get_by_max_user_id(max_user_id)
                    channels = await channel_repo.get_by_owner(user.id) if user else []
                    if not channels:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет каналов для теста. Добавь канал.",
                            attachments=[_admin_menu()],
                        )
                        return
                    builder = InlineKeyboardBuilder()
                    for ch in channels:
                        builder.row((ch.title[:40], f"admin:testpost_channel:{ch.id}"))
                    builder.row(("Назад в админку", "admin:menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери канал для тестового поста:",
                        attachments=[builder.build()],
                    )

                elif callback_data.startswith("admin:testpost_channel:"):
                    ch_id = int(callback_data.split(":")[2])
                    ch = await channel_repo.get_by_id(ch_id)
                    if not ch:
                        return
                    plan_repo = SQLAContentPlanRepository(session)
                    plans = await plan_repo.get_by_channel(ch_id)
                    active_plans = [p for p in plans if p.status != PlanStatus.COMPLETED]
                    if not active_plans:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"Нет активных планов для канала *{ch.title}*.",
                            attachments=[_admin_menu()],
                            fmt="markdown",
                        )
                        return
                    builder = InlineKeyboardBuilder()
                    for p in active_plans:
                        builder.row((f"План на {p.duration_days} дн.", f"admin:testpost_plan:{p.id}"))
                    builder.row(("Назад в админку", "admin:menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Канал *{ch.title}* — выбери план:",
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("admin:testpost_plan:"):
                    plan_id = int(callback_data.split(":")[2])
                    topic_repo = SQLAContentTopicRepository(session)
                    topics = await topic_repo.get_by_plan(plan_id)
                    if not topics:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет тем в этом плане.",
                            attachments=[_admin_menu()],
                        )
                        return
                    builder = InlineKeyboardBuilder()
                    for t in topics:
                        builder.row((t.topic[:50], f"admin:testpost_topic:{t.id}"))
                    builder.row(("Назад в админку", "admin:menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери тему для тестового поста:",
                        attachments=[builder.build()],
                    )

                elif callback_data.startswith("admin:testpost_topic:"):
                    topic_id = int(callback_data.split(":")[2])

                    callback_id = cb.get("callback_id", "")
                    if callback_id:
                        from app.infrastructure.redis.client import get_redis
                        redis = await get_redis()
                        dedup_key = f"dedup:admin:testpost:{callback_id}"
                        is_first_delivery = await redis.set(
                            dedup_key,
                            "1",
                            ex=TESTPOST_DEDUP_TTL_SECONDS,
                            nx=True,
                        )
                        if not is_first_delivery:
                            return

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Генерирую тестовый пост...",
                    )
                    channel_repo2 = SQLAlchemyChannelRepository(session)
                    topic_repo = SQLAContentTopicRepository(session)
                    post_repo = SQLAContentPostRepository(session)
                    openai_client = OpenAIService()
                    uc = GeneratePostUseCase(channel_repo2, post_repo, topic_repo, openai_client)
                    post = await uc.execute(topic_id)
                    await session.commit()
                    img_uc = GenerateImageForPostUseCase(post_repo, openai_client, max_client)
                    topic = await topic_repo.get_by_id(topic_id)
                    plan_repo3 = SQLAContentPlanRepository(session)
                    plan = await plan_repo3.get_by_id(topic.plan_id) if topic else None
                    ch2 = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    await img_uc.execute(post.id, ch2.channel_link if ch2 else None)
                    await session.commit()
                    post = await post_repo.get_by_id(post.id)
                    attachments = []
                    if post.image_url:
                        payload = {"token": post.image_url} if "/app/uploads/" not in (post.image_url or "") else {"url": post.image_url}
                        attachments.append({"type": "image", "payload": payload})
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"*{post.title}*\n\n{post.text}\n\n_{post.cta}_",
                        attachments=attachments if attachments else None,
                        fmt="markdown",
                    )
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Это тестовый пост. Он не опубликован в канал.",
                        attachments=[_admin_menu()],
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
        .row(("📋 Контент-планы", "admin:plans"))
        .row(("💰 Расходы OpenAI", "admin:costs"))
        .row(("📝 Логи", "admin:logs"))
        .row(("🧪 Тестовый пост", "admin:testpost"))
        .row(("На главную", "main_menu"))
        .build()
    )
