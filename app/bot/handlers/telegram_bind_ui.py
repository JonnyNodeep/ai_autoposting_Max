"""Telegram bind prompts shared by setup flow and channels card."""
from __future__ import annotations

from app.application.channels.telegram_bind import (
    REDIS_TG_CHAT_WAIT,
    REDIS_TG_LINK_WAIT,
    REDIS_TG_TTL,
    bind_telegram_chat,
    normalize_telegram_link,
    parse_telegram_chat_id,
    set_telegram_link,
    unbind_telegram,
)
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.redis.client import get_redis


def _wait_chat_key(user_id: int) -> str:
    return REDIS_TG_CHAT_WAIT.format(user_id=user_id)


def _wait_link_key(user_id: int) -> str:
    return REDIS_TG_LINK_WAIT.format(user_id=user_id)


async def offer_telegram_mirror(
    max_user_id: int,
    channel_id: int,
    max_client,
    *,
    source: str = "setup",
) -> None:
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "Привязать *Telegram-канал* для дублей постов?\n\n"
            "Telegram-бот должен быть администратором канала "
            "с правом публиковать сообщения.\n"
            "Ссылку для CTA подтянем сами по chat\\_id (для публичных каналов)."
        ),
        attachments=[InlineKeyboardBuilder.telegram_mirror_offer(channel_id, source=source)],
        fmt="markdown",
    )


async def start_telegram_chat_wait(
    max_user_id: int,
    channel_id: int,
    max_client,
    *,
    source: str = "setup",
) -> None:
    redis = await get_redis()
    await redis.delete(_wait_link_key(max_user_id))
    await redis.setex(_wait_chat_key(max_user_id), REDIS_TG_TTL, f"{channel_id}:{source}")
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "Пришли *chat\\_id* Telegram-канала (число вида `-100...`).\n\n"
            "Как узнать: добавь бота админом и посмотри id канала, "
            "либо перешли пост из канала сервису вроде @userinfobot."
        ),
        attachments=[InlineKeyboardBuilder.telegram_bind_retry(channel_id, source=source)],
        fmt="markdown",
    )


async def show_channel_telegram_card(
    max_user_id: int,
    channel,
    max_client,
) -> None:
    if channel.telegram_chat_id:
        link_line = channel.telegram_link or "публичная ссылка не задана"
        status = (
            f"Telegram: привязан\n"
            f"chat\\_id: `{channel.telegram_chat_id}`\n"
            f"Ссылка: {link_line}"
        )
    else:
        status = "Telegram: не привязан (посты только в Max)"
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=f"*{channel.title}*\n\n{status}",
        attachments=[
            InlineKeyboardBuilder.channel_card(
                channel.id,
                has_telegram=bool(channel.telegram_chat_id),
            )
        ],
        fmt="markdown",
    )


async def handle_telegram_chat_id_message(
    max_user_id: int,
    message_text: str,
    *,
    channel_repo,
    max_client,
    session,
    on_setup_done,
) -> bool:
    """Returns True if message was consumed as TG bind input."""
    redis = await get_redis()
    raw = await redis.get(_wait_chat_key(max_user_id))
    if not raw:
        return False

    await redis.delete(_wait_chat_key(max_user_id))
    parts = str(raw).split(":")
    channel_id = int(parts[0])
    source = parts[1] if len(parts) > 1 else "setup"

    chat_id = parse_telegram_chat_id(message_text)
    if chat_id is None:
        await redis.setex(_wait_chat_key(max_user_id), REDIS_TG_TTL, f"{channel_id}:{source}")
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Нужен числовой chat\\_id, например `-1004414934235`.",
            attachments=[InlineKeyboardBuilder.telegram_bind_retry(channel_id, source=source)],
            fmt="markdown",
        )
        return True

    channel = await channel_repo.get_by_id(channel_id)
    if not channel:
        await max_client.send_message_to_user(user_id=max_user_id, text="Канал не найден.")
        return True

    result = await bind_telegram_chat(channel, chat_id, channel_repo=channel_repo)
    await session.commit()

    if not result.ok:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=result.message,
            attachments=[InlineKeyboardBuilder.telegram_bind_retry(channel_id, source=source)],
            fmt="markdown",
        )
        return True

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=result.message,
        fmt="markdown",
    )

    if result.need_manual_link:
        await redis.setex(_wait_link_key(max_user_id), REDIS_TG_TTL, f"{channel_id}:{source}")
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Пришли ссылку `https://t.me/...` или `@username`, либо пропусти.",
            attachments=[InlineKeyboardBuilder.telegram_link_fallback(channel_id, source=source)],
            fmt="markdown",
        )
        return True

    if source == "setup":
        await on_setup_done()
    else:
        ch = await channel_repo.get_by_id(channel_id)
        if ch:
            await show_channel_telegram_card(max_user_id, ch, max_client)
    return True


async def handle_telegram_link_message(
    max_user_id: int,
    message_text: str,
    *,
    channel_repo,
    max_client,
    session,
    on_setup_done,
) -> bool:
    redis = await get_redis()
    raw = await redis.get(_wait_link_key(max_user_id))
    if not raw:
        return False

    await redis.delete(_wait_link_key(max_user_id))
    parts = str(raw).split(":")
    channel_id = int(parts[0])
    source = parts[1] if len(parts) > 1 else "setup"

    channel = await channel_repo.get_by_id(channel_id)
    if not channel:
        await max_client.send_message_to_user(user_id=max_user_id, text="Канал не найден.")
        return True

    result = await set_telegram_link(channel, message_text, channel_repo=channel_repo)
    if not result.ok:
        await redis.setex(_wait_link_key(max_user_id), REDIS_TG_TTL, f"{channel_id}:{source}")
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=result.message,
            attachments=[InlineKeyboardBuilder.telegram_link_fallback(channel_id, source=source)],
            fmt="markdown",
        )
        return True

    await session.commit()
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=result.message,
        fmt="markdown",
    )
    if source == "setup":
        await on_setup_done()
    else:
        ch = await channel_repo.get_by_id(channel_id)
        if ch:
            await show_channel_telegram_card(max_user_id, ch, max_client)
    return True


# Re-export helpers used by callback handlers
__all__ = [
    "offer_telegram_mirror",
    "start_telegram_chat_wait",
    "show_channel_telegram_card",
    "handle_telegram_chat_id_message",
    "handle_telegram_link_message",
    "unbind_telegram",
    "normalize_telegram_link",
    "parse_telegram_chat_id",
]
