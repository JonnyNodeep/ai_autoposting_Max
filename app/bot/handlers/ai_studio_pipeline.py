from loguru import logger

from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM
from app.infrastructure.services.openai_client import OpenAIService

from app.bot.handlers.ai_studio_entry import _escape_md, _model_name, _session_expired, _show_blocks
from app.bot.handlers.ai_studio_video import _run_video_test


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

        prompt_block = state.get("blocks", {}).get("image_prompt", {})
        generated_prompt = prompt_block.get("generated_prompt", "")

        if not generated_prompt:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Сначала настрой промпт для изображения в блоке «📝 Промпт для изображений».",
                attachments=[InlineKeyboardBuilder.ai_studio_blocks(state["blocks"])],
            )
            return True

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="🧪 Запускаю тест — генерирую изображение...",
        )

        openai_client = OpenAIService()
        logger.info(f"AI Studio test: generating image, prompt_len={len(generated_prompt)}")
        image_url = await openai_client.generate_image(
            prompt=generated_prompt,
            channel_link=None,
        )
        logger.info(f"AI Studio test: image generated, url_preview={image_url[:120] if image_url else 'empty'}")

        attachments = [InlineKeyboardBuilder.ai_studio_blocks(state["blocks"])]
        if image_url:
            if image_url.startswith("http://") or image_url.startswith("https://"):
                payload = {"url": image_url}
                logger.info(f"AI Studio test: using external URL for image")
            else:
                logger.info(f"AI Studio test: uploading local file to MAX: {image_url}")
                token = await max_client.upload_file(image_url, "image")
                payload = {"token": token}
                logger.info(f"AI Studio test: MAX upload done, token={token[:40]}")
            attachments.insert(0, {"type": "image", "payload": payload})

        logger.info(f"AI Studio test: sending result to user {max_user_id}")
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🧪 *Тест — {ch_title}*\n\n"
                f"Модель: {_model_name(state['blocks'].get('image_gen', {}).get('model', ''))}\n"
                f"Промпт:\n`{generated_prompt[:300]}`"
            ),
            attachments=attachments,
            fmt="markdown",
        )

        video_block = state.get("blocks", {}).get("video_gen", {})
        video_token = None
        if video_block.get("enabled") and video_block.get("generated_prompt") and image_url:
            video_token = await _run_video_test(
                max_user_id=max_user_id,
                max_client=max_client,
                state=state,
                image_url=image_url,
                ch_title=ch_title,
                channel_link=channel.channel_link if channel else "",
            )

        post_block = state.get("blocks", {}).get("post_gen", {})
        if post_block.get("enabled") and post_block.get("generated_post"):
            post_text = post_block["generated_post"]

            if post_block.get("add_channel_link") and channel and channel.channel_link:
                post_text += f"\n\n**👉 [Подпишись на {_escape_md(ch_title)}]({channel.channel_link})**"

            combined_attachments = []
            if video_token:
                combined_attachments.append({"type": "video", "payload": {"token": video_token}})

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=post_text[:3800],
                attachments=combined_attachments if combined_attachments else None,
                fmt="markdown",
            )
        elif video_token:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="🎬",
                attachments=[{"type": "video", "payload": {"token": video_token}}],
                fmt="markdown",
            )
        return True

    return False
