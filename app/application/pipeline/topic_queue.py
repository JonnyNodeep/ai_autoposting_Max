from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

TOPIC_QUEUE_MAX_ITEMS = 100
TOPIC_QUEUE_MAX_LEN = 200
TOPIC_HISTORY_MAX_ITEMS = 300
TOPIC_GENERATE_MAX = 100
TOPIC_GENERATE_BATCH = 25
TOPIC_GENERATE_PROMPT_AVOID = 80


def normalize_topic_list(raw: Any, *, max_items: int) -> list[str]:
    """Normalize any raw value into a FIFO list of short topic strings."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = raw.splitlines()
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []

    cap = max(1, int(max_items))
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
        if len(out) >= cap:
            break
    return out


def normalize_topic_queue(raw: Any) -> list[str]:
    """Normalize any raw value into a FIFO list of short topic strings."""
    return normalize_topic_list(raw, max_items=TOPIC_QUEUE_MAX_ITEMS)


def normalize_topic_history(raw: Any) -> list[str]:
    """Normalize published-topic journal (keeps up to TOPIC_HISTORY_MAX_ITEMS)."""
    return normalize_topic_list(raw, max_items=TOPIC_HISTORY_MAX_ITEMS)


def topic_match_key(text: str) -> str:
    """Loose key for duplicate checks: casefold, no emoji/punctuation."""
    s = str(text or "").casefold()
    chars: list[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith("So") or cat in ("Mn", "Sk", "Sm"):
            continue
        chars.append(ch)
    s = "".join(chars)
    s = re.sub(r"[^\w\s]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def filter_new_topics(candidates: list[str], avoid: list[str]) -> list[str]:
    """Drop candidates that match avoid list by exact or loose key."""
    avoid_fold = {t.casefold() for t in avoid if t}
    avoid_loose = {topic_match_key(t) for t in avoid if t and topic_match_key(t)}
    out: list[str] = []
    seen_fold: set[str] = set()
    seen_loose: set[str] = set()
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        fold = text.casefold()
        loose = topic_match_key(text)
        if fold in avoid_fold or fold in seen_fold:
            continue
        if loose and (loose in avoid_loose or loose in seen_loose):
            continue
        seen_fold.add(fold)
        if loose:
            seen_loose.add(loose)
        out.append(text)
    return out


def merge_avoid_topics(*groups: list[str] | None) -> list[str]:
    items: list[str] = []
    for group in groups:
        if group:
            items.extend(group)
    return normalize_topic_list(
        items, max_items=TOPIC_HISTORY_MAX_ITEMS + TOPIC_QUEUE_MAX_ITEMS
    )


def append_topic_history(existing: list[str] | None, used: str | None) -> list[str]:
    """Append a published topic; keep the most recent TOPIC_HISTORY_MAX_ITEMS."""
    items = normalize_topic_history(existing)
    text_list = normalize_topic_list([used], max_items=1)
    if not text_list:
        return items
    text = text_list[0]
    key = text.casefold()
    items = [t for t in items if t.casefold() != key]
    items.append(text)
    if len(items) > TOPIC_HISTORY_MAX_ITEMS:
        items = items[-TOPIC_HISTORY_MAX_ITEMS:]
    return items


def clamp_topic_generate_count(
    count: Any, *, queue_len: int = 0, default: int = 14
) -> int:
    """Clamp requested count to 1..100 and remaining queue slots (may be 0)."""
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = int(default)
    n = max(1, min(n, TOPIC_GENERATE_MAX))
    try:
        used = max(0, int(queue_len or 0))
    except (TypeError, ValueError):
        used = 0
    room = max(0, TOPIC_QUEUE_MAX_ITEMS - used)
    return min(n, room)


def pop_topic(queue: list[str] | None) -> tuple[str | None, list[str]]:
    """Pop the first topic; return (topic_or_None, remaining)."""
    items = normalize_topic_queue(queue)
    if not items:
        return None, []
    return items[0], items[1:]


def _set_post_gen_history(raw: dict[str, Any], history: list[str]) -> None:
    for step in raw.get("steps") or []:
        if step.get("type") != "post_gen":
            continue
        cfg = dict(step.get("config") or {})
        cfg["topic_history"] = history
        step["config"] = cfg
        return
    # No post_gen step: attach history on a disabled post_gen so the journal survives.
    import uuid

    steps = list(raw.get("steps") or [])
    steps.append(
        {
            "id": uuid.uuid4().hex[:12],
            "type": "post_gen",
            "enabled": False,
            "config": {"topic_history": history},
        }
    )
    raw["steps"] = steps


def apply_topic_queue_remaining(
    blocks_config: Any,
    remaining: list[str],
    *,
    block_type: str = "post_gen",
    used_topic: str | None = None,
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

    if used_topic:
        current = topic_history_from_blocks_config(raw)
        _set_post_gen_history(raw, append_topic_history(current, used_topic))

    return raw


def get_topic_queue_from_post_cfg(cfg: dict[str, Any] | None) -> list[str]:
    if not isinstance(cfg, dict):
        return []
    return normalize_topic_queue(cfg.get("topic_queue"))


def get_topic_history_from_post_cfg(cfg: dict[str, Any] | None) -> list[str]:
    if not isinstance(cfg, dict):
        return []
    return normalize_topic_history(cfg.get("topic_history"))


def topic_queue_from_blocks_config(
    blocks_config: Any,
    *,
    block_type: str = "post_gen",
) -> list[str]:
    """Read topic_queue from v2 or UI-shaped blocks_config."""
    from app.application.pipeline.normalize import steps_to_ui_dict

    ui = steps_to_ui_dict(blocks_config or {})
    return get_topic_queue_from_post_cfg(ui.get(block_type))


def topic_history_from_blocks_config(blocks_config: Any) -> list[str]:
    """Read topic_history from post_gen in v2 or UI-shaped config."""
    from app.application.pipeline.normalize import steps_to_ui_dict

    ui = steps_to_ui_dict(blocks_config or {})
    return get_topic_history_from_post_cfg(ui.get("post_gen"))


def _picked_topic_history(ui_post: dict[str, Any], live_blocks_config: Any) -> list[str]:
    live_hist = topic_history_from_blocks_config(live_blocks_config)
    if live_hist:
        return live_hist
    return get_topic_history_from_post_cfg(ui_post)


def with_preserved_topic_history(
    ui_blocks: Any,
    live_blocks_config: Any,
) -> dict[str, Any]:
    """Keep live topic_history; if live is empty, keep UI history (backfill)."""
    raw = copy.deepcopy(ui_blocks) if isinstance(ui_blocks, dict) else {}
    post = dict(raw.get("post_gen") or {})
    post["topic_history"] = _picked_topic_history(post, live_blocks_config)
    raw["post_gen"] = post
    return raw


def with_preserved_topic_queue(
    ui_blocks: Any,
    live_blocks_config: Any,
) -> dict[str, Any]:
    """Copy UI blocks but keep topic queues from the live pipeline config.

    Prevents Studio edits (schedule, etc.) from restoring topics already
    consumed by a published slot. Always preserves topic_history from live
    (or UI backfill if live journal is still empty).
    """
    raw = copy.deepcopy(ui_blocks) if isinstance(ui_blocks, dict) else {}
    post_queue = topic_queue_from_blocks_config(live_blocks_config, block_type="post_gen")
    story_queue = topic_queue_from_blocks_config(live_blocks_config, block_type="story_gen")
    post = dict(raw.get("post_gen") or {})
    post["topic_queue"] = post_queue
    post["topic_history"] = _picked_topic_history(post, live_blocks_config)
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
    extra_prompt: str = "",
    model: str | None = None,
    mode: str = "post",
) -> list[str]:
    """Ask the model for a list of post topic titles for the queue."""
    try:
        n = int(count or 14)
    except (TypeError, ValueError):
        n = 14
    n = max(1, min(n, TOPIC_GENERATE_MAX))
    avoid = merge_avoid_topics(existing)
    fairy = (mode or "post").strip().lower() in ("fairy_tale", "bedtime", "fairy")
    if fairy:
        system_prompt = (
            "Ты придумываешь темы добрых детских сказок на ночь для канала. "
            "Возраст слушателей: 3–6 лет. Без страха, жести и сложных тем. "
            "Ответь ТОЛЬКО списком тем: каждая тема на новой строке, "
            "без нумерации, без пояснений и без кавычек."
        )
    else:
        system_prompt = (
            "Ты придумываешь темы постов для канала. "
            "Ответь ТОЛЬКО списком тем: каждая тема на новой строке, "
            "без нумерации, без пояснений и без кавычек."
        )
    extra = (extra_prompt or "").strip()[:1500]
    extra_block = ""
    if extra:
        extra_block = (
            "Дополнительно обязательно учти эти пожелания "
            "(каждая тема должна им соответствовать):\n"
            f"«{extra}»\n\n"
        )
    out: list[str] = []
    while len(out) < n:
        need = min(TOPIC_GENERATE_BATCH, n - len(out))
        combined_avoid = merge_avoid_topics(avoid, out)
        listed = "\n".join(f"- {t}" for t in combined_avoid[-TOPIC_GENERATE_PROMPT_AVOID:])
        avoid_block = ""
        if listed:
            avoid_block = (
                "Уже в очереди, уже выходили в канал или только что предложены — "
                f"не повторяй:\n{listed}\n\n"
            )
        if fairy:
            user_prompt = (
                f"Придумай {need} РАЗНЫХ тем сказок для канала «{channel_title}» "
                f"по этому брифу/правилам:\n\n"
                f"«{(brief or '').strip()[:3500]}»\n\n"
                f"{extra_block}"
                f"{avoid_block}"
                f"Правила:\n"
                f"- Язык: русский\n"
                f"- Одна строка = одна тема (яркий заголовок сказки)\n"
                f"- Для детей 3–6 лет, bedtime / на ночь, тёплые и безопасные\n"
                f"- Темы должны быть разными по герою/ситуации\n"
                f"- Не дублируй близкие вариации одной мысли\n"
                f"- Ответ — только список тем"
            )
        else:
            user_prompt = (
                f"Придумай {need} РАЗНЫХ тем постов для канала «{channel_title}» "
                f"по этому брифу/правилам:\n\n"
                f"«{(brief or '').strip()[:3500]}»\n\n"
                f"{extra_block}"
                f"{avoid_block}"
                f"Правила:\n"
                f"- Язык: русский\n"
                f"- Одна строка = одна тема (яркий заголовок)\n"
                f"- Темы должны быть разными по главному объекту/ситуации\n"
                f"- Не дублируй близкие вариации одной мысли\n"
                f"- Ответ — только список тем"
            )
        raw = await openai_client.generate_text(
            prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
        )
        batch = filter_new_topics(
            normalize_topic_list(raw, max_items=TOPIC_GENERATE_BATCH + 10),
            combined_avoid,
        )[:need]
        if not batch:
            break
        out.extend(batch)
    return out[:n]
