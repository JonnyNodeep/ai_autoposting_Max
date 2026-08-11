from __future__ import annotations

from loguru import logger

from app.application.auth.admin_access import (
    display_channels_limit,
    is_admin_max_user,
)
from app.application.billing.manage_billing import CreatePaymentUseCase
from app.application.billing.pricing import (
    POSTS_PER_DAY_OPTIONS,
    TIER_LABELS,
    TIER_ORDER,
    is_upgrade,
    prorated_upgrade_amount,
    quote,
    remaining_days,
)
from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.payment_repository import SQLAPaymentRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.yookassa_service import YooKassaService


def _tier_buttons(*, prefix: str = "subscription:tier") -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for key in TIER_ORDER:
        q = quote(key, 1)
        builder.row((f"{TIER_LABELS[key]} (от {q.amount}₽)", f"{prefix}:{key}"))
    builder.row(("Назад", "subscription:status"))
    return builder


def _posts_buttons(tier: str, *, prefix: str = "subscription:posts") -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for ppd in POSTS_PER_DAY_OPTIONS:
        q = quote(tier, ppd)
        builder.row(
            (
                f"{ppd}/день · {q.quota} на 30 дн. · {q.amount}₽",
                f"{prefix}:{tier}:{ppd}",
            )
        )
    builder.row(("Назад", "subscription:buy"))
    return builder


def _confirm_keyboard(tier: str, posts_per_day: int, *, pay_cb: str) -> dict:
    q = quote(tier, posts_per_day)
    return (
        InlineKeyboardBuilder()
        .row((f"Оплатить {q.amount}₽", pay_cb))
        .row(("Изменить публикации/день", f"subscription:tier:{tier}"))
        .row(("Назад", "subscription:buy"))
        .build()
    )


def register_subscription_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["subscription:"])
    async def on_subscription_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {})
        max_user_id_raw = user_data.get("user_id") or user_data.get("id") or user_data.get("userId")
        try:
            max_user_id = int(max_user_id_raw) if max_user_id_raw is not None else None
        except (TypeError, ValueError):
            max_user_id = None

        if not callback_data or not max_user_id:
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            subscription_repo = SQLAlchemySubscriptionRepository(session)
            payment_repo = SQLAPaymentRepository(session)
            max_client = MaxAPIHTTPClient()
            yookassa = YooKassaService()

            user = await user_repo.get_by_max_user_id(max_user_id)
            user_id = user.id if user else None

            try:
                if callback_data == "subscription:status":
                    await _show_status(
                        max_client,
                        max_user_id=max_user_id,
                        user_id=user_id,
                        subscription_repo=subscription_repo,
                    )

                elif callback_data == "subscription:buy":
                    if is_admin_max_user(max_user_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="У тебя админский безлимит — оплата не нужна.",
                            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        )
                    else:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=(
                                "*Выбор тарифа*\n\n"
                                "Сначала каналы, затем сколько публикаций в день.\n"
                                "Подписка на *30 дней* с момента оплаты."
                            ),
                            attachments=[_tier_buttons().build()],
                            fmt="markdown",
                        )

                elif callback_data.startswith("subscription:tier:"):
                    tier = callback_data.split(":")[2]
                    if tier not in TIER_ORDER:
                        return
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"*{TIER_LABELS[tier]}*\n\n"
                            "Сколько публикаций в день?\n"
                            "Квота = выбранное число × 30 на период подписки."
                        ),
                        attachments=[_posts_buttons(tier).build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("subscription:posts:"):
                    parts = callback_data.split(":")
                    if len(parts) < 4:
                        return
                    tier, ppd_s = parts[2], parts[3]
                    posts_per_day = int(ppd_s)
                    q = quote(tier, posts_per_day)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"*Подтверждение*\n\n"
                            f"Тариф: {q.label}\n"
                            f"Публикаций в день: {q.posts_per_day}\n"
                            f"Пакет: {q.quota} на {q.period_days} дней\n"
                            f"Цена: *{q.amount}₽*\n\n"
                            "После оплаты подписка активна 30 дней."
                        ),
                        attachments=[
                            _confirm_keyboard(
                                tier,
                                posts_per_day,
                                pay_cb=f"subscription:pay:{tier}:{posts_per_day}",
                            )
                        ],
                        fmt="markdown",
                    )

                elif callback_data.startswith("subscription:pay:"):
                    if not user_id:
                        return
                    if is_admin_max_user(max_user_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="У тебя админский безлимит — оплата не нужна.",
                            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        )
                        return
                    parts = callback_data.split(":")
                    if len(parts) < 4:
                        return
                    tier, posts_per_day = parts[2], int(parts[3])
                    await _create_and_send_payment(
                        max_client,
                        session=session,
                        payment_repo=payment_repo,
                        subscription_repo=subscription_repo,
                        yookassa=yookassa,
                        max_user_id=max_user_id,
                        user_id=user_id,
                        tier=tier,
                        posts_per_day=posts_per_day,
                        user_repo=user_repo,
                    )

                elif callback_data == "subscription:upgrade":
                    if not user_id or is_admin_max_user(max_user_id):
                        return
                    sub = await subscription_repo.get_active_by_user(user_id)
                    if not sub or sub.status != SubscriptionStatus.ACTIVE:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Апгрейд доступен на активной оплаченной подписке. Сначала оформите тариф.",
                            attachments=[
                                InlineKeyboardBuilder()
                                .row(("Оформить", "subscription:buy"))
                                .row(("На главную", "main_menu"))
                                .build()
                            ],
                        )
                        return
                    builder = InlineKeyboardBuilder()
                    options = 0
                    for tier in TIER_ORDER:
                        for ppd in POSTS_PER_DAY_OPTIONS:
                            if not is_upgrade(sub.tier.value, sub.posts_per_day, tier, ppd):
                                continue
                            days_left = remaining_days(sub.expires_at)
                            amount = prorated_upgrade_amount(
                                sub.tier.value,
                                sub.posts_per_day,
                                tier,
                                ppd,
                                days_left,
                            )
                            if amount < 1:
                                continue
                            q = quote(tier, ppd)
                            builder.row(
                                (
                                    f"{q.label} · {ppd}/день · доплата {amount}₽",
                                    f"subscription:upgradepay:{tier}:{ppd}",
                                )
                            )
                            options += 1
                    if options == 0:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Уже максимальный пакет. Можно продлить текущий.",
                            attachments=[
                                InlineKeyboardBuilder()
                                .row(("Продлить", "subscription:renew"))
                                .row(("Назад", "subscription:status"))
                                .build()
                            ],
                        )
                        return
                    builder.row(("Назад", "subscription:status"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "*Апгрейд*\n\n"
                            "Доплата пропорциональна оставшимся дням. "
                            "Срок подписки не сдвигается."
                        ),
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("subscription:upgradepay:"):
                    if not user_id:
                        return
                    parts = callback_data.split(":")
                    if len(parts) < 4:
                        return
                    tier, posts_per_day = parts[2], int(parts[3])
                    await _create_and_send_payment(
                        max_client,
                        session=session,
                        payment_repo=payment_repo,
                        subscription_repo=subscription_repo,
                        yookassa=yookassa,
                        max_user_id=max_user_id,
                        user_id=user_id,
                        tier=tier,
                        posts_per_day=posts_per_day,
                        force_kind="upgrade",
                        user_repo=user_repo,
                    )

                elif callback_data == "subscription:renew":
                    if not user_id:
                        return
                    sub = await subscription_repo.get_active_by_user(user_id)
                    if not sub:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет активной подписки — оформите тариф.",
                            attachments=[
                                InlineKeyboardBuilder()
                                .row(("Оформить", "subscription:buy"))
                                .build()
                            ],
                        )
                        return
                    await _create_and_send_payment(
                        max_client,
                        session=session,
                        payment_repo=payment_repo,
                        subscription_repo=subscription_repo,
                        yookassa=yookassa,
                        max_user_id=max_user_id,
                        user_id=user_id,
                        tier=sub.tier.value,
                        posts_per_day=int(sub.posts_per_day or 1),
                        force_kind="renew" if sub.status == SubscriptionStatus.ACTIVE else "new",
                        user_repo=user_repo,
                    )

            except Exception:
                logger.exception(f"Error handling subscription callback: {callback_data}")
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Произошла ошибка. Попробуй ещё раз.",
                    attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                )

            await max_client.close()
            await session.commit()


async def _show_status(
    max_client: MaxAPIHTTPClient,
    *,
    max_user_id: int,
    user_id: int | None,
    subscription_repo,
) -> None:
    if not user_id:
        return

    sub = await subscription_repo.get_active_by_user(user_id)
    builder = InlineKeyboardBuilder()

    if not sub:
        builder.row(("Оформить подписку", "subscription:buy"))
        builder.row(("На главную", "main_menu"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="У тебя нет активной подписки.",
            attachments=[builder.build()],
        )
        return

    if is_admin_max_user(max_user_id):
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "*Твоя подписка*\n\n"
                "Тариф: Admin — безлимит\n"
                "Каналы: ∞\n"
                "Квота публикаций: ∞\n"
                "Действует: без срока"
            ),
            attachments=[InlineKeyboardBuilder().row(("На главную", "main_menu")).build()],
            fmt="markdown",
        )
        return

    tier_name = TIER_LABELS.get(sub.tier.value, sub.tier.value)
    channels_limit = display_channels_limit(max_user_id, sub)
    expires = sub.expires_at.strftime("%d.%m.%Y") if sub.expires_at else "?"
    left = sub.generations_left

    if sub.status == SubscriptionStatus.TRIAL:
        builder.row(("Оформить подписку", "subscription:buy"))
    else:
        builder.row(("Продлить", "subscription:renew"))
        builder.row(("Апгрейд", "subscription:upgrade"))
        builder.row(("Другой пакет", "subscription:buy"))
    builder.row(("На главную", "main_menu"))

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            f"*Твоя подписка*\n\n"
            f"Тариф: {tier_name}\n"
            f"Статус: {sub.status.value}\n"
            f"Каналов: {'∞' if channels_limit is None else channels_limit}\n"
            f"Публикаций в день: {sub.posts_per_day}\n"
            f"Квота: {left} из {sub.generations_quota}\n"
            f"Действует до: {expires}"
        ),
        attachments=[builder.build()],
        fmt="markdown",
    )


async def _create_and_send_payment(
    max_client: MaxAPIHTTPClient,
    *,
    session,
    payment_repo,
    subscription_repo,
    yookassa: YooKassaService,
    max_user_id: int,
    user_id: int,
    tier: str,
    posts_per_day: int,
    force_kind: str | None = None,
    user_repo=None,
) -> None:
    if not yookassa.is_configured:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Оплата временно недоступна (ЮKassa не настроена). Напишите администратору.",
            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
        )
        return

    uc = CreatePaymentUseCase(payment_repo, subscription_repo, yookassa, user_repo)
    try:
        payment = await uc.execute(
            user_id,
            tier,
            posts_per_day,
            kind=force_kind,
        )
    except ValueError as exc:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=str(exc),
            attachments=[
                InlineKeyboardBuilder()
                .row(("К подписке", "subscription:status"))
                .row(("На главную", "main_menu"))
                .build()
            ],
        )
        return

    await session.commit()

    q = quote(tier, posts_per_day)
    kind_label = {
        "new": "Оформление",
        "renew": "Продление",
        "upgrade": "Апгрейд",
    }.get(payment.kind.value, "Оплата")

    builder = (
        InlineKeyboardBuilder()
        .row(("💳 Перейти к оплате", payment.confirmation_url, "link", payment.confirmation_url))
        .row(("К подписке", "subscription:status"))
        .row(("На главную", "main_menu"))
    )
    discount_line = ""
    if payment.discount_percent:
        discount_line = (
            f"Скидка: *{payment.discount_percent}%* "
            f"(было {payment.amount_before_discount}₽)\n"
        )
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            f"*{kind_label}*\n\n"
            f"{q.label}\n"
            f"{posts_per_day} пуб./день · пакет {q.quota}\n"
            f"{discount_line}"
            f"К оплате: *{payment.amount}₽*\n\n"
            "После оплаты подписка активируется автоматически."
        ),
        attachments=[builder.build()],
        fmt="markdown",
    )
