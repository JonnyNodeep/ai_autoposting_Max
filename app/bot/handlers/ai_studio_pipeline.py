from typing import Any

from loguru import logger

from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM
from app.infrastructure.services.openai_client import OpenAIService

from app.bot.handlers.ai_studio_entry import _model_name, _session_expired, _show_blocks


async def sync_active_pipeline(session, state: dict[str, Any] | None) -> bool:
    """Push FSM blocks into the active run (if any). Returns True if a run was touched."""
    if not state or not state.get("channel_id"):
        return False

    from app.application.pipeline.manage_pipeline import PipelineManager
    from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository

    repo = SQLAPipelineRunRepository(session)
    mgr = PipelineManager(repo)
    active = await mgr.get_active_for_channel(state["channel_id"])
    if not active:
        return False

    await mgr.update_active_config(state["channel_id"], state.get("blocks") or {})
    await session.commit()
    return True


async def handle_pipeline_callback(
    callback_data: str,
    max_user_id: int,
    max_client,
    session,
    user_id: int | None,
    channel_repo,
    cb: dict,
) -> bool:
    if callback_data == "ai:pipeline:start":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        channel = await channel_repo.get_by_id(state["channel_id"]) if state.get("channel_id") else None
        sched_block = state.get("blocks", {}).get("schedule", {})
        if not sched_block.get("enabled"):
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Сначала настрой расписание в блоке «⏱ Расписание публикаций».",
            )
            return True
        if not sched_block.get("times"):
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Сначала выбери время публикации в блоке «⏱ Расписание публикаций».",
            )
            return True

        from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
        from app.application.pipeline.manage_pipeline import PipelineManager

        repo = SQLAPipelineRunRepository(session)
        mgr = PipelineManager(repo)
        await mgr.start(
            user_id=user_id,
            max_user_id=max_user_id,
            channel_id=state["channel_id"],
            channel_link=channel.channel_link if channel else "",
            blocks_config=state["blocks"],
            frequency=sched_block["frequency"],
            times=sched_block["times"],
        )
        await session.commit()
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:pipeline:stop":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
        from app.application.pipeline.manage_pipeline import PipelineManager

        repo = SQLAPipelineRunRepository(session)
        mgr = PipelineManager(repo)
        await mgr.stop_by_channel(state["channel_id"])
        await session.commit()

        state = await fsm.get_state(max_user_id)
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:pipeline:info":
        await max_client.answer_callback(
            cb.get("callback_id", ""),
            text="Пайплайн активен. Посты будут выходить по расписанию.",
        )
        return True

    if callback_data == "ai:blocks:test":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        channel = await channel_repo.get_by_id(state["channel_id"]) if state.get("channel_id") else None
        ch_title = channel.title if channel else ""
        blocks = state.get("blocks", {})

        prompt_block = blocks.get("image_prompt", {})
        generated_prompt = prompt_block.get("generated_prompt", "")
        post_block = blocks.get("post_gen", {})
        post_text = (post_block.get("generated_post") or "").strip()
        post_brief = (post_block.get("user_input") or "").strip()
        has_legacy_prompt = bool(generated_prompt)
        has_from_post_fixed = bool(
            prompt_block.get("enabled")
            and prompt_block.get("mode") == "from_post"
            and post_text
            and post_block.get("mode") != "ai"
        )
        has_from_post_ai = bool(
            prompt_block.get("enabled")
            and prompt_block.get("mode") == "from_post"
            and post_block.get("mode") == "ai"
            and post_brief
        )
        has_from_post = has_from_post_fixed or has_from_post_ai

        if not has_legacy_prompt and not has_from_post:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "Сначала настрой «📝 Промпт для изображений» "
                    "(готовый/AI промпт или режим «Картинка по тексту поста» "
                    "вместе с текстом/брифом в «📋 Генерация поста»)."
                ),
                attachments=[InlineKeyboardBuilder.ai_studio_blocks(blocks)],
            )
            return True

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="🧪 Запускаю тест — генерирую контент...",
        )

        from app.application.pipeline.context import PipelineContext
        from app.application.pipeline.runner import PipelineRunner

        openai_client = OpenAIService()

        async def _on_progress(text: str) -> None:
            # ImageGen already sends its own start notify; skip duplicate first line
            if "Генерирую изображение" in text:
                return
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=text,
                fmt="markdown",
            )

        ctx = PipelineContext(
            channel=channel,
            channel_link=(channel.channel_link if channel else "") or "",
            run_id=None,
            max_client=max_client,
            openai_client=openai_client,
            target="user",
            target_user_id=max_user_id,
            channel_title=ch_title,
            on_progress=_on_progress,
            meta={
                "image_model_name": _model_name(blocks.get("image_gen", {}).get("model", "")),
                "preview_keyboard": InlineKeyboardBuilder.ai_studio_blocks(blocks),
            },
        )
        logger.info(f"AI Studio test: running PipelineRunner for user={max_user_id}")
        await PipelineRunner().run(ctx, blocks)
        return True

    return False
