"""Shared Telegram channel bind helpers for Max bot UI."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.entities.channel import Channel
from app.infrastructure.services.telegram_client import TelegramAPIHTTPClient

REDIS_TG_CHAT_WAIT = "tg_bind_chat:{user_id}"
REDIS_TG_LINK_WAIT = "tg_bind_link:{user_id}"
REDIS_TG_TTL = 1800

_CHAT_ID_RE = re.compile(r"^-?\d{5,20}$")
_TME_RE = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})/?$",
    re.IGNORECASE,
)


@dataclass
class BindResult:
    ok: bool
    message: str
    need_manual_link: bool = False
    channel: Channel | None = None


def parse_telegram_chat_id(raw: str) -> int | None:
    text = (raw or "").strip().replace(" ", "")
    if not _CHAT_ID_RE.match(text):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def normalize_telegram_link(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    m = _TME_RE.match(text)
    if m:
        return f"https://t.me/{m.group(1)}"
    if text.startswith("@"):
        username = text[1:].strip()
        if re.match(r"^[A-Za-z0-9_]{4,64}$", username):
            return f"https://t.me/{username}"
    return None


async def bind_telegram_chat(
    channel: Channel,
    chat_id: int,
    *,
    channel_repo,
    tg_client: TelegramAPIHTTPClient | None = None,
) -> BindResult:
    client = tg_client or TelegramAPIHTTPClient()
    owns_client = tg_client is None
    try:
        if not client.configured:
            return BindResult(False, "Telegram-бот не настроен (нет TELEGRAM_TOKEN).")
        chat = await client.get_chat(chat_id)
        title = (chat.get("title") or chat.get("username") or str(chat_id)).strip()
        link = TelegramAPIHTTPClient.resolve_public_link(chat)
        channel.telegram_chat_id = chat_id
        channel.telegram_link = link
        await channel_repo.update(channel)

        if link:
            return BindResult(
                True,
                f"Telegram привязан: *{title}*\nСсылка: {link}",
                channel=channel,
            )
        return BindResult(
            True,
            (
                f"Telegram привязан: *{title}*\n"
                f"Публичная ссылка не найдена (приватный канал?).\n"
                f"Пришли ссылку вида `https://t.me/...` или `@username`, "
                f"либо нажми «Пропустить» — в CTA останется ссылка Max."
            ),
            need_manual_link=True,
            channel=channel,
        )
    except Exception as e:
        return BindResult(False, f"Не удалось привязать Telegram: {e}")
    finally:
        if owns_client:
            await client.close()


async def set_telegram_link(
    channel: Channel,
    raw_link: str,
    *,
    channel_repo,
) -> BindResult:
    link = normalize_telegram_link(raw_link)
    if not link:
        return BindResult(
            False,
            "Не похоже на ссылку Telegram. Пример: `https://t.me/my_channel` или `@my_channel`.",
        )
    channel.telegram_link = link
    await channel_repo.update(channel)
    return BindResult(True, f"Ссылка сохранена: {link}", channel=channel)


async def unbind_telegram(channel: Channel, *, channel_repo) -> BindResult:
    channel.telegram_chat_id = None
    channel.telegram_link = None
    await channel_repo.update(channel)
    return BindResult(True, "Telegram-зеркало отвязано.", channel=channel)
