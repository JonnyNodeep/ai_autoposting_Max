from datetime import datetime, UTC, timedelta

from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.domain.entities.subscription import Subscription
from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.domain.value_objects.subscription_status import SubscriptionStatus


TIER_NAMES = {
    "solo": "Solo — 1 канал",
    "creator": "Creator — до 3 каналов",
    "studio": "Studio — до 10 каналов",
}

TIER_PRICES = {
    "solo": {"amount": 990, "period_days": 30, "label": "Solo (990₽/мес)"},
    "creator": {"amount": 2490, "period_days": 30, "label": "Creator (2490₽/мес)"},
    "studio": {"amount": 4990, "period_days": 30, "label": "Studio (4990₽/мес)"},
}


def register_subscription_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["subscription:"])
    async def on_subscription_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {})
        max_user_id = user_data.get("user_id")

        if not callback_data or not max_user_id:
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            subscription_repo = SQLAlchemySubscriptionRepository(session)
            max_client = MaxAPIHTTPClient()

            user = await user_repo.get_by_max_user_id(max_user_id) if max_user_id else None
            user_id = user.id if user else None

            try:
                if callback_data == "subscription:status":
                    if not user_id:
                        return

                    sub = await subscription_repo.get_active_by_user(user_id)
                    if not sub:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="У тебя нет активной подписки.",
                            attachments=[InlineKeyboardBuilder.main_menu()],
                        )
                        return

                    tier_name = TIER_NAMES.get(sub.tier.value, sub.tier.value)
                    expires = sub.expires_at.strftime("%d.%m.%Y") if sub.expires_at else "?"

                    builder = InlineKeyboardBuilder()
                    for t_key in ["solo", "creator", "studio"]:
                        price = TIER_PRICES[t_key]
                        if sub.tier.value == t_key:
                            builder.row((f"✅ {price['label']} (текущий)", "none"))
                        else:
                            builder.row((f"⬆️ {price['label']}", f"subscription:buy:{t_key}"))
                    builder.row(("На главную", "main_menu"))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"*Твоя подписка*\n\n"
                            f"Тариф: {tier_name}\n"
                            f"Статус: {sub.status.value}\n"
                            f"Доступно каналов: {sub.channels_limit}\n"
                            f"Действует до: {expires}\n\n"
                            f"Выбери тариф для смены:"
                        ),
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("subscription:buy:"):
                    if not user_id:
                        return

                    tier = callback_data.split(":")[2]
                    tier_enum = SubscriptionTier(tier)
                    price = TIER_PRICES.get(tier, TIER_PRICES["solo"])

                    existing = await subscription_repo.get_active_by_user(user_id)
                    if existing and existing.tier == tier_enum:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"Ты уже на тарифе *{TIER_NAMES[tier]}*.",
                            attachments=[InlineKeyboardBuilder.main_menu()],
                            fmt="markdown",
                        )
                        return

                    new_expires = datetime.now(UTC) + timedelta(days=price["period_days"])

                    if existing and existing.status == SubscriptionStatus.TRIAL:
                        existing.tier = tier_enum
                        existing.status = SubscriptionStatus.ACTIVE
                        existing.channels_limit = tier_enum.channels_limit
                        existing.expires_at = new_expires
                        await subscription_repo.update(existing)
                    elif existing and existing.tier == tier_enum:
                        existing.expires_at = existing.expires_at + timedelta(days=price["period_days"])
                        await subscription_repo.update(existing)
                    else:
                        if existing:
                            existing.status = SubscriptionStatus.EXPIRED
                            await subscription_repo.update(existing)
                        await subscription_repo.create(
                            Subscription(
                                user_id=user_id,
                                tier=tier_enum,
                                status=SubscriptionStatus.ACTIVE,
                                channels_limit=tier_enum.channels_limit,
                                expires_at=new_expires,
                            )
                        )

                    await session.commit()

                    tier_name = TIER_NAMES.get(tier, tier)
                    expires_str = new_expires.strftime("%d.%m.%Y")

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"Тариф обновлён!\n\n"
                            f"*{tier_name}*\n"
                            f"Каналов: {tier_enum.channels_limit}\n"
                            f"Действует до: {expires_str}\n\n"
                            f"Оплата будет доступна позже."
                        ),
                        attachments=[InlineKeyboardBuilder.main_menu()],
                        fmt="markdown",
                    )

            except Exception:
                logger.exception(f"Error handling subscription callback: {callback_data}")
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Произошла ошибка. Попробуй ещё раз.",
                    attachments=[InlineKeyboardBuilder.main_menu()],
                )

            await max_client.close()
            await session.commit()
