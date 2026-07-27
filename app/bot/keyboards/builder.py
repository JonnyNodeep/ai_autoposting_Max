from typing import Any

from app.config import settings


class InlineKeyboardBuilder:
    def __init__(self) -> None:
        self._rows: list[list[dict[str, Any]]] = []

    def add_button(self, text: str, callback_data: str, row: int = 0, button_type: str = "callback", url: str = "") -> "InlineKeyboardBuilder":
        while len(self._rows) <= row:
            self._rows.append([])
        btn: dict[str, Any] = {"type": button_type, "text": text}
        if button_type == "link":
            btn["url"] = url or callback_data
        else:
            btn["payload"] = callback_data
        self._rows[row].append(btn)
        return self

    def row(self, *buttons: tuple) -> "InlineKeyboardBuilder":
        row_index = len(self._rows)
        for btn_spec in buttons:
            text = btn_spec[0]
            data = btn_spec[1]
            btn_type = btn_spec[2] if len(btn_spec) > 2 else "callback"
            url = btn_spec[3] if len(btn_spec) > 3 else ""
            self.add_button(text, data, row=row_index, button_type=btn_type, url=url)
        return self

    def build(self) -> dict[str, Any]:
        return {"type": "inline_keyboard", "payload": {"buttons": self._rows}}

    @classmethod
    def main_menu(cls, max_user_id: int | None = None, channels_used: int = 0, channels_limit: int = 0) -> dict[str, Any]:
        if channels_limit > 0:
            channels_label = f"📡 Каналы ({channels_used}/{channels_limit})"
        else:
            channels_label = "📡 Каналы"
        result = (
            cls()
            .row((channels_label, "channels:list"), ("💳 Подписка", "subscription:status"))
            .row(("➕ Добавить канал", "channels:add"))
            .row(("🤖 AI Content Studio", "ai_studio"))
            .row(("Помощь", "help"), ("Настройки", "settings"))
        )
        if max_user_id and max_user_id == settings.admin.max_user_id:
            result.row(("🛠 Админ", "admin:menu"))
        return result.build()

    @classmethod
    def topic_presets(cls) -> dict[str, Any]:
        builder = cls()
        presets = [
            ("Бизнес", "setup:topic:business"),
            ("Технологии", "setup:topic:tech"),
            ("Лайфстайл", "setup:topic:lifestyle"),
            ("Образование", "setup:topic:education"),
            ("Новости", "setup:topic:news"),
            ("Маркетинг", "setup:topic:marketing"),
            ("Здоровье", "setup:topic:health"),
            ("Своя тема", "setup:topic:custom"),
        ]
        for i in range(0, len(presets), 2):
            row_buttons = presets[i:i + 2]
            if row_buttons:
                builder.row(*row_buttons)
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def frequency_presets(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("3 раза в день", "setup:frequency:3x_day"))
            .row(("2 раза в день", "setup:frequency:2x_day"))
            .row(("1 раз в день", "setup:frequency:daily"))
            .row(("2 раза в неделю", "setup:frequency:2x_week"))
            .row(("1 раз в неделю", "setup:frequency:weekly"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def style_review(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("Утвердить", "setup:style:approve"))
            .row(("Перегенерировать", "setup:style:regenerate"))
            .row(("💬 Пояснения", "setup:style:prompt"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def desc_review(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("Утвердить", "setup:desc:approve"))
            .row(("Перегенерировать", "setup:desc:regenerate"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def desc_question(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("Да, нужна SEO-настройка", "setup:desc:yes"))
            .row(("Нет, пропустить", "setup:desc:no"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def logo_review(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("Другой вариант", "setup:logo:regenerate"))
            .row(("Готово", "setup:logo:done"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def logo_question(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("Да, нужен логотип", "setup:logo:yes"))
            .row(("Нет, пропустить", "setup:logo:no"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def plan_prefs_skip(cls, channel_id: int, duration_days: int) -> dict[str, Any]:
        return (
            cls()
            .row(("Пропустить", f"plan:prefs:skip:{channel_id}:{duration_days}"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def plan_settings(cls, prefs: dict) -> dict[str, Any]:
        subscribe_label = "Подписка: ВКЛ" if prefs.get("subscribe_cta") else "Подписка: ВЫКЛ"
        share_label = "Поделиться: ВКЛ" if prefs.get("share_cta") else "Поделиться: ВЫКЛ"
        comments_label = "💬 Комментарии: ВКЛ" if prefs.get("comments_enabled", False) else "💬 Комментарии: ВЫКЛ"
        search_label = "🔍 Поиск в интернете: ВКЛ" if prefs.get("search_enabled") else "🔍 Поиск в интернете: ВЫКЛ"
        sources_label = "📎 Источники: ВКЛ" if prefs.get("show_sources") else "📎 Источники: ВЫКЛ"
        review_label = "👁️ Ревью перед публикацией: ВКЛ" if prefs.get("review_enabled") else "👁️ Ревью перед публикацией: ВЫКЛ"
        return (
            cls()
            .row((subscribe_label, "plan:settings:toggle:subscribe_cta"))
            .row((share_label, "plan:settings:toggle:share_cta"))
            .row((comments_label, "plan:settings:toggle:comments_enabled"))
            .row((search_label, "plan:settings:toggle:search_enabled"))
            .row((sources_label, "plan:settings:toggle:show_sources"))
            .row((review_label, "plan:settings:toggle:review_enabled"))
            .row(("👁️ Визуальный стиль", "settings:visual"))
            .row(("Генерировать план ▶️", "plan:settings:generate"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def plan_time_picker(cls, plan_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(
                ("12:00 МСК", f"plan:time:{plan_id}:12"),
                ("15:00 МСК", f"plan:time:{plan_id}:15"),
            )
            .row(
                ("18:00 МСК", f"plan:time:{plan_id}:18"),
                ("21:00 МСК", f"plan:time:{plan_id}:21"),
            )
            .row(("🕐 Своё время", f"plan:time:custom:{plan_id}"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def plan_actions(cls, plan_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(("⚙️ Настройки плана", f"plan:settings_view:{plan_id}"))
            .row(("🕐 Изменить время", f"plan:edittime:{plan_id}"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def plan_settings_edit(cls, plan_id: int, prefs: dict, freq_name: str = "") -> dict[str, Any]:
        subscribe_label = "Подписка: ВКЛ" if prefs.get("subscribe_cta") else "Подписка: ВЫКЛ"
        share_label = "Поделиться: ВКЛ" if prefs.get("share_cta") else "Поделиться: ВЫКЛ"
        comments_label = "💬 Комментарии: ВКЛ" if prefs.get("comments_enabled", False) else "💬 Комментарии: ВЫКЛ"
        search_label = "🔍 Поиск в интернете: ВКЛ" if prefs.get("search_enabled") else "🔍 Поиск в интернете: ВЫКЛ"
        sources_label = "📎 Источники: ВКЛ" if prefs.get("show_sources") else "📎 Источники: ВЫКЛ"
        review_label = "👁️ Ревью перед публикацией: ВКЛ" if prefs.get("review_enabled") else "👁️ Ревью перед публикацией: ВЫКЛ"
        freq_label = f"⏱ Частота: {freq_name}" if freq_name else "⏱ Частота"
        return (
            cls()
            .row((subscribe_label, f"plan:settings:etoggle:{plan_id}:subscribe_cta"))
            .row((share_label, f"plan:settings:etoggle:{plan_id}:share_cta"))
            .row((comments_label, f"plan:settings:etoggle:{plan_id}:comments_enabled"))
            .row((search_label, f"plan:settings:etoggle:{plan_id}:search_enabled"))
            .row((sources_label, f"plan:settings:etoggle:{plan_id}:show_sources"))
            .row((review_label, f"plan:settings:etoggle:{plan_id}:review_enabled"))
            .row((freq_label, f"plan:freq:{plan_id}"))
            .row(("🕐 Изменить время", f"plan:edittime:{plan_id}"))
            .row(("👁️ Визуальный стиль", f"plan:visual:{plan_id}"))
            .row(("✏️ Ред. контент план", f"plan:edit:{plan_id}"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def plan_edit(cls, plan_id: int, topics: list) -> dict[str, Any]:
        builder = cls()
        for i, t in enumerate(topics):
            builder.row(
                (f"✅ {t.topic[:35]}", f"topic:approve:{t.id}:edit:{plan_id}"),
                (f"❌", f"topic:delete:{t.id}:edit:{plan_id}"),
            )
        builder.row(("+ Добавить тему", f"topic:add:{plan_id}"))
        builder.row(("💬 Уточнить пожелания", f"plan:reprefs:{plan_id}:edit"))
        builder.row(("🔄 Перегенерировать план", f"plan:regenerate:{plan_id}"))
        builder.row(("🚀 Утвердить план", f"plan:approve:{plan_id}"))
        builder.row(("🕐 Изменить время", f"plan:edittime:{plan_id}"))
        builder.row(("👁️ Визуальный стиль", f"plan:visual:{plan_id}"))
        builder.row(("🗑 Удалить план", f"plan:delete:{plan_id}"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def channel_actions(cls, channel_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(("Настроить", f"setup:start:{channel_id}"))
            .row(("👁️ Визуальный стиль", f"setup:visual:analyze:{channel_id}"))
            .row(("Удалить", f"channels:delete:{channel_id}"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def confirm_delete(cls, channel_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(("Да, удалить", f"channels:delete:confirm:{channel_id}"))
            .row(("Отмена", "channels:list"))
            .build()
        )

    @classmethod
    def channel_list(cls, channels: list, include_back: bool = True) -> dict[str, Any]:
        builder = cls()
        for ch in channels:
            label = f"{ch.title}"
            if not getattr(ch, "is_setup_complete", True):
                label += " ⚙️"
            builder.row((label, f"channels:select:{ch.id}"))
        if include_back:
            builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_studio_channel_select(cls, channels: list) -> dict[str, Any]:
        builder = cls()
        for ch in channels:
            builder.row((ch.title, f"ai:channel:{ch.id}"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_studio_blocks(cls, blocks: dict, pipeline_active: bool = False) -> dict[str, Any]:
        builder = cls()

        sched_enabled = blocks.get("schedule", {}).get("enabled", False)
        sched_freq = blocks.get("schedule", {}).get("frequency", "daily")
        sched_times = blocks.get("schedule", {}).get("times", [])
        sched_label = "⏱ Расписание публикаций"
        freq_names = {"daily": "1 раз в день", "2x_day": "2 раза в день", "3x_day": "3 раза в день",
                       "2x_week": "2 раза в неделю", "weekly": "1 раз в неделю"}
        if sched_enabled:
            sched_label += f" — {freq_names.get(sched_freq, sched_freq)}"
            if sched_times:
                msk_times = []
                for t in sched_times:
                    parts = t.split(":")
                    h = (int(parts[0]) + 3) % 24
                    m = parts[1] if len(parts) > 1 else "00"
                    msk_times.append(f"{h:02d}:{m}")
                sched_label += f" ({', '.join(msk_times)} МСК)"
        else:
            sched_label += " (выкл)"
        builder.row((sched_label, "ai:edit:schedule"))

        image_enabled = blocks.get("image_gen", {}).get("enabled", False)
        image_model = blocks.get("image_gen", {}).get("model", "")
        image_label = "🖼 Генерация изображений"
        if image_enabled:
            image_label += f" — {image_model}"
        else:
            image_label += " (выкл)"
        builder.row((image_label, "ai:edit:image_gen"))

        prompt_enabled = blocks.get("image_prompt", {}).get("enabled", False)
        prompt_preview = blocks.get("image_prompt", {}).get("generated_prompt", "")
        prompt_mode = blocks.get("image_prompt", {}).get("mode", "ai")
        prompt_label = "📝 Промпт для изображений"
        if prompt_enabled:
            if prompt_preview:
                preview = prompt_preview[:40] + "…" if len(prompt_preview) > 40 else prompt_preview
                prompt_label += f" — {preview}"
            else:
                prompt_label += " — " + ("AI" if prompt_mode == "ai" else "Готовый")
        else:
            prompt_label += " (выкл)"
        builder.row((prompt_label, "ai:edit:image_prompt"))

        video_enabled = blocks.get("video_gen", {}).get("enabled", False)
        video_model = blocks.get("video_gen", {}).get("model", "")
        video_label = "🎬 Генерация видео"
        if video_enabled:
            video_label += f" — {video_model}"
        else:
            video_label += " (выкл)"
        builder.row((video_label, "ai:edit:video_gen"))

        post_enabled = blocks.get("post_gen", {}).get("enabled", False)
        post_mode = blocks.get("post_gen", {}).get("mode", "")
        post_label = "📋 Генерация поста"
        if post_enabled:
            mode_display = "AI" if post_mode == "ai" else "Фикс. текст"
            post_label += f" — {mode_display}"
        else:
            post_label += " (выкл)"
        builder.row((post_label, "ai:edit:post_gen"))

        if pipeline_active:
            builder.row(("🟢 Пайплайн запущен", "ai:pipeline:info"))
            builder.row(("⏹ Остановить", "ai:pipeline:stop"))
        elif sched_enabled:
            builder.row(("▶ Запустить пайплайн", "ai:pipeline:start"))

        builder.row(("ℹ️ Информация", "ai:blocks:info"))
        builder.row(("🧪 Тест", "ai:blocks:test"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_image_model_select(cls, current_model: str) -> dict[str, Any]:
        builder = cls()
        for model_id, model_name in [("gpt-image-2", "GPT Images 2")]:
            prefix = "✅ " if model_id == current_model else ""
            builder.row((f"{prefix}{model_name}", f"ai:block:image_gen:model:{model_id}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_image_prompt_review(cls, mode: str = "ai") -> dict[str, Any]:
        builder = cls()
        if mode == "ai":
            builder.row(("✅ Утвердить", "ai:image_prompt:approve"))
            builder.row(("🔄 Переделать", "ai:image_prompt:regenerate"))
            builder.row(("✏️ Другое описание", "ai:image_prompt:edit_desc"))
        else:
            builder.row(("✅ Сохранить", "ai:image_prompt:approve"))
            builder.row(("✏️ Изменить текст", "ai:image_prompt:edit_desc"))
        builder.row(("↩ Назад к блокам", "ai:image_prompt:cancel"))
        return builder.build()

    @classmethod
    def ai_video_model_select(cls, current_model: str) -> dict[str, Any]:
        from app.bot.states.ai_studio import VIDEO_MODELS
        builder = cls()
        for model_id, model_name in VIDEO_MODELS:
            prefix = "✅ " if model_id == current_model else ""
            builder.row((f"{prefix}{model_name}", f"ai:block:video_gen:model:{model_id}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_video_prompt_review(cls, mode: str = "ai") -> dict[str, Any]:
        builder = cls()
        if mode == "ai":
            builder.row(("✅ Утвердить", "ai:video_prompt:approve"))
            builder.row(("🔄 Переделать", "ai:video_prompt:regenerate"))
            builder.row(("✏️ Другое описание", "ai:video_prompt:edit_desc"))
        else:
            builder.row(("✅ Сохранить", "ai:video_prompt:approve"))
            builder.row(("✏️ Изменить текст", "ai:video_prompt:edit_desc"))
        builder.row(("↩ Назад к блокам", "ai:video_prompt:cancel"))
        return builder.build()

    @classmethod
    def ai_prompt_mode_select(cls, block_id: str) -> dict[str, Any]:
        return (
            cls()
            .row(("🤖 AI сгенерирует", f"ai:block:{block_id}:mode:ai"))
            .row(("📄 Готовый промпт", f"ai:block:{block_id}:mode:fixed"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_post_gen_mode_select(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("🤖 AI сгенерирует", "ai:block:post_gen:mode:ai"))
            .row(("📄 Готовый текст", "ai:block:post_gen:mode:fixed"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_post_gen_link_toggle(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("✅ Да", "ai:block:post_gen:link:yes"))
            .row(("❌ Нет", "ai:block:post_gen:link:no"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_post_gen_review(cls, mode: str) -> dict[str, Any]:
        builder = cls()
        if mode == "ai":
            builder.row(("✅ Утвердить", "ai:post_gen:approve"))
            builder.row(("🔄 Переделать", "ai:post_gen:regenerate"))
            builder.row(("✏️ Другое описание", "ai:post_gen:edit_input"))
        else:
            builder.row(("✅ Сохранить", "ai:post_gen:approve"))
            builder.row(("✏️ Изменить текст", "ai:post_gen:edit_input"))
        builder.row(("↩ Назад к блокам", "ai:post_gen:cancel"))
        return builder.build()

    @classmethod
    def post_review(cls, post_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(("Сделать короче", f"edit:shorter:{post_id}"))
            .row(("Сделать длиннее", f"edit:longer:{post_id}"))
            .row(("Сделать экспертнее", f"edit:expert:{post_id}"))
            .row(("Дружелюбнее", f"edit:friendly:{post_id}"))
            .row(("Добавить фактов", f"edit:facts:{post_id}"))
            .row(("Переделать полностью", f"edit:rewrite:{post_id}"))
            .row(("📅 В расписание", f"schedule:show:{post_id}"))
            .row(("🖼 Картинка", f"post:image:{post_id}"))
            .row(("✅ Опубликовать", f"post:publish:{post_id}"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def schedule_review(cls, schedule_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(("✏️ Редактировать", f"schedule:edit:{schedule_id}"))
            .row(("🖼 Картинка", f"schedule:image:{schedule_id}"))
            .row(("✅ Опубликовать", f"schedule:confirm:{schedule_id}"))
            .row(("❌ Пропустить", f"schedule:skip:{schedule_id}"))
            .build()
        )

    @classmethod
    def schedule_edit_options(cls, schedule_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(("Сделать короче", f"schedule:edit:{schedule_id}:shorter"))
            .row(("Сделать длиннее", f"schedule:edit:{schedule_id}:longer"))
            .row(("Сделать экспертнее", f"schedule:edit:{schedule_id}:expert"))
            .row(("Дружелюбнее", f"schedule:edit:{schedule_id}:friendly"))
            .row(("Добавить фактов", f"schedule:edit:{schedule_id}:facts"))
            .row(("Переделать полностью", f"schedule:edit:{schedule_id}:rewrite"))
            .row(("💬 Своё описание", f"schedule:edit:{schedule_id}:custom"))
            .row(("Назад", f"schedule:review:{schedule_id}"))
            .build()
        )

    @classmethod
    def ai_schedule_freq_select(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("3 раза в день", "ai:block:schedule:freq:3x_day"))
            .row(("2 раза в день", "ai:block:schedule:freq:2x_day"))
            .row(("1 раз в день", "ai:block:schedule:freq:daily"))
            .row(("2 раза в неделю", "ai:block:schedule:freq:2x_week"))
            .row(("1 раз в неделю", "ai:block:schedule:freq:weekly"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_schedule_time_picker(cls, slot_info: str = "") -> dict[str, Any]:
        header = f"{slot_info}\n" if slot_info else ""
        return (
            cls()
            .row(
                ("12:00 МСК", "ai:block:schedule:time:12"),
                ("15:00 МСК", "ai:block:schedule:time:15"),
            )
            .row(
                ("18:00 МСК", "ai:block:schedule:time:18"),
                ("21:00 МСК", "ai:block:schedule:time:21"),
            )
            .row(("🕐 Своё время", "ai:block:schedule:time:custom"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def plan_creation_prompt(cls, channel_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(("Да, создать план", f"newplan:start:{channel_id}"))
            .row(("Позже", "main_menu"))
            .build()
        )

    @classmethod
    def plan_creation_method(cls, channel_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(("🧠 AI сгенерирует", f"newplan:ai:{channel_id}"))
            .row(("📋 Загрузить свой", f"newplan:custom:{channel_id}"))
            .row(("🔍 AI с поиском в интернете", f"newplan:search:{channel_id}"))
            .row(("Назад", "main_menu"))
            .build()
        )
