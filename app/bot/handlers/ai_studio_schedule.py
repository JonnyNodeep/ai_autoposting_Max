import json

from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient

from app.bot.handlers.ai_studio_entry import REDIS_TTL, _session_expired, _show_blocks


async def handle_schedule_callback(callback_data: str, max_user_id: int, max_client, channel_repo) -> bool:
    if callback_data.startswith("ai:edit:schedule"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("schedule", {})
        if not block.get("enabled"):
            await fsm.toggle_block(max_user_id, "schedule")

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("schedule", {})
        current_freq = block.get("frequency", "daily")

        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        freq_names = {"daily": "1 раз в день", "2x_day": "2 раза в день", "3x_day": "3 раза в день",
                      "2x_week": "2 раза в неделю", "weekly": "1 раз в неделю"}
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"⏱ *Расписание публикаций*\n\n"
                f"Текущая: {freq_names.get(current_freq, current_freq)}"
            ),
            attachments=[InlineKeyboardBuilder.ai_schedule_freq_select()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:schedule:freq:"):
        freq = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(max_user_id, "schedule", {"frequency": freq, "times": []})
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        slots_per_day = {"2x_day": 2, "3x_day": 3}.get(freq, 1)
        redis = await get_redis()
        slot_state = {"slot": 0, "total": slots_per_day, "times": []}
        await redis.setex(f"ai_schedule_slots:{max_user_id}", REDIS_TTL, json.dumps(slot_state))

        slot_label = f"Время для слота 1 из {slots_per_day}" if slots_per_day > 1 else ""
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"⏱ *Расписание — выбери время*",
            attachments=[InlineKeyboardBuilder.ai_schedule_time_picker(slot_label)],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:schedule:time:custom"):
        redis = await get_redis()
        await redis.setex(f"ai_schedule_custom_time:{max_user_id}", REDIS_TTL, "1")

        builder = InlineKeyboardBuilder()
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Напиши время в формате ЧЧ:ММ (по Москве).\nНапример: 14:30",
            attachments=[builder.build()],
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
            raw = json.dumps({"slot": 0, "total": 1, "times": []})

        slot_state = json.loads(raw)
        slot_state["times"].append(time_str)
        slot_idx = slot_state["slot"] + 1

        if slot_idx >= slot_state["total"]:
            await redis.delete(f"ai_schedule_slots:{max_user_id}")
            await fsm.set_block_data(max_user_id, "schedule", {"times": slot_state["times"]})
            state = await fsm.get_state(max_user_id)
            await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        else:
            slot_state["slot"] = slot_idx
            await redis.setex(f"ai_schedule_slots:{max_user_id}", REDIS_TTL, json.dumps(slot_state))
            slot_label = f"Время для слота {slot_idx + 1} из {slot_state['total']}"
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=f"⏱ *Расписание — выбери время*",
                attachments=[InlineKeyboardBuilder.ai_schedule_time_picker(slot_label)],
                fmt="markdown",
            )
        return True

    return False


async def handle_schedule_message(max_user_id: int, message_text: str, redis) -> bool:
    schedule_custom = await redis.get(f"ai_schedule_custom_time:{max_user_id}")
    if not schedule_custom:
        return False

    await redis.delete(f"ai_schedule_custom_time:{max_user_id}")

    from app.bot.handlers.time_utils import parse_time_hh_mm

    parsed = parse_time_hh_mm(message_text)
    if parsed is None:
        await redis.setex(f"ai_schedule_custom_time:{max_user_id}", REDIS_TTL, "1")
        max_client = MaxAPIHTTPClient()
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Не понял время. Напиши в формате ЧЧ:ММ, например 14:30.",
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
                await redis2.delete(f"ai_schedule_slots:{max_user_id}")
                await fsm.set_block_data(max_user_id, "schedule", {"times": slot_state["times"]})
                state = await fsm.get_state(max_user_id)
                await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
            else:
                slot_state["slot"] = slot_idx
                await redis2.setex(f"ai_schedule_slots:{max_user_id}", REDIS_TTL, json.dumps(slot_state))
                slot_label = f"Время для слота {slot_idx + 1} из {slot_state['total']}"
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=f"⏱ *Расписание — выбери время*",
                    attachments=[InlineKeyboardBuilder.ai_schedule_time_picker(slot_label)],
                    fmt="markdown",
                )
        else:
            await fsm.set_block_data(max_user_id, "schedule", {"times": [time_str]})
            state = await fsm.get_state(max_user_id)
            await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

        await max_client.close()
        return True
