from __future__ import annotations

from typing import Any


async def generate_post_text(
    openai_client: Any,
    brief: str,
    channel_title: str,
    *,
    bold_headings: bool = True,
    use_emoji: bool = True,
    comments_enabled: bool = False,
    recent_topics: list[str] | None = None,
) -> str:
    """Generate a channel post from a recurring brief and style flags."""
    system_prompt = (
        "Ты — профессиональный копирайтер и автор контента для каналов MAX. "
        "Твоя задача — каждый раз писать новый интересный, вовлекающий пост "
        "по брифу пользователя. Не копируй предыдущие посты дословно."
    )

    bold_rule = (
        "- Заголовок и подзаголовки оформи жирным markdown: **текст**"
        if bold_headings
        else "- Не используй жирное выделение для заголовков и подзаголовков"
    )
    emoji_rule = (
        "- Используй заметно больше уместных эмодзи в заголовках и по тексту "
        "(секции, списки, CTA), но без перебора и без спама каждым словом"
        if use_emoji
        else "- Не используй эмодзи"
    )
    if comments_enabled:
        cta_rule = (
            "- CTA в конце ОБЯЗАТЕЛЬНО: призыв поделиться с друзьями "
            "(например «поделитесь с друзьями, если пригодилось»); "
            "можно также предложить реакции, сохранить, написать в комментариях"
        )
    else:
        cta_rule = (
            "- Комментарии в канале НЕ подключены: НЕ задавай вопросов читателям, "
            "НЕ проси ничего написать / ответить в комментариях / оставить отзыв текстом. "
            "- CTA в конце ОБЯЗАТЕЛЬНО: призыв поделиться с друзьями "
            "(например «поделитесь с друзьями, если рецепт пригодился»); "
            "плюс реакции, если понравилось — например «ставьте реакции, если понравилось»; "
            "можно сохранить / подписаться"
        )

    topics = [t.strip() for t in (recent_topics or []) if t and t.strip()]
    if topics:
        listed = "\n".join(f"- {t}" for t in topics[:25])
        avoid_block = (
            f"Уже недавно публиковались темы/рецепты:\n{listed}\n\n"
            f"НЕ повторяй их (ни те же блюда/темы, ни близкие вариации с тем же "
            f"главным ингредиентом или названием). Придумай другую тему.\n\n"
        )
    else:
        avoid_block = ""

    user_prompt = (
        f"Напиши пост для канала «{channel_title}» по этому брифу/правилам:\n\n"
        f"«{brief}»\n\n"
        f"{avoid_block}"
        f"Правила:\n"
        f"- Язык: русский\n"
        f"- Каждый раз придумывай новый уникальный пост по брифу\n"
        f"- Заголовок: яркий, привлекающий внимание\n"
        f"- Текст: информативный, полезный, с фактами и примерами\n"
        f"{cta_rule}\n"
        f"- Длина: 600-2000 символов\n"
        f"{bold_rule}\n"
        f"{emoji_rule}\n"
        f"- Ответ — ТОЛЬКО готовый пост, без пояснений"
    )
    result = await openai_client.generate_text(prompt=user_prompt, system_prompt=system_prompt)
    return result.strip()
