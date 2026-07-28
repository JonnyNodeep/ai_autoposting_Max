from __future__ import annotations

from typing import Any

from loguru import logger

DEFAULT_RECENT_LIMIT = 25
TOPIC_MAX_LEN = 120


def topic_from_post_text(text: str, *, max_len: int = TOPIC_MAX_LEN) -> str:
    """Take the first non-empty line as a short topic label."""
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            if len(cleaned) > max_len:
                return cleaned[: max_len - 1].rstrip() + "…"
            return cleaned
    return ""


def topics_from_messages(messages: list[dict[str, Any]], *, max_len: int = TOPIC_MAX_LEN) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        text = (msg.get("body") or {}).get("text") or ""
        topic = topic_from_post_text(text, max_len=max_len)
        if not topic:
            continue
        key = topic.casefold()
        if key in seen:
            continue
        seen.add(key)
        topics.append(topic)
    return topics


async def fetch_recent_post_topics(
    max_client: Any,
    chat_id: int | None,
    *,
    limit: int = DEFAULT_RECENT_LIMIT,
) -> list[str]:
    """Load recent channel posts and return short topic labels for exclude list."""
    if not chat_id or max_client is None:
        return []
    try:
        messages = await max_client.get_messages(chat_id, count=min(limit, 100))
    except Exception as e:
        logger.warning(f"fetch_recent_post_topics failed chat_id={chat_id}: {e}")
        return []
    return topics_from_messages(messages or [])
