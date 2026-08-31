import json

from loguru import logger

from app.application.pipeline.generate_post import TopicDedupExhausted, generate_post_text
from app.application.pipeline.recent_topics import fetch_recent_post_topics
from app.bot.ai_studio_text_input import claim_text_input
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService

from app.bot.handlers.ai_studio_entry import (
    REDIS_TTL,
    REVIEW_TTL,
    _post_review_text,
    _session_expired,
    _show_blocks,
)
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline
from app.application.pipeline.blocks.post_gen import RELATED_CHANNELS_MAX
from app.application.pipeline.normalize import normalize_related_channels
from app.bot.texts.studio_hints import RELATED_CHANNELS_HINT, SUBSCRIBE_CTA_INTRO


def _parse_related_channel_manual(text: str) -> tuple[str, str] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    for sep in ("|", "—", "–"):
        if sep in raw:
            left, right = raw.split(sep, 1)
            title = left.strip()
            link = right.strip()
            if title and link.startswith("http"):
                return title, link
            return None
    if " - " in raw:
        left, right = raw.split(" - ", 1)
        title = left.strip()
        link = right.strip()
        if title and link.startswith("http"):
            return title, link
    return None


def _selected_connected_ids(related_channels: list[dict]) -> set[int]:
    ids: set[int] = set()
    for item in related_channels:
        if str(item.get("source") or "") != "connected":
            continue
        channel_id = item.get("channel_id")
        if channel_id is None:
            continue
        try:
            ids.add(int(channel_id))
        except (TypeError, ValueError):
            continue
    return ids


def _related_channels_summary(related_channels: list[dict]) -> str:
    if not related_channels:
        return "Пока ничего не выбрано."
    lines = [f"• {item.get('title', 'Канал')}" for item in related_channels]
    return "Выбрано:\n" + "\n".join(lines)


async def _continue_post_gen_after_related(
    max_user_id: int,
    max_client,
    mode: str,
) -> None:
    if mode == "ai":
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "📋 *Текст поста — AI*\n\n"
                "Жирный заголовок и подзаголовки помогают читать длинные посты.\n\n"
                "Использовать жирный для заголовков?"
            ),
            attachments=[InlineKeyboardBuilder.ai_post_gen_bold_toggle()],
            fmt="markdown",
        )
        return
    await _ask_brief(max_user_id, max_client, mode)


async def _ask_related_channels_toggle(max_user_id: int, max_client) -> None:
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "📋 *Текст поста — другие каналы*\n\n"
            f"{RELATED_CHANNELS_HINT}\n\n"
            "Добавить блок с другими каналами?"
        ),
        attachments=[InlineKeyboardBuilder.ai_post_gen_related_toggle()],
        fmt="markdown",
    )


async def _show_related_channels_picker(
    max_user_id: int,
    max_client,
    channel_repo,
    state: dict,
) -> None:
    current_channel_id = state.get("channel_id")
    channel = (
        await channel_repo.get_by_id(current_channel_id)
        if current_channel_id
        else None
    )
    owner_channels: list = []
    if channel is not None:
        owner_channels = await channel_repo.get_by_owner(channel.owner_id)

    post_block = state.get("blocks", {}).get("post_gen", {})
    related_channels = list(post_block.get("related_channels") or [])
    selected_ids = _selected_connected_ids(related_channels)

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "📋 *Другие каналы*\n\n"
            f"{_related_channels_summary(related_channels)}\n\n"
            "Отметьте подключённые каналы или добавьте вручную.\n"
            f"Формат ручного ввода: `Название | https://...`\n"
            f"Максимум {RELATED_CHANNELS_MAX} каналов."
        ),
        attachments=[
            InlineKeyboardBuilder.ai_post_gen_related_picker(
                owner_channels,
                current_channel_id=current_channel_id,
                selected_channel_ids=selected_ids,
                has_entries=bool(related_channels),
            )
        ],
        fmt="markdown",
    )


async def _ask_brief(max_user_id: int, max_client, mode: str) -> None:
    redis = await get_redis()
    await claim_text_input(redis, max_user_id, "post_gen", mode, REDIS_TTL)

    builder = InlineKeyboardBuilder()
    builder.row(("Назад к блокам", "ai:post_gen:cancel"))
    builder.row(("На главную", "main_menu"))

    if mode == "ai":
        prompt_text = (
            "📋 *Генерация поста — AI*\n\n"
            "Опиши *бриф / правила* для постов канала.\n"
            "Бот будет писать *новый пост при каждом запуске* пайплайна.\n\n"
            "Если канал на RSS/новостях — это не тема поста, а *как писать*: "
            "тон, табу, длина, без желтухи и т.п. Факты возьмутся из новости.\n\n"
            "Пример для городского канала: «дружелюбный тон, коротко, "
            "акцент на хороших новостях, не копируй заголовок СМИ один в один»\n\n"
            "Пример для обычного канала: «каждый пост — отдельный нетривиальный "
            "ПП-рецепт с КБЖУ, ингредиентами и шагами приготовления»"
        )
    else:
        prompt_text = (
            "📋 *Генерация поста — готовый текст*\n\n"
            "Отправь готовый текст поста одним сообщением:"
        )

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=prompt_text,
        attachments=[builder.build()],
        fmt="markdown",
    )


async def handle_post_callback(callback_data: str, max_user_id: int, max_client, channel_repo, session) -> bool:
    if callback_data.startswith("ai:edit:post_gen"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("post_gen", {})
        if not block.get("enabled"):
            await fsm.toggle_block(max_user_id, "post_gen")

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("post_gen", {})
        current_mode = block.get("mode", "ai")

        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"📋 *Текст поста — выбор режима*\n\n"
                f"Текущий: "
                f"{'AI (новый пост каждый запуск)' if current_mode == 'ai' else 'Готовый текст (один и тот же)'}\n\n"
                "*AI* — бот пишет новый пост при каждом выходе.\n"
                "*Готовый текст* — один и тот же текст каждый раз."
            ),
            attachments=[InlineKeyboardBuilder.ai_post_gen_mode_select()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:post_gen:mode:"):
        mode = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(max_user_id, "post_gen", {"mode": mode})
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        mode_display = "AI" if mode == "ai" else "Готовый текст"
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"📋 *Текст поста — {mode_display}*\n\n"
                f"{SUBSCRIBE_CTA_INTRO}\n\n"
                "Добавить призыв подписаться на канал?"
            ),
            attachments=[InlineKeyboardBuilder.ai_post_gen_link_toggle()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:post_gen:link:"):
        link = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("post_gen", {})
        mode = block.get("mode", "ai")
        logger.info(f"AI Studio post_gen link: mode={mode}, block_keys={list(block.keys())}")
        await fsm.set_block_data(max_user_id, "post_gen", {"add_channel_link": link == "yes"})

        channel = None
        if state.get("channel_id"):
            channel = await channel_repo.get_by_id(state["channel_id"])
        if link == "yes" and channel and not (channel.channel_link or "").strip():
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "Ссылка на канал пока не известна — бот подставит её, "
                    "когда канал полностью подключён. "
                    "Если ссылка не появится, проверьте, что бот — админ канала."
                ),
            )

        await _ask_related_channels_toggle(max_user_id, max_client)
        return True

    if callback_data.startswith("ai:block:post_gen:related:"):
        action = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("post_gen", {})
        mode = block.get("mode", "ai")

        if action == "no":
            await fsm.set_block_data(
                max_user_id,
                "post_gen",
                {"related_channels_enabled": False},
            )
            await _continue_post_gen_after_related(max_user_id, max_client, mode)
            return True

        if action == "yes":
            post_block = dict(block)
            if not post_block.get("related_channels_enabled"):
                await fsm.set_block_data(
                    max_user_id,
                    "post_gen",
                    {"related_channels_enabled": True},
                )
            state = await fsm.get_state(max_user_id) or state
            await _show_related_channels_picker(max_user_id, max_client, channel_repo, state)
            return True

        if action == "manual":
            redis = await get_redis()
            await claim_text_input(redis, max_user_id, "post_gen_related", "1", REDIS_TTL)
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "📋 *Добавить канал вручную*\n\n"
                    "Отправьте одной строкой:\n"
                    "`Название канала | https://...`"
                ),
                fmt="markdown",
            )
            return True

        if action == "done":
            state = await fsm.get_state(max_user_id) or state
            related = list(
                state.get("blocks", {}).get("post_gen", {}).get("related_channels") or []
            )
            if not related:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Выберите хотя бы один канал или нажмите «Нет» на предыдущем шаге.",
                )
                await _show_related_channels_picker(
                    max_user_id, max_client, channel_repo, state
                )
                return True
            normalized = normalize_related_channels(related)
            await fsm.set_block_data(
                max_user_id,
                "post_gen",
                {
                    "related_channels_enabled": True,
                    "related_channels": normalized,
                },
            )
            await _continue_post_gen_after_related(max_user_id, max_client, mode)
            return True

        if action == "pick":
            try:
                picked_id = int(callback_data.split(":")[5])
            except (IndexError, ValueError):
                return True
            if picked_id == state.get("channel_id"):
                return True

            related = list(block.get("related_channels") or [])
            selected_ids = _selected_connected_ids(related)
            if picked_id in selected_ids:
                related = [
                    item
                    for item in related
                    if not (
                        str(item.get("source") or "") == "connected"
                        and item.get("channel_id") == picked_id
                    )
                ]
            else:
                if len(related) >= RELATED_CHANNELS_MAX:
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Максимум {RELATED_CHANNELS_MAX} каналов в списке.",
                    )
                    state = await fsm.get_state(max_user_id) or state
                    await _show_related_channels_picker(
                        max_user_id, max_client, channel_repo, state
                    )
                    return True
                picked = await channel_repo.get_by_id(picked_id)
                if picked is None:
                    return True
                link = (picked.channel_link or "").strip()
                if not link:
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"У канала «{picked.title}» пока нет ссылки — "
                            "бот подставит её, когда канал полностью подключён."
                        ),
                    )
                related.append(
                    {
                        "title": picked.title,
                        "link": link,
                        "source": "connected",
                        "channel_id": picked.id,
                    }
                )
            related = normalize_related_channels(related)
            await fsm.set_block_data(
                max_user_id,
                "post_gen",
                {"related_channels": related, "related_channels_enabled": True},
            )
            state = await fsm.get_state(max_user_id) or state
            await _show_related_channels_picker(max_user_id, max_client, channel_repo, state)
            return True

        return True

    if callback_data.startswith("ai:block:post_gen:bold:"):
        value = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(max_user_id, "post_gen", {"bold_headings": value == "yes"})
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "📋 *Текст поста — AI*\n\n"
                "Эмодзи добавляют живости — можно отключить для строгого тона.\n\n"
                "Нужны ли эмодзи?"
            ),
            attachments=[InlineKeyboardBuilder.ai_post_gen_emoji_toggle()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:post_gen:emoji:"):
        value = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(max_user_id, "post_gen", {"use_emoji": value == "yes"})
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "📋 *Текст поста — AI*\n\n"
                "Если в канале подключены комментарии — бот учтёт это в тексте.\n\n"
                "Комментарии в канале подключены?\n\n"
                "Если нет — бот не будет просить читателей писать ответы "
                "и вместо этого предложит ставить реакции."
            ),
            attachments=[InlineKeyboardBuilder.ai_post_gen_comments_toggle()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:post_gen:comments:"):
        value = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(max_user_id, "post_gen", {"comments_enabled": value == "yes"})
        await _ask_brief(max_user_id, max_client, "ai")
        return True

    if callback_data.startswith("ai:post_gen:approve"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        redis = await get_redis()
        raw = await redis.get(f"ai_post_gen_review:{max_user_id}")
        if raw:
            review = json.loads(raw)
            data = {"user_input": review["input"]}
            if review["mode"] == "ai":
                # Preview only for info; runtime regenerates from user_input.
                data["generated_post"] = review.get("post", "")
            else:
                data["generated_post"] = review["input"]
            await fsm.set_block_data(max_user_id, "post_gen", data)
            await redis.delete(f"ai_post_gen_review:{max_user_id}")
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Время сессии истекло. Настрой пост заново.",
            )
            return True

        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:post_gen:regenerate":
        redis = await get_redis()
        raw = await redis.get(f"ai_post_gen_review:{max_user_id}")
        if not raw:
            await _session_expired(max_user_id, max_client)
            return True

        review = json.loads(raw)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="♻️ Перегенерирую пост...",
        )

        openai_client = OpenAIService()
        fsm = AIStudioFSM()
        st = await fsm.get_state(max_user_id)
        channel = await channel_repo.get_by_id(st["channel_id"]) if st and st.get("channel_id") else None
        ch_title = channel.title if channel else ""
        post_block = (st or {}).get("blocks", {}).get("post_gen", {})
        chat_id = channel.max_chat_id if channel else None
        recent_topics = await fetch_recent_post_topics(max_client, chat_id)
        from app.application.admin.billing_context import billing_user

        owner_id = channel.owner_id if channel else None
        try:
            with billing_user(owner_id):
                generated_post, _topic = await generate_post_text(
                    openai_client,
                    review["input"],
                    ch_title,
                    bold_headings=bool(post_block.get("bold_headings", True)),
                    use_emoji=bool(post_block.get("use_emoji", True)),
                    comments_enabled=bool(post_block.get("comments_enabled", False)),
                    recent_topics=recent_topics,
                )
        except TopicDedupExhausted as e:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"Не смог найти новую тему для «{e.channel_title}» "
                    f"после {e.attempts} попыток. Дубль не сохранён — "
                    "попробуй перегенерировать позже или измени бриф."
                ),
                attachments=[InlineKeyboardBuilder.ai_post_gen_review("ai")],
            )
            return True

        review["post"] = generated_post
        await redis.setex(f"ai_post_gen_review:{max_user_id}", REVIEW_TTL, json.dumps(review, ensure_ascii=False))

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=_post_review_text(generated_post),
            attachments=[InlineKeyboardBuilder.ai_post_gen_review("ai")],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:post_gen:edit_input":
        redis = await get_redis()
        raw = await redis.get(f"ai_post_gen_review:{max_user_id}")
        mode = "ai"
        if raw:
            review = json.loads(raw)
            mode = review.get("mode", "ai")
        await redis.delete(f"ai_post_gen_review:{max_user_id}")
        await claim_text_input(redis, max_user_id, "post_gen", mode, REDIS_TTL)

        builder = InlineKeyboardBuilder()
        builder.row(("Назад к блокам", "ai:post_gen:cancel"))
        builder.row(("На главную", "main_menu"))

        text = (
            "📋 Опиши бриф / правила заново:"
            if mode == "ai"
            else "📋 Отправь новый текст:"
        )
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=text,
            attachments=[builder.build()],
        )
        return True

    if callback_data == "ai:post_gen:cancel":
        redis = await get_redis()
        await redis.delete(f"ai_post_gen_wait:{max_user_id}")
        await redis.delete(f"ai_post_gen_related_wait:{max_user_id}")
        await redis.delete(f"ai_post_gen_review:{max_user_id}")

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if state:
            block = state.get("blocks", {}).get("post_gen", {})
            # AI mode is configured by brief; fixed by generated_post.
            has_config = (
                (block.get("mode") == "ai" and block.get("user_input"))
                or (block.get("mode") != "ai" and block.get("generated_post"))
            )
            if block.get("enabled") and not has_config:
                await fsm.toggle_block(max_user_id, "post_gen")
            await fsm.set_data(max_user_id, {"step": AIStudioStep.SELECT_FEATURES})
            state = await fsm.get_state(max_user_id)
            await sync_active_pipeline(session, state)
            await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Возвращаюсь на главную.",
                attachments=[InlineKeyboardBuilder.main_menu()],
            )
        return True

    return False


async def handle_post_message(max_user_id: int, message_text: str, redis) -> bool:
    related_wait_key = f"ai_post_gen_related_wait:{max_user_id}"
    if await redis.get(related_wait_key):
        await redis.delete(related_wait_key)
        parsed = _parse_related_channel_manual(message_text)
        async with async_session_factory() as session:
            channel_repo = SQLAlchemyChannelRepository(session)
            max_client = MaxAPIHTTPClient()
            try:
                if not parsed:
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Не удалось разобрать строку.\n"
                            "Пример: `Биохакинг | https://max.ru/bio`"
                        ),
                        fmt="markdown",
                    )
                    return True

                title, link = parsed
                fsm = AIStudioFSM()
                state = await fsm.get_state(max_user_id)
                if not state:
                    return True

                related = list(
                    state.get("blocks", {}).get("post_gen", {}).get("related_channels") or []
                )
                if len(related) >= RELATED_CHANNELS_MAX:
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Максимум {RELATED_CHANNELS_MAX} каналов в списке.",
                    )
                    return True

                related.append(
                    {"title": title, "link": link, "source": "manual"},
                )
                related = normalize_related_channels(related)
                await fsm.set_block_data(
                    max_user_id,
                    "post_gen",
                    {"related_channels": related, "related_channels_enabled": True},
                )
                state = await fsm.get_state(max_user_id) or state
                await _show_related_channels_picker(
                    max_user_id, max_client, channel_repo, state
                )
            finally:
                await max_client.close()
        return True

    post_wait_key = f"ai_post_gen_wait:{max_user_id}"
    wait_data = await redis.get(post_wait_key)
    if not wait_data:
        return False

    mode = wait_data.decode() if isinstance(wait_data, bytes) else wait_data
    logger.info(f"AI Studio post_gen message: mode_from_redis={mode}")
    await redis.delete(post_wait_key)

    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        openai_client = OpenAIService()
        channel_repo = SQLAlchemyChannelRepository(session)

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        channel = await channel_repo.get_by_id(state["channel_id"]) if state and state.get("channel_id") else None
        ch_title = channel.title if channel else ""
        post_block = (state or {}).get("blocks", {}).get("post_gen", {})

        if mode == "ai":
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="📋 Генерирую превью поста...",
            )

            chat_id = channel.max_chat_id if channel else None
            recent_topics = await fetch_recent_post_topics(max_client, chat_id)
            from app.application.admin.billing_context import billing_user

            try:
                with billing_user(channel.owner_id if channel else None):
                    generated_post, _topic = await generate_post_text(
                        openai_client,
                        message_text,
                        ch_title,
                        bold_headings=bool(post_block.get("bold_headings", True)),
                        use_emoji=bool(post_block.get("use_emoji", True)),
                        comments_enabled=bool(post_block.get("comments_enabled", False)),
                        recent_topics=recent_topics,
                    )
            except TopicDedupExhausted as e:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=(
                        f"Не смог найти новую тему для «{e.channel_title}» "
                        f"после {e.attempts} попыток. Дубль не сохранён — "
                        "попробуй другой бриф или повтори позже."
                    ),
                )
                return True

            review_data = json.dumps({
                "mode": "ai",
                "input": message_text,
                "post": generated_post,
            }, ensure_ascii=False)
            await redis.setex(f"ai_post_gen_review:{max_user_id}", REVIEW_TTL, review_data)

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "📋 *Превью поста* (при каждом запуске пайплайна будет новый текст "
                    "по твоему брифу)\n\n"
                    f"{generated_post[:3000]}"
                    + ("…" if len(generated_post) > 3000 else "")
                ),
                attachments=[InlineKeyboardBuilder.ai_post_gen_review("ai")],
                fmt="markdown",
            )
        else:
            review_data = json.dumps({
                "mode": "fixed",
                "input": message_text,
                "post": message_text,
            }, ensure_ascii=False)
            await redis.setex(f"ai_post_gen_review:{max_user_id}", REVIEW_TTL, review_data)

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"📋 *Готовый текст поста*\n\n"
                    f"{message_text[:3000]}"
                ),
                attachments=[InlineKeyboardBuilder.ai_post_gen_review("fixed")],
                fmt="markdown",
            )

        await max_client.close()
        return True
