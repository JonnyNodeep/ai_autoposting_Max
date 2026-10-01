"""Astro Oracle (horoscope) channel preset."""
from __future__ import annotations

from app.application.pipeline.horoscope_presets import (
    EVENING_UTC,
    MIDDAY_UTC,
    MORNING_UTC,
    build_horoscope_blocks_ui,
    slot_msk_label,
)
from app.bot.handlers.ai_studio_entry import _session_expired, _show_blocks
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep


async def handle_horoscope_callback(
    callback_data: str,
    max_user_id: int,
    max_client,
    channel_repo,
    session,
) -> bool:
    if not callback_data.startswith("ai:horoscope:"):
        return False

    if callback_data == "ai:horoscope:preset":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state or not state.get("channel_id"):
            await _session_expired(max_user_id, max_client)
            return True
        blocks = build_horoscope_blocks_ui()
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
                "Preset «Астро-оракул» применён.\n\n"
                f"Слоты МСК: {slot_msk_label(MORNING_UTC)} утро (12 знаков), "
                f"{slot_msk_label(MIDDAY_UTC)} день (рубрика/совместимость), "
                f"{slot_msk_label(EVENING_UTC)} вечер (прогноз/интерактив).\n"
                "Очередь тем не нужна — дата, день недели и луна подставляются сами.\n"
                "Запустите автопостинг, когда будете готовы."
            ),
        )
        return True

    return False
