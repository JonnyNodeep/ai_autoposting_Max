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
            if prompt_mode == "from_post":
                prompt_label += " — по тексту поста"
            elif prompt_preview:
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
            mode_display = "AI каждый запуск" if post_mode == "ai" else "Фикс. текст"
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
        builder = (
            cls()
            .row(("🤖 AI сгенерирует", f"ai:block:{block_id}:mode:ai"))
            .row(("📄 Готовый промпт", f"ai:block:{block_id}:mode:fixed"))
        )
        if block_id == "image_prompt":
            builder.row(
                ("🖼 Картинка по тексту поста", f"ai:block:{block_id}:mode:from_post")
            )
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

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
    def ai_image_prompt_visual_style_toggle(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("✅ Да", "ai:block:image_prompt:visual:yes"))
            .row(("❌ Нет", "ai:block:image_prompt:visual:no"))
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
    def ai_post_gen_bold_toggle(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("✅ Да", "ai:block:post_gen:bold:yes"))
            .row(("❌ Нет", "ai:block:post_gen:bold:no"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_post_gen_emoji_toggle(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("✅ Да", "ai:block:post_gen:emoji:yes"))
            .row(("❌ Нет", "ai:block:post_gen:emoji:no"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_post_gen_comments_toggle(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("✅ Да, подключены", "ai:block:post_gen:comments:yes"))
            .row(("❌ Нет", "ai:block:post_gen:comments:no"))
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
