from __future__ import annotations

import copy
from typing import Any

TOPIC_QUEUE_MAX_ITEMS = 100
TOPIC_QUEUE_MAX_LEN = 200


def normalize_topic_queue(raw: Any) -> list[str]:
    """Normalize any raw value into a FIFO list of short topic strings."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = raw.splitlines()
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        # Strip common list markers from model / paste output.
        if text[:2] in ("- ", "• ", "* "):
            text = text[2:].strip()
        elif len(text) > 2 and text[0].isdigit() and text[1] in ".)":
            text = text[2:].strip()
        elif len(text) > 3 and text[0].isdigit() and text[1].isdigit() and text[2] in ".)":
            text = text[3:].strip()
        if not text:
            continue
        if len(text) > TOPIC_QUEUE_MAX_LEN:
            text = text[: TOPIC_QUEUE_MAX_LEN - 1].rstrip() + "…"
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= TOPIC_QUEUE_MAX_ITEMS:
            break
    return out


def pop_topic(queue: list[str] | None) -> tuple[str | None, list[str]]:
    """Pop the first topic; return (topic_or_None, remaining)."""
    items = normalize_topic_queue(queue)
    if not items:
        return None, []
    return items[0], items[1:]


def apply_topic_queue_remaining(
    blocks_config: Any,
    remaining: list[str],
    *,
    block_type: str = "post_gen",
) -> dict[str, Any]:
    """Return a deep-copied blocks_config with topic_queue set to remaining."""
    from app.application.pipeline.normalize import is_v2, normalize_blocks_config

    raw = copy.deepcopy(blocks_config) if blocks_config is not None else {}
    if not is_v2(raw):
        raw = normalize_blocks_config(raw)

    queue = normalize_topic_queue(remaining)
    target = (block_type or "post_gen").strip() or "post_gen"
    found = False
    for step in raw.get("steps") or []:
        if step.get("type") != target:
            continue
        cfg = dict(step.get("config") or {})
        cfg["topic_queue"] = queue
        step["config"] = cfg
        found = True
        break

    if not found:
        import uuid

        steps = list(raw.get("steps") or [])
        steps.append(
            {
                "id": uuid.uuid4().hex[:12],
                "type": target,
                "enabled": False,
                "config": {"topic_queue": queue},
            }
        )
        raw["steps"] = steps

    return raw


def get_topic_queue_from_post_cfg(cfg: dict[str, Any] | None) -> list[str]:
    if not isinstance(cfg, dict):
        return []
    return normalize_topic_queue(cfg.get("topic_queue"))


def topic_queue_from_blocks_config(
    blocks_config: Any,
    *,
    block_type: str = "post_gen",
) -> list[str]:
    """Read topic_queue from v2 or UI-shaped blocks_config."""
    from app.application.pipeline.normalize import steps_to_ui_dict

    ui = steps_to_ui_dict(blocks_config or {})
    return get_topic_queue_from_post_cfg(ui.get(block_type))


def with_preserved_topic_queue(
    ui_blocks: Any,
    live_blocks_config: Any,
) -> dict[str, Any]:
    """Copy UI blocks but keep topic queues from the live pipeline config.

    Prevents Studio edits (schedule, etc.) from restoring topics already
    consumed by a published slot.
    """
    raw = copy.deepcopy(ui_blocks) if isinstance(ui_blocks, dict) else {}
    post_queue = topic_queue_from_blocks_config(live_blocks_config, block_type="post_gen")
    story_queue = topic_queue_from_blocks_config(live_blocks_config, block_type="story_gen")
    post = dict(raw.get("post_gen") or {})
    post["topic_queue"] = post_queue
    raw["post_gen"] = post
    if "story_gen" in raw or story_queue:
        story = dict(raw.get("story_gen") or {})
        story["topic_queue"] = story_queue
        raw["story_gen"] = story
    return raw


async def generate_topics_for_brief(
    openai_client: Any,
    *,
    brief: str,
    channel_title: str,
    count: int = 14,
    existing: list[str] | None = None,
) -> list[str]:
    """Ask the model for a list of post topic titles for the queue."""
    n = max(1, min(int(count or 14), 30))
    avoid = normalize_topic_queue(existing)
    avoid_block = ""
    if avoid:
        listed = "\n".join(f"- {t}" for t in avoid[:40])
        avoid_block = (
            f"Уже в очереди или недавно использовались — не повторяй:\n{listed}\n\n"
        )
    system_prompt = (
        "Ты придумываешь темы постов для канала. "
        "Ответь ТОЛЬКО списком тем: каждая тема на новой строке, "
        "без нумерации, без пояснений и без кавычек."
    )
    user_prompt = (
        f"Придумай {n} РАЗНЫХ тем постов для канала «{channel_title}» "
        f"по этому брифу/правилам:\n\n"
        f"«{(brief or '').strip()[:3500]}»\n\n"
        f"{avoid_block}"
        f"Правила:\n"
        f"- Язык: русский\n"
        f"- Одна строка = одна тема (яркий заголовок)\n"
        f"- Темы должны быть разными по главному объекту/ситуации\n"
        f"- Не дублируй близкие вариации одной мысли\n"
        f"- Ответ — только список тем"
    )
    raw = await openai_client.generate_text(
        prompt=user_prompt, system_prompt=system_prompt
    )
    return normalize_topic_queue(raw)[:n]
