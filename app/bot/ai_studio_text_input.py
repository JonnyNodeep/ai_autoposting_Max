from __future__ import annotations

from typing import Literal

from loguru import logger

TextInputKind = Literal[
    # AI Studio
    "image_prompt",
    "video_prompt",
    "post_gen",
    "story_gen",
    "tts_instructions",
    "schedule_custom",
    "schedule_slot_prompt",
    "schedule_time_pick",
    "rss_feed",
    "rss_site",
    "rss_window",
    "rss_topic_brief",
    "rss_keywords_edit",
    "topic_queue",
    # Channel setup
    "style_prompt",
    "setup_refpost",
    "setup_time",
    "setup_slot_custom",
    "setup_time_pick",
    # Telegram bind / watermark
    "telegram_chat",
    "telegram_link",
    "wm_logo",
]

_WAIT_KEYS: dict[TextInputKind, str] = {
    "image_prompt": "ai_image_prompt_wait",
    "video_prompt": "ai_video_prompt_wait",
    "post_gen": "ai_post_gen_wait",
    "story_gen": "ai_story_gen_wait",
    "tts_instructions": "ai_tts_instructions_wait",
    "schedule_custom": "ai_schedule_custom_time",
    "schedule_slot_prompt": "ai_schedule_slot_prompt_wait",
    "schedule_time_pick": "ai_schedule_time_pick_wait",
    "rss_feed": "ai_rss_feed_wait",
    "rss_site": "ai_rss_site_wait",
    "rss_window": "ai_rss_window_wait",
    "rss_topic_brief": "ai_rss_topic_brief_wait",
    "rss_keywords_edit": "ai_rss_keywords_edit_wait",
    "topic_queue": "ai_topic_queue_wait",
    "style_prompt": "style_prompt",
    "setup_refpost": "setup_refpost",
    "setup_time": "setup_time",
    "setup_slot_custom": "setup_slot_custom",
    "setup_time_pick": "setup_time_pick",
    "telegram_chat": "tg_bind_chat",
    "telegram_link": "tg_bind_link",
    "wm_logo": "wm_logo_wait",
}

_REVIEW_KEYS: dict[TextInputKind, str] = {
    "image_prompt": "ai_image_prompt_review",
    "video_prompt": "ai_video_prompt_review",
    "post_gen": "ai_post_gen_review",
    "story_gen": "ai_story_gen_review",
    "topic_queue": "ai_topic_queue_review",
}

SETUP_TEXT_KINDS: frozenset[TextInputKind] = frozenset(
    {
        "style_prompt",
        "setup_refpost",
        "setup_time",
        "setup_slot_custom",
        "setup_time_pick",
        "telegram_chat",
        "telegram_link",
    }
)

STUDIO_TEXT_KINDS: frozenset[TextInputKind] = frozenset(
    {
        "image_prompt",
        "video_prompt",
        "post_gen",
        "story_gen",
        "tts_instructions",
        "schedule_custom",
        "schedule_slot_prompt",
        "schedule_time_pick",
        "rss_feed",
        "rss_site",
        "rss_window",
        "rss_topic_brief",
        "rss_keywords_edit",
        "topic_queue",
    }
)

SCHEDULE_CUSTOM_HINT = (
    'Если хотите ввести своё время, сначала нажмите кнопку «Своё время».'
)


def _key(prefix: str, user_id: int) -> str:
    return f"{prefix}:{user_id}"


def wait_key(kind: TextInputKind, user_id: int) -> str:
    return _key(_WAIT_KEYS[kind], user_id)


async def get_text_owner(redis, user_id: int) -> tuple[TextInputKind, str] | None:
    """Return the single active text-input owner, if any."""
    for kind, prefix in _WAIT_KEYS.items():
        raw = await redis.get(_key(prefix, user_id))
        if raw is None:
            continue
        value = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        return kind, value
    return None


async def has_pending_studio_text_input(redis, user_id: int) -> bool:
    """True if AI Studio is waiting for (or reviewing) text from this user."""
    keys = [_key(_WAIT_KEYS[kind], user_id) for kind in STUDIO_TEXT_KINDS]
    keys.extend(_key(prefix, user_id) for prefix in _REVIEW_KEYS.values())
    if not keys:
        return False
    return bool(await redis.exists(*keys))


async def clear_text_inputs(redis, user_id: int, *, except_kind: TextInputKind | None = None) -> None:
    """Drop all text-wait/review keys, optionally keeping one kind."""
    to_delete: list[str] = []
    for kind, prefix in _WAIT_KEYS.items():
        if kind == except_kind:
            continue
        to_delete.append(_key(prefix, user_id))
    for kind, prefix in _REVIEW_KEYS.items():
        if kind == except_kind:
            continue
        to_delete.append(_key(prefix, user_id))
    if to_delete:
        await redis.delete(*to_delete)


async def release_text_input(redis, user_id: int, kind: TextInputKind | None = None) -> None:
    """Clear one wait kind, or all text inputs if kind is None."""
    if kind is None:
        await clear_text_inputs(redis, user_id)
        return
    to_delete = [_key(_WAIT_KEYS[kind], user_id)]
    review_prefix = _REVIEW_KEYS.get(kind)
    if review_prefix:
        to_delete.append(_key(review_prefix, user_id))
    await redis.delete(*to_delete)


async def claim_text_input(
    redis,
    user_id: int,
    kind: TextInputKind,
    value: str,
    ttl: int,
) -> None:
    """Make ``kind`` the only active text-input wait for this user.

    Prevents leftover waits from stealing input meant for another flow.
    """
    await clear_text_inputs(redis, user_id, except_kind=kind)
    review_prefix = _REVIEW_KEYS.get(kind)
    if review_prefix:
        await redis.delete(_key(review_prefix, user_id))
    wait_prefix = _WAIT_KEYS[kind]
    await redis.setex(_key(wait_prefix, user_id), ttl, value)
    logger.info(f"Claimed text input kind={kind} user={user_id}")
