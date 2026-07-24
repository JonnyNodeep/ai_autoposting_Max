class ContentPrompts:
    @staticmethod
    def analyze_style(topic: str, description: str | None, sample_posts: list[str]) -> tuple[str, str]:
        system = (
            "Ты — AI-стилист контента. Проанализируй примеры постов из канала "
            "и определи стиль. Отвечай ТОЛЬКО валидным JSON, без markdown-блоков. "
            "Все строковые значения полей ДОЛЖНЫ быть на русском языке."
        )
        posts_text = "\n---\n".join(sample_posts) if sample_posts else "нет примеров"
        user = (
            f"Проанализируй стиль постов канала.\n\n"
            f"Тематика канала: {topic}\n"
            f"Описание канала: {description or 'нет'}\n"
            f"Примеры постов:\n{posts_text}\n\n"
            f"Верни JSON со всеми строковыми значениями на русском языке:\n"
            f"- tone: тональность (экспертный/дружелюбный/формальный/непринуждённый/вдохновляющий)\n"
            f'- audience: целевая аудитория (строка, например "владельцы бизнеса")\n'
            f"- topics: список из 3-5 ключевых тем на русском\n"
            f"- format_preference: формат (пост/статья/короткая заметка)\n"
            f"- avg_length: средняя длина поста в символах (число)\n"
            f"- features: список особенностей на русском (эмодзи/хештеги/буллиты/вопросы/сторителлинг/хуки/факты)"
        )
        return system, user

    @staticmethod
    def generate_description(title: str, topic: str, style_profile: dict) -> tuple[str, str]:
        system = (
            "Ты — SEO-специалист по контенту. Создай описание канала "
            "для лучшего ранжирования в поиске. Отвечай только текстом, без маркдауна."
        )
        tone = style_profile.get("tone", "friendly")
        topics_list = ", ".join(style_profile.get("topics", [])) or topic
        user = (
            f"Напиши SEO-описание для канала.\n\n"
            f"Название: {title}\n"
            f"Тематика: {topic}\n"
            f"Ключевые темы: {topics_list}\n"
            f"Тональность: {tone}\n"
            f"Аудитория: {style_profile.get('audience', 'широкая')}\n\n"
            f"Требования:\n"
            f"- 2-3 предложения\n"
            f"- До 500 символов\n"
            f"- Упомяни 2-3 ключевые темы\n"
            f"- Пиши на русском языке"
        )
        return system, user

    @staticmethod
    def generate_logo_prompt(title: str, topic: str, style_profile: dict) -> str:
        tone = style_profile.get("tone", "friendly")
        topics_list = ", ".join(style_profile.get("topics", [])) or topic
        return (
            f"Minimalist logo for a channel called '{title}'. "
            f"Topic: {topic}. Key themes: {topics_list}. "
            f"Tone: {tone}. Style: flat design, clean lines, modern, "
            f"professional but friendly. No text in the image. "
            f"Vector style icon."
        )

    @staticmethod
    def generate_topics(
        title: str, topic: str, style_profile: dict, duration_days: int, topic_count: int,
        user_prefs: str | None = None,
    ) -> tuple[str, str]:
        system = (
            "Ты — AI-контент-менеджер. Придумай темы для постов. "
            "Отвечай ТОЛЬКО списком тем, каждая на новой строке, "
            "без нумерации, без пояснений."
        )
        tone = style_profile.get("tone", "friendly")
        audience = style_profile.get("audience", "широкая аудитория")
        topics_list = ", ".join(style_profile.get("topics", [])) or topic

        prefs_line = ""
        if user_prefs:
            prefs_line = (
                f"ВАЖНО — пожелания пользователя к темам: {user_prefs}\n"
                f"Строго учти их при выборе тем.\n\n"
            )

        user = (
            f"Придумай {topic_count} тем для постов.\n\n"
            f"Канал: {title}\n"
            f"Тематика: {topic}\n"
            f"Ключевые темы: {topics_list}\n"
            f"Тональность: {tone}\n"
            f"Аудитория: {audience}\n"
            f"Период: {duration_days} дней\n\n"
            f"{prefs_line}"
            f"Требования к темам:\n"
            f"- Каждая тема — 1 строка, 5-15 слов\n"
            f"- Темы должны быть разными, не повторяться\n"
            f"- Учитывай интересы аудитории\n"
            f"- Чередуй форматы: практические советы, новости, аналитика, вдохновение, вопросы"
        )
        return system, user

    @staticmethod
    def generate_post(
        title: str, topic_text: str, style_profile: dict, sample_posts: list[str],
        post_settings: dict | None = None, channel_link: str = "",
        search_results: str | None = None,
    ) -> tuple[str, str]:
        system = (
            "Ты — профессиональный автор контента. Пиши интересные, вовлекающие посты. "
            "Отвечай ТОЛЬКО валидным JSON, без markdown-блоков. "
            "Используй **жирный текст** для ключевых терминов, цифр и важных моментов. "
            "Добавляй уместные эмодзи. Делай отступы между смысловыми блоками. "
            "Разбивай текст на короткие абзацы по 2-4 предложения. "
            "НЕ пиши сплошным текстом."
        )
        tone = style_profile.get("tone", "friendly")
        avg_len = style_profile.get("avg_length", 500)
        audience = style_profile.get("audience", "широкая аудитория")
        features = ", ".join(style_profile.get("features", []))
        samples_text = "\n---\n".join(sample_posts[-5:]) if sample_posts else "нет примеров"

        settings = post_settings or {}
        extra_lines = ""
        if settings.get("subscribe_cta") and channel_link:
            extra_lines += (
                f"- В КОНЦЕ поста ОБЯЗАТЕЛЬНО добавь кликабельную ссылку "
                f"[Подпишись на канал]({channel_link}) — даже если в примерах этого нет\n"
            )
        if settings.get("share_cta"):
            extra_lines += (
                "- Поле CTA (cta) ДОЛЖНО содержать призыв поделиться постом с другом, "
                "НО НЕ дублируй этот же текст в поле text\n"
            )
        if settings.get("same_style"):
            extra_lines += (
                "- СТРОГО соблюдай тот же стиль, тональность и структуру что в примерах постов\n"
            )
        if settings.get("match_format"):
            extra_lines += (
                "- ПРОАНАЛИЗИРУЙ формат постов в примерах. Определи тип контента: "
                "это рецепт? обзор? инструкция? новость? лайфхак?\n"
                "- Напиши пост СТРОГО того же типа контента что в примерах. "
                "Если примеры — рецепты, пиши рецепт с ингредиентами и пошаговой инструкцией. "
                "Если примеры — обзоры, пиши обзор. НЕ МЕНЯЙ тип контента.\n"
                "- Сохрани структуру из примеров: те же разделы, такое же форматирование, "
                "такой же способ подачи информации.\n"
            )

        if style_profile.get("visual_style"):
            extra_lines += (
                f"- Промпт изображения (image_prompt) ОБЯЗАТЕЛЬНО должен соответствовать "
                f"визуальному стилю канала: {style_profile['visual_style']}\n"
            )

        if settings.get("comments_enabled") is False:
            extra_lines += (
                "- НЕ упоминай «пишите в комментариях», «обсудим в комментариях», "
                "«делитесь мнением» и подобные призывы к обсуждению — "
                "комментарии в канале отключены. Реакции и лайки упоминать МОЖНО.\n"
            )

        search_block = ""
        if search_results:
            sources_instruction = ""
            if settings.get("show_sources"):
                sources_instruction = "Укажи источники информации в конце поста.\n"
            search_block = (
                f"\n\nРЕЗУЛЬТАТЫ ПОИСКА В ИНТЕРНЕТЕ ПО ТЕМЕ «{topic_text}»:\n"
                f"{search_results}\n\n"
                f"ВАЖНО: Используй ТОЛЬКО факты из результатов поиска выше. "
                f"НЕ придумывай ничего от себя. "
                f"Перепиши информацию в стиле канала, сохранив точность фактов. "
                f"{sources_instruction}"
            )

        user = (
            f"Напиши пост для канала.\n\n"
            f"Название канала: {title}\n"
            f"Тема поста: {topic_text}\n"
            f"Тональность: {tone}\n"
            f"Аудитория: {audience}\n"
            f"Особенности стиля: {features}\n"
            f"Примеры постов (для ориентира по стилю):\n{samples_text}"
            f"{search_block}\n\n"
            f"Требования:\n"
            f"- Заголовок (title): яркий, до 100 символов\n"
            f"- Текст (text): {avg_len}-{avg_len+300} символов, "
            f"структурированный, с примерами\n"
            f"- CTA (cta): 1 вовлекающее предложение в конце. "
            f"Поле text НЕ должно дублировать этот же текст CTA.\n"
            f"{extra_lines}"
            f"- Промпт изображения (image_prompt): 1 предложение на РУССКОМ, описывающее визуал для поста\n\n"
            f"Ответ — ТОЛЬКО JSON:\n"
            f'{{"title": "...", "text": "...", "cta": "...", "image_prompt": "..."}}'
        )
        return system, user

    EDIT_INSTRUCTIONS: dict[str, str] = {
        "shorter": "Сократи текст на 30-50%. Сохрани ключевые мысли, убери лишние слова.",
        "longer": "Расширь текст на 30-50%. Добавь примеры, детали, раскрой тему глубже.",
        "expert": "Сделай текст экспертнее. Добавь терминологию, цифры, строгий тон.",
        "friendly": "Сделай текст дружелюбнее. Упрости лексику, пиши теплее.",
        "facts": "Добавь в текст конкретные факты, цифры или данные по теме.",
        "rewrite": "Перепиши пост полностью. Та же тема, другой угол подачи.",
    }

    @staticmethod
    def edit_post(
        title: str, text: str, cta: str, edit_type: str, style_profile: dict | None = None,
    ) -> tuple[str, str]:
        instruction = ContentPrompts.EDIT_INSTRUCTIONS.get(edit_type, "Перепиши пост заново.")
        tone = style_profile.get("tone", "friendly") if style_profile else "friendly"
        audience = style_profile.get("audience", "широкая аудитория") if style_profile else "широкая аудитория"

        system = (
            "Ты — профессиональный редактор. Отредактируй пост согласно инструкции. "
            "Отвечай ТОЛЬКО валидным JSON, без markdown-блоков."
        )
        user = (
            f"Инструкция: {instruction}\n\n"
            f"Заголовок: {title}\n"
            f"Текст: {text}\n"
            f"CTA: {cta}\n"
            f"Тон: {tone}\n"
            f"Аудитория: {audience}\n\n"
            f'Ответ — JSON: {{"title": "...", "text": "...", "cta": "..."}}'
        )
        return system, user
