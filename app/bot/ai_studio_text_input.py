from __future__ import annotations

from typing import Literal

from loguru import logger

TextInputKind = Literal["image_prompt", "video_prompt", "post_gen", "schedule_custom"]

_WAIT_KEYS: dict[TextInputKind, str] = {
    "image_prompt": "ai_image_prompt_wait",
    "video_prompt": "ai_video_prompt_wait",
    "post_gen": "ai_post_gen_wait",
    "schedule_custom": "ai_schedule_custom_time",
}

_REVIEW_KEYS: dict[TextInputKind, str] = {
    "image_prompt": "ai_image_prompt_review",
    "video_prompt": "ai_video_prompt_review",
    "post_gen": "ai_post_gen_review",
}


def _key(prefix: str, user_id: int) -> str:
    return f"{prefix}:{user_id}"


async def clear_text_inputs(redis, user_id: int, *, except_kind: TextInputKind | None = None) -> None:
    """Drop all AI Studio text-wait/review keys, optionally keeping one kind."""
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


async def claim_text_input(
    redis,
    user_id: int,
    kind: TextInputKind,
    value: str,
    ttl: int,
) -> None:
    """Make ``kind`` the only active text-input wait for this user.

    Prevents leftover waits (e.g. image prompt) from stealing a post brief.
    """
    await clear_text_inputs(redis, user_id, except_kind=kind)
    # Also clear this kind's stale review — a new wait supersedes pending review.
    review_prefix = _REVIEW_KEYS.get(kind)
    if review_prefix:
        await redis.delete(_key(review_prefix, user_id))
    wait_prefix = _WAIT_KEYS[kind]
    await redis.setex(_key(wait_prefix, user_id), ttl, value)
    logger.info(f"AI Studio claimed text input kind={kind} user={user_id}")
