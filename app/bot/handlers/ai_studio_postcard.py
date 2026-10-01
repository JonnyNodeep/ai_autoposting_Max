"""Living Postcards preset and topic-slot entry points."""
from __future__ import annotations

from app.application.pipeline.postcards.presets import (
    build_postcard_blocks_ui,
    slot_msk_label,
)
from app.application.pipeline.postcards.topics import (
    DAILY_UTC,
    EVENING_UTC,
    MORNING_UTC,
    OFFICIAL_UTC,
)
from app.bot.handlers.ai_studio_entry import _session_expired, _show_blocks
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep

_QUEUE_SLOTS = (MORNING_UTC, EVENING_UTC)


def _slot_label(slot_time: str) -> str:
    msk = slot_msk_label(slot_time)
    if slot_time == MORNING_UTC:
        return f"Утро ({msk})"
    if slot_time == EVENING_UTC:
        return f"Вечер ({msk})"
    if slot_time == OFFICIAL_UTC:
        return f"Праздник ({msk})"
    if slot_time == DAILY_UTC:
        return f"Проф. день ({msk})"
    return f"{msk} МСК"


async def handle_postcard_callback(
    callback_data: str,
    max_user_id: int,
    max_client,
    channel_repo,
    session,
) -> bool:
    if not callback_data.startswith("ai:postcard:"):
        return False

    if callback_data == "ai:postcard:preset":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state or not state.get("channel_id"):
            await _session_expired(max_user_id, max_client)
            return True
        blocks = build_postcard_blocks_ui()
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
                "Preset «Живые открытки» применён.\n\n"
                "Слоты МСК: 07:23 утро, 11:37 праздник, 12:37 проф. день, 19:19 вечер.\n"
                "Заполните очереди тем для утра и вечера, затем запустите автопостинг."
            ),
        )
        return True

    if callback_data == "ai:postcard:topics":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        schedule = state.get("blocks", {}).get("schedule") or {}
        times = [t for t in (schedule.get("times") or []) if t in _QUEUE_SLOTS]
        if not times:
            times = list(_QUEUE_SLOTS)
        builder = InlineKeyboardBuilder()
        for t in times:
            # Reuse meditation topic-queue editor for the selected slot
            builder.row((_slot_label(t), f"ai:meditation:topics:pick:{t}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "Очереди тем для открыток\n\n"
                "Утро и вечер берут тему из очереди. "
                "Слоты 11:37 и 12:37 — из календаря праздников (очередь не нужна)."
            ),
            attachments=[builder.build()],
        )
        return True

    return False
