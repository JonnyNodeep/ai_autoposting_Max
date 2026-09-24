"""Meditation channel preset, per-slot topic queues, and style ref uploads."""
from __future__ import annotations

import json

from loguru import logger

from app.application.channels.style_ref import save_style_ref
from app.application.pipeline.meditation_presets import (
    build_meditation_blocks_ui,
    slot_msk_label,
)
from app.application.pipeline.topic_queue import (
    clamp_topic_generate_count,
    filter_new_topics,
    generate_topics_for_brief,
    merge_avoid_topics,
    normalize_topic_queue,
)
from app.application.pipeline.normalize import resolve_post_brief, resolve_slot_topic_gen_extra
from app.bot.ai_studio_text_input import claim_text_input, get_text_owner, release_text_input
from app.bot.handlers.ai_studio_entry import REDIS_TTL, _session_expired, _show_blocks
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.redis.client import get_redis
from app.infrastructure.services.openai_client import OpenAIService

STYLE_REF_TTL = 1800
SLOT_TOPIC_TTL = 1800

_PICK_TOPICS_PREFIX = "ai:meditation:topics:pick:"
_STYLE_REF_PREFIX = "ai:meditation:style_ref:"
_TOPICS_PREFIX = "ai:meditation:topics:"
_TOPIC_ACTIONS = (
    "extra_clear",
    "generate",
    "gen30",
    "gen14",
    "clear",
    "extra",
    "add",
)


def _slot_time_from_callback(callback_data: str, prefix: str) -> str:
    """Extract slot time (may contain ':') after a fixed callback prefix."""
    if not callback_data.startswith(prefix):
        return ""
    return callback_data[len(prefix) :].strip()


def _parse_topics_action_callback(callback_data: str) -> tuple[str, str] | None:
    """Parse ai:meditation:topics:{slot_time}:{action} where slot_time is HH:MM."""
    if not callback_data.startswith(_TOPICS_PREFIX):
        return None
    if callback_data.startswith(_PICK_TOPICS_PREFIX):
        return None
    rest = callback_data[len(_TOPICS_PREFIX) :]
    for action in _TOPIC_ACTIONS:
        suffix = f":{action}"
        if rest.endswith(suffix):
            slot_time = rest[: -len(suffix)].strip()
            if slot_time:
                return slot_time, action
    return None


def _repair_legacy_slot_keys(schedule: dict) -> dict:
    """Fix slot maps saved with broken split(':')[-1] keys (e.g. '30' vs '04:30')."""
    sched = dict(schedule)
    times = [str(t).strip() for t in (sched.get("times") or []) if str(t).strip()]

    def _repair_map(raw: dict | None) -> dict:
        if not isinstance(raw, dict) or not raw:
            return dict(raw or {})
        fixed = dict(raw)
        for key, value in list(fixed.items()):
            if key in times:
                continue
            if ":" in str(key):
                continue
            matches = [t for t in times if t.endswith(f":{key}") or t.replace(":", "") == str(key)]
            if len(matches) == 1:
                if matches[0] not in fixed:
                    fixed[matches[0]] = value
                fixed.pop(key, None)
            elif str(key) == "11":
                for candidate in ("09:11", "15:11"):
                    if candidate in times and candidate not in fixed:
                        fixed[candidate] = value
                        fixed.pop(key, None)
                        break
        return fixed

    sched["slot_image_refs"] = _repair_map(sched.get("slot_image_refs"))
    sched["slot_topic_queues"] = _repair_map(sched.get("slot_topic_queues"))
    sched["slot_topic_history"] = _repair_map(sched.get("slot_topic_history"))
    sched["slot_topic_gen_extra"] = _repair_map(sched.get("slot_topic_gen_extra"))
    return sched


async def _get_schedule_block(max_user_id: int, *, repair: bool = True) -> dict:
    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id) or {}
    schedule = _schedule_block(state)
    if repair:
        repaired = _repair_legacy_slot_keys(schedule)
        if repaired != schedule:
            await _set_schedule_block(max_user_id, repaired)
            schedule = repaired
    return schedule


def _meditation_on(state: dict) -> bool:
    sched = (state.get("blocks") or {}).get("schedule") or {}
    return bool(sched.get("meditation_pipeline"))


def _schedule_block(state: dict) -> dict:
    return dict((state.get("blocks") or {}).get("schedule") or {})


async def _set_schedule_block(max_user_id: int, schedule: dict) -> None:
    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id) or {}
    blocks = dict(state.get("blocks") or {})
    blocks["schedule"] = schedule
    await fsm.set_block_data(max_user_id, "schedule", schedule)
    await fsm.set_data(max_user_id, {"blocks": blocks})


def _slot_queue(schedule: dict, slot_time: str) -> list[str]:
    queues = schedule.get("slot_topic_queues") or {}
    return normalize_topic_queue(queues.get(slot_time) or [])


def _set_slot_queue(schedule: dict, slot_time: str, queue: list[str]) -> dict:
    sched = dict(schedule)
    queues = dict(sched.get("slot_topic_queues") or {})
    queues[slot_time] = normalize_topic_queue(queue)
    sched["slot_topic_queues"] = queues
    return sched


def _slot_extra(schedule: dict, slot_time: str) -> str:
    return resolve_slot_topic_gen_extra(schedule, slot_time)


async def _save_slot_state(redis, max_user_id: int, payload: dict) -> None:
    await redis.setex(
        f"ai_meditation_slot:{max_user_id}",
        SLOT_TOPIC_TTL,
        json.dumps(payload),
    )


async def _load_slot_state(redis, max_user_id: int) -> dict | None:
    raw = await redis.get(f"ai_meditation_slot:{max_user_id}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _slot_label(slot_time: str) -> str:
    msk = slot_msk_label(slot_time)
    if msk == "07:30":
        return f"🌅 Утро ({msk})"
    if msk == "12:11":
        return f"☀️ Обед ({msk})"
    if msk == "18:11":
        return f"🌙 Вечер ({msk})"
    return f"⏱ {msk} МСК"


async def _show_slot_topic_menu(
    max_user_id: int,
    max_client,
    state: dict,
    slot_time: str,
) -> None:
    schedule = _schedule_block(state)
    queue = _slot_queue(schedule, slot_time)
    extra = _slot_extra(schedule, slot_time)
    lines = [
        f"📚 *Очередь тем — {_slot_label(slot_time)}*",
        "",
        f"В очереди: *{len(queue)}*",
        "",
        "На публикации берётся первая тема и удаляется из списка.",
        "Если темы закончатся — бот напишет вам, слот будет пропущен.",
    ]
    if extra:
        preview = extra if len(extra) <= 180 else extra[:179] + "…"
        lines.extend(["", f"_Пожелания к генерации:_ {preview}"])
    if queue:
        lines.append("")
        for i, topic in enumerate(queue[:10], 1):
            lines.append(f"{i}. {topic}")
        if len(queue) > 10:
            lines.append(f"\n_…и ещё {len(queue) - 10}_")
    else:
        lines.extend(["", "_Список пуст. Добавьте темы или сгенерируйте AI._"])

    builder = InlineKeyboardBuilder()
    prefix = f"ai:meditation:topics:{slot_time}"
    builder.row(("➕ Добавить темы", f"{prefix}:add"))
    builder.row(("🤖 Сгенерировать AI", f"{prefix}:generate"))
    builder.row(("✏️ Пожелания к генерации", f"{prefix}:extra"))
    if extra:
        builder.row(("🧹 Очистить пожелания", f"{prefix}:extra_clear"))
    if queue:
        builder.row(("🧹 Очистить очередь", f"{prefix}:clear"))
    builder.row(("⬅️ Слоты", "ai:meditation:topics"))
    builder.row(("Назад к блокам", "ai:back_to_blocks"))

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text="\n".join(lines),
        attachments=[builder.build()],
        fmt="markdown",
    )


async def handle_meditation_callback(
    callback_data: str,
    max_user_id: int,
    max_client,
    channel_repo,
    session,
) -> bool:
    if callback_data == "ai:meditation:preset":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state or not state.get("channel_id"):
            await _session_expired(max_user_id, max_client)
            return True
        blocks = build_meditation_blocks_ui()
        blocks["sunor_gen"]["enabled"] = True
        blocks["post_gen"]["enabled"] = True
        blocks["image_gen"]["enabled"] = True
        blocks["image_prompt"]["enabled"] = True
        await fsm.set_data(
            max_user_id,
            {"blocks": blocks, "step": AIStudioStep.SELECT_FEATURES},
        )
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_blocks(max_user_id, max_client, blocks, channel_repo)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "✅ Preset «Медитационный канал» применён.\n\n"
                "Дальше: загрузите референсы картинок по слотам, "
                "заполните очереди тем и привяжите Telegram."
            ),
        )
        return True

    if callback_data == "ai:meditation:topics":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        schedule = _schedule_block(state)
        times = list(schedule.get("times") or [])
        if not times:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Сначала настройте расписание с 3 слотами.",
            )
            return True
        builder = InlineKeyboardBuilder()
        for t in times:
            builder.row((_slot_label(t), f"ai:meditation:topics:pick:{t}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="📚 *Очереди тем по слотам*\n\nВыберите слот:",
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith(_PICK_TOPICS_PREFIX):
        slot_time = _slot_time_from_callback(callback_data, _PICK_TOPICS_PREFIX)
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        redis = await get_redis()
        await _save_slot_state(redis, max_user_id, {"slot_time": slot_time})
        schedule = await _get_schedule_block(max_user_id)
        state = await fsm.get_state(max_user_id) or state
        await _show_slot_topic_menu(max_user_id, max_client, state, slot_time)
        return True

    if callback_data == "ai:meditation:style_refs":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        schedule = await _get_schedule_block(max_user_id)
        times = list(schedule.get("times") or [])
        builder = InlineKeyboardBuilder()
        refs = schedule.get("slot_image_refs") or {}
        for t in times:
            has = "✅" if str(refs.get(t) or "").strip() else "—"
            builder.row((f"{has} {_slot_label(t)}", f"ai:meditation:style_ref:{t}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="🖼 *Референсы картинок по слотам*",
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith(_STYLE_REF_PREFIX):
        slot_time = _slot_time_from_callback(callback_data, _STYLE_REF_PREFIX)
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state or not state.get("channel_id"):
            await _session_expired(max_user_id, max_client)
            return True
        redis = await get_redis()
        await claim_text_input(
            redis,
            max_user_id,
            "meditation_style_ref",
            slot_time,
            STYLE_REF_TTL,
        )
        builder = InlineKeyboardBuilder()
        builder.row(("Отмена", "ai:meditation:style_refs"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🖼 Загрузите референс для {_slot_label(slot_time)}.\n"
                f"Пришлите картинку файлом или ссылку http(s)."
            ),
            attachments=[builder.build()],
        )
        return True

    parsed = _parse_topics_action_callback(callback_data)
    if not parsed:
        return False
    slot_time, action = parsed

    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id)
    if not state:
        await _session_expired(max_user_id, max_client)
        return True

    schedule = await _get_schedule_block(max_user_id)
    redis = await get_redis()
    await _save_slot_state(redis, max_user_id, {"slot_time": slot_time})

    if action == "add":
        await claim_text_input(redis, max_user_id, "meditation_topic_add", slot_time, SLOT_TOPIC_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Отмена", f"ai:meditation:topics:pick:{slot_time}"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Пришли темы — *каждая с новой строки*.",
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if action == "clear":
        schedule = _set_slot_queue(schedule, slot_time, [])
        await _set_schedule_block(max_user_id, schedule)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state, sync_topic_queue=True)
        await _show_slot_topic_menu(max_user_id, max_client, state or {}, slot_time)
        return True

    if action == "extra":
        await claim_text_input(redis, max_user_id, "meditation_topic_extra", slot_time, SLOT_TOPIC_TTL)
        current = _slot_extra(schedule, slot_time)
        cur_line = f"\n\nСейчас:\n«{current}»" if current else "\n\nСейчас пожеланий нет."
        builder = InlineKeyboardBuilder()
        builder.row(("Отмена", f"ai:meditation:topics:pick:{slot_time}"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "✏️ *Пожелания к генерации тем*\n\n"
                "Напиши, что учесть при генерации тем для этого слота."
                f"{cur_line}"
            ),
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if action == "extra_clear":
        extras = dict(schedule.get("slot_topic_gen_extra") or {})
        extras[slot_time] = ""
        schedule["slot_topic_gen_extra"] = extras
        await _set_schedule_block(max_user_id, schedule)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_slot_topic_menu(max_user_id, max_client, state or {}, slot_time)
        return True

    if action == "generate":
        await claim_text_input(redis, max_user_id, "meditation_topic_count", slot_time, SLOT_TOPIC_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("14 тем", f"ai:meditation:topics:{slot_time}:gen14"))
        builder.row(("30 тем", f"ai:meditation:topics:{slot_time}:gen30"))
        builder.row(("Отмена", f"ai:meditation:topics:pick:{slot_time}"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Сколько тем сгенерировать? Или напишите число 1–100.",
            attachments=[builder.build()],
        )
        return True

    if action in ("gen14", "gen30"):
        count = 14 if action == "gen14" else 30
        await _generate_slot_topics(
            max_user_id,
            max_client,
            session,
            slot_time,
            count=count,
        )
        return True

    return False


async def _generate_slot_topics(
    max_user_id: int,
    max_client,
    session,
    slot_time: str,
    *,
    count: int,
) -> None:
    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id) or {}
    schedule = _schedule_block(state)
    post = (state.get("blocks") or {}).get("post_gen") or {}
    brief = resolve_post_brief(schedule, post, slot_time)
    queue = _slot_queue(schedule, slot_time)
    history = list((schedule.get("slot_topic_history") or {}).get(slot_time) or [])
    extra = _slot_extra(schedule, slot_time)
    n = clamp_topic_generate_count(count, queue_len=len(queue))
    if n <= 0:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Очередь слота уже полна (100 тем).",
        )
        return

    channel_title = "канал"
    ch_id = state.get("channel_id")
    if ch_id:
        from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository

        ch = await SQLAlchemyChannelRepository(session).get_by_id(int(ch_id))
        if ch and ch.title:
            channel_title = ch.title

    openai = OpenAIService()
    avoid = merge_avoid_topics(queue, history)
    topics = await generate_topics_for_brief(
        openai,
        brief=brief,
        channel_title=channel_title,
        count=n,
        existing=avoid,
        extra_prompt=extra,
    )
    if not topics:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Не удалось сгенерировать темы. Попробуйте позже.",
        )
        return

    merged = normalize_topic_queue([*queue, *topics])
    schedule = _set_slot_queue(schedule, slot_time, merged)
    await _set_schedule_block(max_user_id, schedule)
    state = await fsm.get_state(max_user_id)
    await sync_active_pipeline(session, state, sync_topic_queue=True)
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=f"✅ Добавлено тем: {len(topics)}. Всего в очереди слота: {len(merged)}.",
    )
    await _show_slot_topic_menu(max_user_id, max_client, state or {}, slot_time)


def _attachment_image_url(attachments: list | None) -> str:
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        if att.get("type") not in (None, "image"):
            continue
        payload = att.get("payload") or {}
        url = (payload.get("url") or att.get("url") or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return url
    return ""


async def handle_meditation_message(
    max_user_id: int,
    message_text: str,
    redis,
    max_client,
    session,
    *,
    attachments: list | None = None,
) -> bool:
    owner = await get_text_owner(redis, max_user_id)
    if not owner:
        return False
    kind, payload = owner

    if kind == "meditation_topic_add":
        slot_time = payload
        await release_text_input(redis, max_user_id, "meditation_topic_add")
        lines = [ln.strip() for ln in (message_text or "").splitlines() if ln.strip()]
        if not lines:
            return True
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id) or {}
        schedule = _schedule_block(state)
        queue = _slot_queue(schedule, slot_time)
        merged = normalize_topic_queue([*queue, *lines])
        schedule = _set_slot_queue(schedule, slot_time, merged)
        await _set_schedule_block(max_user_id, schedule)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state, sync_topic_queue=True)
        await _show_slot_topic_menu(max_user_id, max_client, state or {}, slot_time)
        return True

    if kind == "meditation_topic_extra":
        slot_time = payload
        await release_text_input(redis, max_user_id, "meditation_topic_extra")
        extras = dict(_schedule_block(await AIStudioFSM().get_state(max_user_id) or {}).get("slot_topic_gen_extra") or {})
        extras[slot_time] = (message_text or "").strip()[:1500]
        schedule = _schedule_block(await AIStudioFSM().get_state(max_user_id) or {})
        schedule["slot_topic_gen_extra"] = extras
        await _set_schedule_block(max_user_id, schedule)
        state = await AIStudioFSM().get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_slot_topic_menu(max_user_id, max_client, state or {}, slot_time)
        return True

    if kind == "meditation_topic_count":
        slot_time = payload
        await release_text_input(redis, max_user_id, "meditation_topic_count")
        text = (message_text or "").strip().replace(" ", "")
        if not text.isdigit():
            return True
        count = int(text)
        if not 1 <= count <= 100:
            return True
        await _generate_slot_topics(max_user_id, max_client, session, slot_time, count=count)
        return True

    if kind == "meditation_style_ref":
        slot_time = payload
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id) or {}
        ch_id = state.get("channel_id")
        if not ch_id:
            return True
        source = (message_text or "").strip()
        if not source:
            source = _attachment_image_url(attachments)
        if not source:
            return True
        try:
            path = await save_style_ref(int(ch_id), slot_time, source)
        except Exception as exc:
            logger.exception("style ref upload failed: {}", exc)
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=f"Не удалось сохранить референс: {exc}",
            )
            return True
        await release_text_input(redis, max_user_id, "meditation_style_ref")
        schedule = _schedule_block(state)
        refs = dict(schedule.get("slot_image_refs") or {})
        refs[slot_time] = path
        schedule["slot_image_refs"] = refs
        await _set_schedule_block(max_user_id, schedule)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"✅ Референс сохранён для {_slot_label(slot_time)}.",
        )
        return True

    return False
