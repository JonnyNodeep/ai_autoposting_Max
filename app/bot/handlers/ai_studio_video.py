import json

from loguru import logger

from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep, VIDEO_MODELS
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService
from app.infrastructure.services.vidgo_client import VidGoClient

from app.bot.handlers.ai_studio_entry import (
    REDIS_TTL,
    REVIEW_TTL,
    _generate_video_prompt,
    _session_expired,
    _show_blocks,
    _video_model_name,
)


async def handle_video_callback(callback_data: str, max_user_id: int, max_client, channel_repo) -> bool:
    if callback_data.startswith("ai:edit:video_gen"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("video_gen", {})
        if not block.get("enabled"):
            await fsm.toggle_block(max_user_id, "video_gen")

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("video_gen", {})
        current_model = block.get("model", VIDEO_MODELS[0][0])

        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🎬 *Генерация видео — выбор модели*\n\n"
                f"Текущая: {_video_model_name(current_model)}"
            ),
            attachments=[InlineKeyboardBuilder.ai_video_model_select(current_model)],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:video_gen:model:"):
        model_id = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        defaults = {
            "grok-imagine": {"duration": 6, "mode": "normal"},
            "wan2.5-image-to-video": {"duration": 5, "resolution": "720p"},
        }
        block_data = {"model": model_id}
        block_data.update(defaults.get(model_id, {}))
        await fsm.set_block_data(max_user_id, "video_gen", block_data)
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("video_gen", {})
        current_pmode = block.get("prompt_mode", "ai")

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🎬 *Генерация видео — {_video_model_name(model_id)}*\n\n"
                f"Режим промпта: {'AI' if current_pmode == 'ai' else 'Готовый промпт'}"
            ),
            attachments=[InlineKeyboardBuilder.ai_prompt_mode_select("video_gen")],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:video_gen:mode:"):
        mode = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(max_user_id, "video_gen", {"prompt_mode": mode})

        redis = await get_redis()
        await redis.setex(f"ai_video_prompt_wait:{max_user_id}", REDIS_TTL, mode)

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("video_gen", {})
        model_id = block.get("model", "grok-imagine")

        builder = InlineKeyboardBuilder()
        builder.row(("Назад к блокам", "ai:video_prompt:cancel"))
        builder.row(("На главную", "main_menu"))

        if mode == "ai":
            prompt_text = (
                f"🎬 *Генерация видео — {_video_model_name(model_id)} (AI)*\n\n"
                "Опиши движение в кадре.\n\n"
                "Например: «медленный зум на лицо, мягкий свет, "
                "камера плавно отъезжает»"
            )
        else:
            prompt_text = (
                f"🎬 *Генерация видео — {_video_model_name(model_id)} (готовый)*\n\n"
                "Отправь готовый промпт одним сообщением:"
            )

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=prompt_text,
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:video_prompt:approve"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        redis = await get_redis()
        raw = await redis.get(f"ai_video_prompt_review:{max_user_id}")
        if raw:
            review = json.loads(raw)
            await fsm.set_block_data(max_user_id, "video_gen", {
                "user_description": review["description"],
                "generated_prompt": review["prompt"],
            })
            await redis.delete(f"ai_video_prompt_review:{max_user_id}")
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Время сессии истекло. Настрой промпт заново.",
            )
            return True

        state = await fsm.get_state(max_user_id)
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:video_prompt:regenerate":
        redis = await get_redis()
        raw = await redis.get(f"ai_video_prompt_review:{max_user_id}")
        if not raw:
            await _session_expired(max_user_id, max_client)
            return True

        review = json.loads(raw)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="♻️ Перегенерирую видеопромпт...",
        )

        openai_client = OpenAIService()
        generated_prompt = await _generate_video_prompt(openai_client, review["description"])

        review["prompt"] = generated_prompt
        await redis.setex(f"ai_video_prompt_review:{max_user_id}", REVIEW_TTL, json.dumps(review, ensure_ascii=False))

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🎬 *Видеопромпт*\n\n"
                f"Твоё описание: _{review['description'][:200]}_\n\n"
                f"Новый промпт:\n`{generated_prompt[:800]}`"
            ),
            attachments=[InlineKeyboardBuilder.ai_video_prompt_review()],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:video_prompt:edit_desc":
        redis = await get_redis()
        await redis.delete(f"ai_video_prompt_review:{max_user_id}")

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        mode = "ai"
        if state:
            mode = state.get("blocks", {}).get("video_gen", {}).get("prompt_mode", "ai")
        await redis.setex(f"ai_video_prompt_wait:{max_user_id}", REDIS_TTL, mode)

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("video_gen", {}) if state else {}
        model_id = block.get("model", "grok-imagine")

        builder = InlineKeyboardBuilder()
        builder.row(("Назад к блокам", "ai:video_prompt:cancel"))
        builder.row(("На главную", "main_menu"))

        text = f"🎬 Опиши заново движение в кадре:" if mode == "ai" else "🎬 Отправь новый промпт:"
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=text,
            attachments=[builder.build()],
        )
        _ = model_id
        return True

    if callback_data == "ai:video_prompt:cancel":
        redis = await get_redis()
        await redis.delete(f"ai_video_prompt_wait:{max_user_id}")
        await redis.delete(f"ai_video_prompt_review:{max_user_id}")

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if state:
            block = state.get("blocks", {}).get("video_gen", {})
            if block.get("enabled") and not block.get("generated_prompt"):
                await fsm.toggle_block(max_user_id, "video_gen")
            await fsm.set_data(max_user_id, {"step": AIStudioStep.SELECT_FEATURES})
            state = await fsm.get_state(max_user_id)
            await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Возвращаюсь на главную.",
                attachments=[InlineKeyboardBuilder.main_menu()],
            )
        return True

    return False


async def handle_video_message(max_user_id: int, message_text: str, redis) -> bool:
    video_wait_key = f"ai_video_prompt_wait:{max_user_id}"
    video_wait_data = await redis.get(video_wait_key)
    if not video_wait_data:
        return False

    video_mode = video_wait_data.decode() if isinstance(video_wait_data, bytes) else video_wait_data
    await redis.delete(video_wait_key)

    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        openai_client = OpenAIService()

        if video_mode == "fixed":
            review_data = json.dumps({
                "description": message_text,
                "prompt": message_text,
            }, ensure_ascii=False)
            await redis.setex(f"ai_video_prompt_review:{max_user_id}", REVIEW_TTL, review_data)

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"🎬 *Готовый видеопромпт*\n\n"
                    f"{message_text[:2000]}"
                ),
                attachments=[InlineKeyboardBuilder.ai_video_prompt_review("fixed")],
                fmt="markdown",
            )
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="🎬 Генерирую видеопромпт...",
            )

            generated_prompt = await _generate_video_prompt(openai_client, message_text)

            review_data = json.dumps({
                "description": message_text,
                "prompt": generated_prompt,
            }, ensure_ascii=False)
            await redis.setex(f"ai_video_prompt_review:{max_user_id}", REVIEW_TTL, review_data)

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"🎬 *Видеопромпт*\n\n"
                    f"Твоё описание: _{message_text[:200]}_\n\n"
                    f"Готовый промпт:\n`{generated_prompt[:800]}`"
                ),
                attachments=[InlineKeyboardBuilder.ai_video_prompt_review("ai")],
                fmt="markdown",
            )

        await max_client.close()
        _ = session
        return True


async def _run_video_test(
    max_user_id: int,
    max_client: MaxAPIHTTPClient,
    state: dict,
    image_url: str,
    ch_title: str,
    channel_link: str = "",
) -> str | None:
    video_block = state["blocks"]["video_gen"]

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text="🎬 Загружаю изображение в VidGo...",
    )

    vidgo = VidGoClient()
    try:
        if image_url.startswith("http://") or image_url.startswith("https://"):
            vidgo_image_url = image_url
        else:
            vidgo_image_url = await vidgo.upload_image(image_url)

        logger.info(f"AI Studio test: submitting video, model={video_block['model']}")
        task_id = await vidgo.submit_video(
            model=video_block["model"],
            prompt=video_block["generated_prompt"],
            image_url=vidgo_image_url,
            duration=video_block.get("duration", 6),
            mode=video_block.get("mode", "normal"),
            resolution=video_block.get("resolution", "720p"),
            task_meta={
                "kind": "ai_test",
                "max_user_id": max_user_id,
                "channel_link": channel_link,
            },
        )

        model_display = ""
        for m_id, m_name in VIDEO_MODELS:
            if m_id == video_block["model"]:
                model_display = m_name
                break

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🎬 *Генерация видео — {ch_title}*\n\n"
                f"Модель: {model_display}\n"
                f"Статус: обрабатывается...\n"
                f"Это может занять несколько минут."
            ),
            fmt="markdown",
        )

        async def _on_progress(elapsed: int, _progress: int) -> None:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=f"🎬 *Генерация видео — {ch_title}*\n\nГенерация: {elapsed // 60} мин...",
                fmt="markdown",
            )

        result = await vidgo.wait_for_task(task_id, timeout=900, on_progress=_on_progress)
        video_url = result["files"][0]["file_url"]

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"📥 Скачиваю видео и загружаю в MAX...",
        )

        import tempfile
        import httpx
        from pathlib import Path

        tmp_path = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as dl_client:
                dl_response = await dl_client.get(video_url)
                dl_response.raise_for_status()

            suffix = Path(video_url).suffix or ".mp4"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(dl_response.content)
                tmp_path = f.name

            logger.info(f"AI Studio test: video downloaded to {tmp_path}")

            if channel_link:
                from app.infrastructure.services.openai_client import _apply_video_watermark
                slug = channel_link.rstrip("/").split("/")[-1]
                watermarked = str(Path(tmp_path).parent / f"wm_{Path(tmp_path).name}")
                _apply_video_watermark(tmp_path, watermarked, slug)
                Path(tmp_path).unlink()
                tmp_path = watermarked
                logger.info(f"AI Studio test: watermark applied, slug={slug}")

            max_token = await max_client.upload_file(tmp_path, "video")
            logger.info(f"AI Studio test: video uploaded to MAX, token={max_token[:40]}")

        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink()
                except Exception:
                    pass

        return max_token

    except Exception as e:
        logger.exception(f"AI Studio test: video generation failed")
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"❌ Ошибка генерации видео: {str(e)[:300]}",
        )
    finally:
        await vidgo.close()
    return None
