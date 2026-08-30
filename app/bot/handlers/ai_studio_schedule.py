import json

from app.application.auth.feature_access import high_freq_allowed, premium_invite_message
from app.bot.ai_studio_text_input import claim_text_input
from app.bot.schedule_frequency import expected_slots, freq_label, is_high_freq
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient

from app.bot.handlers.ai_studio_entry import (
    SCHEDULE_SLOTS_TTL,
    _session_expired,
    _show_blocks,
)
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline
from app.bot.texts.studio_hints import SCHEDULE_INTRO


def _utc_to_msk_label(time_str: str) -> str:
    parts = str(time_str).split(":")
    h = (int(parts[0]) + 3) % 24
    m = parts[1] if len(parts) > 1 else "00"
    return f"{h:02d}:{m}"


def _schedule_time_prompt(*, slot: int = 1, total: int = 1) -> str:
    """Compact header for time picker; keeps example style when multi-slot."""
    if total > 1:
        return f"⏱ *Слот {slot}/{total}* — выбери время"
    return "⏱ *Расписание — выбери время*"


def _schedule_custom_prompt(*, slot: int = 1, total: int = 1) -> str:
    if total > 1:
        return f"*Слот {slot}/{total}.* Напиши ЧЧ:ММ (МСК), напр. 14:30"
    return "Напиши время в формате ЧЧ:ММ (по Москве).\nНапример: 14:30"


async def _claim_schedule_time_pick(redis, max_user_id: int, slot_state: dict | None = None) -> None:
    payload = "1"
    if slot_state:
        payload = f"{int(slot_state.get('slot', 0))}:{int(slot_state.get('total', 1))}"
    await claim_text_input(
        redis, max_user_id, "schedule_time_pick", payload, SCHEDULE_SLOTS_TTL
    )


async def _slots_expired(max_user_id: int, max_client) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(("⏱ Расписание заново", "ai:edit:schedule"))
    builder.row(("Назад к блокам", "ai:back_to_blocks"))
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "Сессия слотов истекла (слишком долгий перерыв).\n"
            "Открой «Расписание» и задай время заново."
        ),
        attachments=[builder.build()],
    )


async def _save_slots(redis, max_user_id: int, slot_state: dict) -> None:
    await redis.setex(
        f"ai_schedule_slots:{max_user_id}",
        SCHEDULE_SLOTS_TTL,
        json.dumps(slot_state),
    )


async def _save_schedule_and_show(
    max_user_id: int,
    max_client,
    channel_repo,
    session,
    *,
    times: list[str],
    per_slot_prompts: bool = False,
    slot_prompts: dict | None = None,
    slot_prompt_modes: dict | None = None,
    slot_image_addons: dict | None = None,
) -> None:
    prompts = dict(slot_prompts or {})
    modes = {
        str(key): "append"
        for key, value in dict(slot_prompt_modes or {}).items()
        if str(value).strip().lower() == "append" and str(prompts.get(key) or "").strip()
    }
    allowed = set(str(t).strip() for t in times if str(t).strip())
    image_addons = {}
    for key, value in dict(slot_image_addons or {}).items():
        time_key = str(key).strip()
        text = str(value or "").strip()
        if time_key in allowed and text:
            image_addons[time_key] = text[:4000]
    fsm = AIStudioFSM()
    await fsm.set_block_data(
        max_user_id,
        "schedule",
        {
            "times": times,
            "per_slot_prompts": bool(per_slot_prompts),
            "slot_prompts": prompts,
            "slot_prompt_modes": modes,
            "slot_image_addons": image_addons,
        },
    )
    state = await fsm.get_state(max_user_id)
    await sync_active_pipeline(session, state)
    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)


async def _ask_per_slot_toggle(max_user_id: int, max_client, times: list[str]) -> None:
    labels = ", ".join(_utc_to_msk_label(t) for t in times)
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            f"⏱ Время слотов: {labels} МСК\n\n"
            f"Разные промпты по слотам?\n"
            f"_Если нет — для всех слотов используется общий бриф поста._"
        ),
        attachments=[InlineKeyboardBuilder.ai_schedule_per_slot_toggle()],
        fmt="markdown",
    )


async def _ask_slot_prompt(
    max_user_id: int,
    max_client,
    redis,
    slot_state: dict,
) -> None:
    times = slot_state.get("times") or []
    idx = int(slot_state.get("prompt_idx", 0))
    total = len(times)
    time_utc = times[idx]
    msk = _utc_to_msk_label(time_utc)
    await claim_text_input(redis, max_user_id, "schedule_slot_prompt", "1", SCHEDULE_SLOTS_TTL)
    if str(slot_state.get("pending_mode") or "") == "append":
        text = (
            f"📝 *Добавить к общему брифу*\n"
            f"Слот {idx + 1} из {total} · {msk} МСК\n\n"
            f"Напиши дополнительную настройку — она будет добавлена к общему брифу.\n"
            f"Или нажми «Как общий бриф», чтобы оставить слот без своей настройки."
        )
    else:
        text = (
            f"📝 *Промпт для слота {idx + 1} из {total}*\n"
            f"Время: {msk} МСК\n\n"
            f"Напиши свой бриф для этого слота — он заменит общий.\n"
            f"Или нажми «Добавить к общему брифу», чтобы подмешать настройку, "
            f"либо «Как общий бриф»."
        )
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=text,
        attachments=[InlineKeyboardBuilder.ai_schedule_slot_prompt_actions()],
        fmt="markdown",
    )


async def _ask_slot_image_addon(
    max_user_id: int,
    max_client,
    redis,
    slot_state: dict,
) -> None:
    times = slot_state.get("times") or []
    idx = int(slot_state.get("prompt_idx", 0))
    total = len(times)
    time_utc = times[idx]
    msk = _utc_to_msk_label(time_utc)
    await claim_text_input(redis, max_user_id, "schedule_slot_image", "1", SCHEDULE_SLOTS_TTL)
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            f"🖼 *Картинка слота {idx + 1} из {total}*\n"
            f"Время: {msk} МСК\n\n"
            f"Напиши доп. для картинки этого слота (например: на картинке котики).\n"
            f"Или нажми «Без добавки» — картинка останется как в общих настройках."
        ),
        attachments=[InlineKeyboardBuilder.ai_schedule_slot_image_actions()],
        fmt="markdown",
    )


async def _advance_or_finish_slot_prompts(
    max_user_id: int,
    max_client,
    channel_repo,
    session,
    redis,
    slot_state: dict,
) -> None:
    times = list(slot_state.get("times") or [])
    next_idx = int(slot_state.get("prompt_idx", 0)) + 1
    slot_state["pending_mode"] = ""
    if next_idx >= len(times):
        await redis.delete(f"ai_schedule_slots:{max_user_id}")
        await _save_schedule_and_show(
            max_user_id,
            max_client,
            channel_repo,
            session,
            times=times,
            per_slot_prompts=True,
            slot_prompts=dict(slot_state.get("prompts") or {}),
            slot_prompt_modes=dict(slot_state.get("modes") or {}),
            slot_image_addons=dict(slot_state.get("image_addons") or {}),
        )
        return
    slot_state["prompt_idx"] = next_idx
    await _save_slots(redis, max_user_id, slot_state)
    await _ask_slot_prompt(max_user_id, max_client, redis, slot_state)


async def _finish_times_collected(
    max_user_id: int,
    max_client,
    channel_repo,
    session,
    redis,
    slot_state: dict,
) -> None:
    from app.bot.ai_studio_text_input import release_text_input

    await release_text_input(redis, max_user_id, "schedule_time_pick")
    times = list(slot_state.get("times") or [])
    total = int(slot_state.get("total") or len(times) or 1)
    if total <= 1 or len(times) <= 1:
        await redis.delete(f"ai_schedule_slots:{max_user_id}")
        await _save_schedule_and_show(
            max_user_id,
            max_client,
            channel_repo,
            session,
            times=times,
            per_slot_prompts=False,
            slot_prompts={},
            slot_prompt_modes={},
            slot_image_addons={},
        )
        return

    slot_state["prompts"] = {}
    slot_state["modes"] = {}
    slot_state["image_addons"] = {}
    slot_state["pending_mode"] = ""
    slot_state["prompt_idx"] = 0
    await redis.setex(f"ai_schedule_slots:{max_user_id}", SCHEDULE_SLOTS_TTL, json.dumps(slot_state))
    await _ask_per_slot_toggle(max_user_id, max_client, times)


async def handle_schedule_callback(callback_data: str, max_user_id: int, max_client, channel_repo, session) -> bool:
    if callback_data.startswith("ai:edit:schedule"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("schedule", {})
        if not block.get("enabled"):
            await fsm.toggle_block(max_user_id, "schedule")

        await fsm.set_block_data(max_user_id, "news_rss", {"enabled": False})

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("schedule", {})
        current_freq = block.get("frequency", "daily")

        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"⏱ *Когда публиковать*\n\n"
                f"{SCHEDULE_INTRO}\n\n"
                f"Текущая частота: {freq_label(current_freq)}\n\n"
                f"_При включении расписания RSS-мониторинг отключается._"
            ),
            attachments=[InlineKeyboardBuilder.ai_schedule_freq_select(max_user_id)],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:schedule:freq:"):
        freq = callback_data.split(":")[4]
        if is_high_freq(freq) and not high_freq_allowed(max_user_id):
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=premium_invite_message("Частота 6–8 публикаций в день"),
                attachments=[InlineKeyboardBuilder.ai_schedule_freq_select(max_user_id)],
            )
            return True
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(
            max_user_id,
            "schedule",
            {
                "frequency": freq,
                "times": [],
                "per_slot_prompts": False,
                "slot_prompts": {},
                "slot_prompt_modes": {},
                "slot_image_addons": {},
            },
        )
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        slots_per_day = expected_slots(freq)
        redis = await get_redis()
        slot_state = {
            "slot": 0,
            "total": slots_per_day,
            "times": [],
            "prompts": {},
            "modes": {},
            "image_addons": {},
            "pending_mode": "",
        }
        await _save_slots(redis, max_user_id, slot_state)
        await _claim_schedule_time_pick(redis, max_user_id, slot_state)

        slot_label = f"Время для слота 1 из {slots_per_day}" if slots_per_day > 1 else ""
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=_schedule_time_prompt(slot=1, total=slots_per_day),
            attachments=[InlineKeyboardBuilder.ai_schedule_time_picker(slot_label)],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:block:schedule:per_slot:no":
        redis = await get_redis()
        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        times = []
        if raw:
            times = list(json.loads(raw).get("times") or [])
        else:
            await _slots_expired(max_user_id, max_client)
            return True
        await redis.delete(f"ai_schedule_slots:{max_user_id}")
        await _save_schedule_and_show(
            max_user_id,
            max_client,
            channel_repo,
            session,
            times=times,
            per_slot_prompts=False,
            slot_prompts={},
            slot_prompt_modes={},
            slot_image_addons={},
        )
        return True

    if callback_data == "ai:block:schedule:per_slot:yes":
        redis = await get_redis()
        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        if not raw:
            await _slots_expired(max_user_id, max_client)
            return True
        slot_state = json.loads(raw)
        slot_state["prompts"] = {}
        slot_state["modes"] = {}
        slot_state["image_addons"] = {}
        slot_state["pending_mode"] = ""
        slot_state["prompt_idx"] = 0
        await _save_slots(redis, max_user_id, slot_state)
        await _ask_slot_prompt(max_user_id, max_client, redis, slot_state)
        return True

    if callback_data == "ai:block:schedule:slot_prompt:append":
        redis = await get_redis()
        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        if not raw:
            await _slots_expired(max_user_id, max_client)
            return True
        slot_state = json.loads(raw)
        slot_state["pending_mode"] = "append"
        await _save_slots(redis, max_user_id, slot_state)
        await _ask_slot_prompt(max_user_id, max_client, redis, slot_state)
        return True

    if callback_data == "ai:block:schedule:slot_prompt:skip":
        redis = await get_redis()
        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        if not raw:
            await _slots_expired(max_user_id, max_client)
            return True
        slot_state = json.loads(raw)
        slot_state["pending_mode"] = ""
        await _save_slots(redis, max_user_id, slot_state)
        await _ask_slot_image_addon(max_user_id, max_client, redis, slot_state)
        return True

    if callback_data == "ai:block:schedule:slot_image:skip":
        redis = await get_redis()
        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        if not raw:
            await _slots_expired(max_user_id, max_client)
            return True
        slot_state = json.loads(raw)
        times = list(slot_state.get("times") or [])
        idx = int(slot_state.get("prompt_idx", 0))
        image_addons = dict(slot_state.get("image_addons") or {})
        if 0 <= idx < len(times):
            image_addons.pop(times[idx], None)
        slot_state["image_addons"] = image_addons
        await _advance_or_finish_slot_prompts(
            max_user_id, max_client, channel_repo, session, redis, slot_state
        )
        return True

    if callback_data.startswith("ai:block:schedule:time:custom"):
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "schedule_custom", "1", SCHEDULE_SLOTS_TTL)
        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        slot_n, total = 1, 1
        if raw:
            st = json.loads(raw)
            total = int(st.get("total") or 1)
            slot_n = int(st.get("slot") or 0) + 1

        builder = InlineKeyboardBuilder()
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=_schedule_custom_prompt(slot=slot_n, total=total),
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:schedule:time:"):
        hour_msk = int(callback_data.split(":")[4])
        hour_utc = (hour_msk - 3) % 24
        time_str = f"{hour_utc:02d}:00"

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        redis = await get_redis()
        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        if not raw:
            freq = (state.get("blocks", {}).get("schedule") or {}).get("frequency", "daily")
            if expected_slots(freq) > 1:
                await _slots_expired(max_user_id, max_client)
                return True
            raw = json.dumps(
                {
                    "slot": 0,
                    "total": 1,
                    "times": [],
                    "prompts": {},
                    "modes": {},
                    "image_addons": {},
                    "pending_mode": "",
                }
            )

        slot_state = json.loads(raw)
        slot_state["times"].append(time_str)
        slot_idx = slot_state["slot"] + 1

        if slot_idx >= slot_state["total"]:
            await _finish_times_collected(
                max_user_id, max_client, channel_repo, session, redis, slot_state
            )
        else:
            slot_state["slot"] = slot_idx
            await _save_slots(redis, max_user_id, slot_state)
            await _claim_schedule_time_pick(redis, max_user_id, slot_state)
            next_slot = slot_idx + 1
            total = int(slot_state["total"])
            slot_label = f"Время для слота {next_slot} из {total}"
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=_schedule_time_prompt(slot=next_slot, total=total),
                attachments=[InlineKeyboardBuilder.ai_schedule_time_picker(slot_label)],
                fmt="markdown",
            )
        return True

    return False


async def handle_schedule_message(max_user_id: int, message_text: str, redis) -> bool:
    time_pick = await redis.get(f"ai_schedule_time_pick_wait:{max_user_id}")
    if time_pick:
        from app.bot.ai_studio_text_input import SCHEDULE_CUSTOM_HINT

        max_client = MaxAPIHTTPClient()
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=SCHEDULE_CUSTOM_HINT,
        )
        await max_client.close()
        return True

    slot_prompt_wait = await redis.get(f"ai_schedule_slot_prompt_wait:{max_user_id}")
    if slot_prompt_wait:
        await redis.delete(f"ai_schedule_slot_prompt_wait:{max_user_id}")
        prompt_text = (message_text or "").strip()
        if len(prompt_text) < 3:
            await claim_text_input(redis, max_user_id, "schedule_slot_prompt", "1", SCHEDULE_SLOTS_TTL)
            max_client = MaxAPIHTTPClient()
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "Промпт слишком короткий. Напиши хотя бы пару слов, "
                    "нажми «Добавить к общему брифу» или «Как общий бриф»."
                ),
                attachments=[InlineKeyboardBuilder.ai_schedule_slot_prompt_actions()],
            )
            await max_client.close()
            return True

        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        if not raw:
            max_client = MaxAPIHTTPClient()
            await _slots_expired(max_user_id, max_client)
            await max_client.close()
            return True
        slot_state = json.loads(raw)
        times = list(slot_state.get("times") or [])
        idx = int(slot_state.get("prompt_idx", 0))
        if idx < 0 or idx >= len(times):
            return True
        prompts = dict(slot_state.get("prompts") or {})
        prompts[times[idx]] = prompt_text[:4000]
        modes = dict(slot_state.get("modes") or {})
        if str(slot_state.get("pending_mode") or "") == "append":
            modes[times[idx]] = "append"
        else:
            modes.pop(times[idx], None)
        slot_state["prompts"] = prompts
        slot_state["modes"] = modes
        slot_state["pending_mode"] = ""
        await _save_slots(redis, max_user_id, slot_state)

        async with async_session_factory() as session:
            max_client = MaxAPIHTTPClient()
            channel_repo = SQLAlchemyChannelRepository(session)
            await _ask_slot_image_addon(
                max_user_id, max_client, redis, slot_state
            )
            await max_client.close()
        return True

    slot_image_wait = await redis.get(f"ai_schedule_slot_image_wait:{max_user_id}")
    if slot_image_wait:
        await redis.delete(f"ai_schedule_slot_image_wait:{max_user_id}")
        addon_text = (message_text or "").strip()
        if len(addon_text) < 3:
            await claim_text_input(redis, max_user_id, "schedule_slot_image", "1", SCHEDULE_SLOTS_TTL)
            max_client = MaxAPIHTTPClient()
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "Слишком коротко. Напиши хотя бы пару слов "
                    "или нажми «Без добавки»."
                ),
                attachments=[InlineKeyboardBuilder.ai_schedule_slot_image_actions()],
            )
            await max_client.close()
            return True

        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        if not raw:
            max_client = MaxAPIHTTPClient()
            await _slots_expired(max_user_id, max_client)
            await max_client.close()
            return True
        slot_state = json.loads(raw)
        times = list(slot_state.get("times") or [])
        idx = int(slot_state.get("prompt_idx", 0))
        if idx < 0 or idx >= len(times):
            return True
        image_addons = dict(slot_state.get("image_addons") or {})
        image_addons[times[idx]] = addon_text[:4000]
        slot_state["image_addons"] = image_addons
        await _save_slots(redis, max_user_id, slot_state)

        async with async_session_factory() as session:
            max_client = MaxAPIHTTPClient()
            channel_repo = SQLAlchemyChannelRepository(session)
            await _advance_or_finish_slot_prompts(
                max_user_id, max_client, channel_repo, session, redis, slot_state
            )
            await max_client.close()
        return True

    schedule_custom = await redis.get(f"ai_schedule_custom_time:{max_user_id}")
    if not schedule_custom:
        return False

    await redis.delete(f"ai_schedule_custom_time:{max_user_id}")

    from app.bot.handlers.time_utils import parse_time_hh_mm

    parsed = parse_time_hh_mm(message_text)
    if parsed is None:
        await claim_text_input(redis, max_user_id, "schedule_custom", "1", SCHEDULE_SLOTS_TTL)
        raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
        slot_n, total = 1, 1
        if raw:
            st = json.loads(raw)
            total = int(st.get("total") or 1)
            slot_n = int(st.get("slot") or 0) + 1
        max_client = MaxAPIHTTPClient()
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"Не понял время. {_schedule_custom_prompt(slot=slot_n, total=total)}",
            fmt="markdown",
        )
        await max_client.close()
        return True

    hour_msk, minute_msk = parsed
    hour_utc = (hour_msk - 3) % 24
    time_str = f"{hour_utc:02d}:{minute_msk:02d}"

    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        channel_repo = SQLAlchemyChannelRepository(session)

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await max_client.close()
            return True

        redis2 = await get_redis()
        raw = await redis2.get(f"ai_schedule_slots:{max_user_id}")
        if raw:
            slot_state = json.loads(raw)
            slot_state["times"].append(time_str)
            slot_idx = slot_state["slot"] + 1

            if slot_idx >= slot_state["total"]:
                await _finish_times_collected(
                    max_user_id, max_client, channel_repo, session, redis2, slot_state
                )
            else:
                slot_state["slot"] = slot_idx
                await _save_slots(redis2, max_user_id, slot_state)
                await _claim_schedule_time_pick(redis2, max_user_id, slot_state)
                next_slot = slot_idx + 1
                total = int(slot_state["total"])
                slot_label = f"Время для слота {next_slot} из {total}"
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=_schedule_time_prompt(slot=next_slot, total=total),
                    attachments=[InlineKeyboardBuilder.ai_schedule_time_picker(slot_label)],
                    fmt="markdown",
                )
        else:
            freq = (state.get("blocks", {}).get("schedule") or {}).get("frequency", "daily")
            if expected_slots(freq) > 1:
                await _slots_expired(max_user_id, max_client)
            else:
                await _save_schedule_and_show(
                    max_user_id,
                    max_client,
                    channel_repo,
                    session,
                    times=[time_str],
                    per_slot_prompts=False,
                    slot_prompts={},
                    slot_prompt_modes={},
                    slot_image_addons={},
                )

        await max_client.close()
        return True
