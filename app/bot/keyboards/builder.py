from typing import Any

from app.config import settings
from app.application.auth.feature_access import (
    audio_allowed,
    drive_allowed,
    high_freq_allowed,
    rss_allowed,
    video_allowed,
)
from app.bot.schedule_frequency import FREQ_LABELS


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
    def main_menu(
        cls,
        max_user_id: int | None = None,
        channels_used: int = 0,
        channels_limit: int | None = 0,
    ) -> dict[str, Any]:
        if channels_limit is None:
            channels_label = f"📡 Каналы ({channels_used}/∞)"
        elif channels_limit > 0:
            channels_label = f"📡 Каналы ({channels_used}/{channels_limit})"
        else:
            channels_label = "📡 Каналы"
        result = (
            cls()
            .row((channels_label, "channels:list"), ("💳 Подписка", "subscription:status"))
            .row(("➕ Добавить канал", "channels:add"))
            .row(("🤖 AI Content Studio", "ai_studio"))
            .row(("❓ Помощь", "help"))
        )
        if max_user_id and max_user_id == settings.admin.max_user_id:
            result.row(("🛠 Админ", "admin:menu"))
        return result.build()

    @classmethod
    def help_menu(cls, max_user_id: int | None = None) -> dict[str, Any]:
        builder = cls()
        builder.row(("🚀 Как начать", "help:faq:start"))
        builder.row(("📋 Посты и подписка", "help:faq:subscribe_cta"))
        builder.row(("⏱ Расписание", "help:faq:schedule"))
        builder.row(("🧪 Тест и запуск", "help:faq:test"))
        builder.row(("🖼 Картинки", "help:faq:images"))
        if rss_allowed(max_user_id):
            builder.row(("📰 RSS и новости", "help:faq:rss"))
        if video_allowed(max_user_id) or audio_allowed(max_user_id):
            builder.row(("🎬 Видео и аудио", "help:faq:video_audio"))
        builder.row(("📎 Telegram", "help:faq:telegram"))
        builder.row(("💳 Подписка", "help:faq:subscription"))
        builder.row(("⏳ Сессия истекла", "help:faq:session"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

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
    def frequency_presets(cls, max_user_id: int | None = None) -> dict[str, Any]:
        builder = cls()
        if high_freq_allowed(max_user_id):
            builder.row(("8 раз в день", "setup:frequency:8x_day"))
            builder.row(("7 раз в день", "setup:frequency:7x_day"))
            builder.row(("6 раз в день", "setup:frequency:6x_day"))
        builder.row(("5 раз в день", "setup:frequency:5x_day"))
        builder.row(("4 раза в день", "setup:frequency:4x_day"))
        builder.row(("3 раза в день", "setup:frequency:3x_day"))
        builder.row(("2 раза в день", "setup:frequency:2x_day"))
        builder.row(("1 раз в день", "setup:frequency:daily"))
        builder.row(("2 раза в неделю", "setup:frequency:2x_week"))
        builder.row(("1 раз в неделю", "setup:frequency:weekly"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

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
            .row(("🖼 Логотип watermark", f"channels:wm_logo:{channel_id}"))
            .row(("Удалить", f"channels:delete:{channel_id}"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def watermark_logo_menu(cls, channel_id: int) -> dict[str, Any]:
        return (
            cls()
            .row(("Взять из иконки канала", f"channels:wm_logo:sync:{channel_id}"))
            .row(("Загрузить свой файл", f"channels:wm_logo:upload:{channel_id}"))
            .row(("К каналу", f"channels:select:{channel_id}"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def channel_card(cls, channel_id: int, *, has_telegram: bool = False) -> dict[str, Any]:
        builder = cls()
        if has_telegram:
            builder.row(("🔓 Отвязать Telegram", f"channels:tg:unbind:{channel_id}"))
        else:
            builder.row(("📎 Привязать Telegram", f"channels:tg:bind:{channel_id}"))
        return (
            builder
            .row(("Настроить", f"setup:start:{channel_id}"))
            .row(("👁️ Визуальный стиль", f"setup:visual:analyze:{channel_id}"))
            .row(("🖼 Логотип watermark", f"channels:wm_logo:{channel_id}"))
            .row(("Удалить", f"channels:delete:{channel_id}"))
            .row(("К списку каналов", "channels:list"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def telegram_mirror_offer(cls, channel_id: int, *, source: str = "setup") -> dict[str, Any]:
        return (
            cls()
            .row(("📎 Привязать Telegram", f"{source}:tg:bind:{channel_id}"))
            .row(("Пропустить", f"{source}:tg:skip:{channel_id}"))
            .build()
        )

    @classmethod
    def telegram_bind_retry(cls, channel_id: int, *, source: str = "setup") -> dict[str, Any]:
        return (
            cls()
            .row(("Пропустить", f"{source}:tg:skip:{channel_id}"))
            .build()
        )

    @classmethod
    def telegram_link_fallback(cls, channel_id: int, *, source: str = "setup") -> dict[str, Any]:
        return (
            cls()
            .row(("Пропустить ссылку", f"{source}:tg:skip_link:{channel_id}"))
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
    def ai_studio_blocks(
        cls,
        blocks: dict,
        pipeline_active: bool = False,
        max_user_id: int | None = None,
    ) -> dict[str, Any]:
        builder = cls()

        sched_enabled = blocks.get("schedule", {}).get("enabled", False)
        sched_freq = blocks.get("schedule", {}).get("frequency", "daily")
        sched_times = blocks.get("schedule", {}).get("times", [])
        sched_label = "⏱ Когда публиковать"
        freq_names = FREQ_LABELS
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
            if blocks.get("schedule", {}).get("per_slot_prompts"):
                sched_label += " + промпты по слотам"
        else:
            sched_label += " (выкл)"

        rss = blocks.get("news_rss") or {}
        rss_enabled = bool(rss.get("enabled", False))
        rss_feeds = list(rss.get("feeds") or [])
        rss_sites = list(rss.get("sites") or [])
        if rss_allowed(max_user_id):
            rss_label = "📰 RSS / сайты"
            if rss_enabled:
                interval = rss.get("poll_interval_minutes", 5)
                niche = rss.get("niche") or ""
                from app.application.pipeline.rss_monitor import NICHE_LABELS
                niche_part = f" / {NICHE_LABELS.get(niche, niche)}" if niche else ""
                parts = [f"{len(rss_feeds)} лент"]
                if rss_sites:
                    parts.append(f"{len(rss_sites)} сайт.")
                rss_label += f" — {', '.join(parts)} / {interval} мин{niche_part}"
            else:
                rss_label += " (выкл)"
            builder.row((rss_label, "ai:edit:news_rss"))

        drive = blocks.get("drive_video") or {}
        if drive_allowed(max_user_id):
            drive_enabled = bool(drive.get("enabled", False))
            drive_label = "📁 Google Drive"
            if drive_enabled:
                folder = str(drive.get("folder_id") or "").strip()
                short = folder[:12] + "…" if len(folder) > 12 else (folder or "без папки")
                drive_label += f" — {short}"
            else:
                drive_label += " (выкл)"
            builder.row((drive_label, "ai:edit:drive_video"))

        image_enabled = blocks.get("image_gen", {}).get("enabled", False)
        image_label = "🖼 Картинки к посту"
        if image_enabled:
            wm = "логотип" if blocks.get("image_gen", {}).get("add_watermark", False) else "без wm"
            txt = "текст" if blocks.get("image_gen", {}).get("allow_text", True) else "без текста"
            image_label += f" — вкл · {wm} · {txt}"
        else:
            image_label += " (выкл)"
        builder.row((image_label, "ai:edit:image_gen"))

        prompt_enabled = blocks.get("image_prompt", {}).get("enabled", False)
        prompt_preview = blocks.get("image_prompt", {}).get("generated_prompt", "")
        prompt_mode = blocks.get("image_prompt", {}).get("mode", "ai")
        prompt_label = "📝 Промпт для картинки"
        if prompt_enabled:
            if prompt_mode == "from_topic":
                prompt_label += " — по теме поста"
            elif prompt_mode == "from_post":
                prompt_label += " — по тексту поста"
            elif prompt_mode == "from_news":
                prompt_label += " — из новости → AI"
            elif prompt_preview:
                preview = prompt_preview[:40] + "…" if len(prompt_preview) > 40 else prompt_preview
                prompt_label += f" — {preview}"
            else:
                prompt_label += " — " + ("AI" if prompt_mode == "ai" else "Готовый")
        else:
            prompt_label += " (выкл)"
        builder.row((prompt_label, "ai:edit:image_prompt"))

        if video_allowed(max_user_id):
            video_enabled = blocks.get("video_gen", {}).get("enabled", False)
            video_label = "🎬 Видео"
            if video_enabled:
                video_label += " — вкл"
            else:
                video_label += " (выкл)"
            builder.row((video_label, "ai:edit:video_gen"))

        story = blocks.get("story_gen") or {}
        tts = blocks.get("tts_gen") or {}
        audio_on = bool(story.get("enabled") and tts.get("enabled"))
        sunor_on = bool((blocks.get("sunor_gen") or {}).get("enabled"))
        if audio_allowed(max_user_id):
            audio_label = "🎙 Аудио"
            if audio_on:
                from app.application.pipeline.tts_voices import TTS_PROVIDER_SUNOR

                mins = story.get("target_minutes", 5)
                provider = str(tts.get("provider") or TTS_PROVIDER_SUNOR)
                fmt = story.get("format") or "fairy_tale"
                kind = "сказка" if fmt in ("fairy_tale", "bedtime") else str(fmt)
                if provider == TTS_PROVIDER_SUNOR:
                    audio_label += (
                        f" — {kind} · видео · Sunor V5.5 · 3–6 лет · ~{mins} мин"
                    )
                else:
                    from app.application.pipeline.tts_instructions import (
                        TTS_INSTRUCTION_PRESET_LABELS,
                    )
                    from app.application.pipeline.tts_voices import (
                        TTS_PROVIDER_SPEECHKIT,
                        role_label,
                        voice_label,
                    )

                    voice = tts.get("voice") or (
                        "dasha" if provider == TTS_PROVIDER_SPEECHKIT else "shimmer"
                    )
                    speed = tts.get(
                        "speed", 0.9 if provider == TTS_PROVIDER_SPEECHKIT else 0.85
                    )
                    prov_label = (
                        "SpeechKit" if provider == TTS_PROVIDER_SPEECHKIT else "OpenAI"
                    )
                    v_label = voice_label(provider, str(voice))
                    if provider == TTS_PROVIDER_SPEECHKIT:
                        pitch = tts.get("pitchShift", 0)
                        extra = (
                            f"pitch {pitch} · "
                            f"{role_label(str(tts.get('role') or 'neutral'))}"
                        )
                    else:
                        style_key = str(tts.get("instructions_preset") or "bedtime")
                        extra = TTS_INSTRUCTION_PRESET_LABELS.get(style_key, style_key)
                    audio_label += (
                        f" — {kind} · {mins} мин · {prov_label} · "
                        f"{v_label} · {speed} · {extra}"
                    )
            else:
                audio_label += " (выкл)"
            builder.row((audio_label, "ai:edit:tts_gen"))

            sunor = blocks.get("sunor_gen") or {}
            sunor_label = "🎵 Sunor API"
            if sunor.get("enabled"):
                mode = str(sunor.get("music_mode") or "inspiration")
                tags = (sunor.get("tags") or sunor.get("gpt_description_prompt") or "")[:30]
                dur = int(sunor.get("target_duration_sec") or 0)
                dur_s = f"{dur // 60}м" if dur else "авто"
                sunor_label += f" — {mode} · {tags or '…'} · {dur_s}"
            else:
                sunor_label += " (выкл)"
            builder.row((sunor_label, "ai:edit:sunor_gen"))

        post_enabled = blocks.get("post_gen", {}).get("enabled", False)
        post_mode = blocks.get("post_gen", {}).get("mode", "")
        if (audio_on or sunor_on) and audio_allowed(max_user_id):
            post_label = "📋 Текст под аудио"
            if post_enabled:
                post_label += " — призыв подписаться"
            else:
                post_label += " (выкл)"
        else:
            post_label = "📋 Текст поста"
            if post_enabled:
                mode_display = "AI каждый запуск" if post_mode == "ai" else "Фикс. текст"
                post_label += f" — {mode_display}"
            else:
                post_label += " (выкл)"
        builder.row((post_label, "ai:edit:post_gen"))

        queue = list((blocks.get("post_gen") or {}).get("topic_queue") or [])
        queue_label = f"📚 Темы для постов ({len(queue)})"
        builder.row((queue_label, "ai:edit:topic_queue"))

        # Schedule after topics — time setup is secondary to content themes.
        builder.row((sched_label, "ai:edit:schedule"))

        can_start = False
        if sched_enabled and blocks.get("schedule", {}).get("times"):
            can_start = True
        if rss_allowed(max_user_id) and rss_enabled and (rss_feeds or rss_sites):
            can_start = True

        if pipeline_active:
            builder.row(("🟢 Автопостинг запущен", "ai:pipeline:info"))
            builder.row(("⏹ Остановить", "ai:pipeline:stop"))
        elif can_start:
            builder.row(("▶ Запустить автопостинг", "ai:pipeline:start"))

        builder.row(("ℹ️ Информация", "ai:blocks:info"))
        builder.row(("🧪 Тест (пример вам)", "ai:blocks:test"))
        builder.row(("← К выбору канала", "ai_studio"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_news_rss_menu(cls, block: dict) -> dict[str, Any]:
        enabled = bool(block.get("enabled", False))
        feeds = list(block.get("feeds") or [])
        sites = list(block.get("sites") or [])
        interval = int(block.get("poll_interval_minutes") or 5)
        try:
            spacing = int(block.get("publish_interval_minutes", 15))
        except (TypeError, ValueError):
            spacing = 15
        include_n = len(block.get("include_keywords") or [])
        exclude_n = len(block.get("exclude_keywords") or [])
        niche = block.get("niche") or ""
        from app.application.pipeline.rss_monitor import (
            NICHE_LABELS,
            format_publish_spacing_label,
            format_publish_window_label,
        )
        niche_label = NICHE_LABELS.get(niche, "не выбрана") if niche else "не выбрана"
        window_label = format_publish_window_label(
            str(block.get("publish_from_msk") or "09:00"),
            str(block.get("publish_until_msk") or "22:00"),
        )
        spacing_label = format_publish_spacing_label(spacing)
        builder = cls()
        builder.row(
            ("🟢 Включено" if enabled else "⚪ Выключено", "ai:block:news_rss:toggle")
        )
        builder.row(("➕ Добавить RSS", "ai:block:news_rss:add"))
        for i, url in enumerate(feeds[:8]):
            short = url if len(url) <= 48 else url[:45] + "…"
            builder.row((f"🗑 RSS {short}", f"ai:block:news_rss:del:{i}"))
        builder.row(("➕ Добавить сайт", "ai:block:news_rss:add_site"))
        for i, url in enumerate(sites[:8]):
            short = url if len(url) <= 48 else url[:45] + "…"
            builder.row((f"🗑 Сайт {short}", f"ai:block:news_rss:del_site:{i}"))
        for mins, label in ((2, "2 мин"), (5, "5 мин"), (10, "10 мин")):
            prefix = "✅ " if mins == interval else ""
            builder.row((f"{prefix}Опрос: {label}", f"ai:block:news_rss:interval:{mins}"))
        builder.row((f"🕐 Окно: {window_label}", "ai:block:news_rss:window"))
        builder.row((f"⏳ Между постами: {spacing_label}", "ai:block:news_rss:spacing"))
        builder.row((f"🎯 Тема / фильтр — {niche_label}", "ai:block:news_rss:filters"))
        builder.row((f"Слова: +{include_n} / −{exclude_n}", "ai:block:news_rss:filters"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_news_rss_spacing_select(cls, block: dict) -> dict[str, Any]:
        try:
            current = int(block.get("publish_interval_minutes", 15))
        except (TypeError, ValueError):
            current = 15
        from app.application.pipeline.rss_monitor import (
            RSS_PUBLISH_INTERVAL_PRESETS,
            format_publish_spacing_label,
        )

        presets = (
            (0, "Сразу (пачкой)"),
            *((m, format_publish_spacing_label(m)) for m in RSS_PUBLISH_INTERVAL_PRESETS if m > 0),
        )
        builder = cls()
        for value, label in presets:
            prefix = "✅ " if value == current else ""
            builder.row(
                (f"{prefix}{label}", f"ai:block:news_rss:spacing_set:{value}")
            )
        builder.row(("Назад", "ai:edit:news_rss"))
        return builder.build()

    @classmethod
    def ai_news_rss_rate_select(cls, block: dict) -> dict[str, Any]:
        return cls.ai_news_rss_spacing_select(block)

    @classmethod
    def ai_news_rss_window_select(cls, block: dict) -> dict[str, Any]:
        from app.application.pipeline.rss_monitor import format_publish_window_label

        cur_from = str(block.get("publish_from_msk") or "09:00")
        cur_until = str(block.get("publish_until_msk") or "22:00")
        presets = (
            ("00:00", "00:00"),
            ("09:00", "22:00"),
            ("08:00", "23:00"),
            ("10:00", "20:00"),
        )
        builder = cls()
        for fr, until in presets:
            if fr == "00:00" and until == "00:00":
                text = "Без окна (круглосуточно)"
            else:
                text = format_publish_window_label(fr, until)
            prefix = "✅ " if fr == cur_from and until == cur_until else ""
            # payload uses dashes so split stays stable: window:09-00:22-00
            builder.row(
                (
                    f"{prefix}{text}",
                    f"ai:block:news_rss:window_set:{fr.replace(':', '-')}:{until.replace(':', '-')}",
                )
            )
        builder.row(("✏️ Своё окно", "ai:block:news_rss:window_custom"))
        builder.row(("Назад", "ai:edit:news_rss"))
        return builder.build()

    @classmethod
    def ai_news_rss_niche_select(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("Крипта", "ai:block:news_rss:niche:crypto"))
            .row(("IT / технологии", "ai:block:news_rss:niche:it"))
            .row(("Политика", "ai:block:news_rss:niche:politics"))
            .row(("Общее", "ai:block:news_rss:niche:general"))
            .row(("Своя тема", "ai:block:news_rss:niche:custom"))
            .row(("Назад", "ai:edit:news_rss"))
            .build()
        )

    @classmethod
    def ai_news_rss_filters_menu(cls, *, has_keywords: bool) -> dict[str, Any]:
        builder = cls()
        if has_keywords:
            builder.row(("👁 Показать текущие", "ai:block:news_rss:kw:show"))
        builder.row(("✏️ Править вручную", "ai:block:news_rss:kw:edit_manual"))
        builder.row(("🤖 Подобрать заново (ИИ)", "ai:block:news_rss:kw:pick_ai"))
        builder.row(("Назад", "ai:edit:news_rss"))
        return builder.build()

    @classmethod
    def ai_news_rss_keywords_review(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("✅ Применить", "ai:block:news_rss:kw:approve"))
            .row(("✏️ Править вручную", "ai:block:news_rss:kw:edit_manual"))
            .row(("🔄 Переделать", "ai:block:news_rss:kw:regen"))
            .row(("✏️ Другое описание темы", "ai:block:news_rss:kw:edit_brief"))
            .row(("Назад", "ai:edit:news_rss"))
            .build()
        )

    @classmethod
    def ai_drive_video_menu(cls, block: dict) -> dict[str, Any]:
        enabled = bool(block.get("enabled", False))
        delete_on = bool(block.get("delete_after_publish", True))
        builder = cls()
        builder.row(
            ("🟢 Включено" if enabled else "⚪ Выключено", "ai:block:drive_video:toggle")
        )
        builder.row(("📂 Указать папку", "ai:block:drive_video:set_folder"))
        builder.row(("✏️ Подпись к видео", "ai:block:drive_video:set_caption"))
        builder.row(
            (
                "🗑 Удалять после поста: да" if delete_on else "🗑 Удалять после поста: нет",
                "ai:block:drive_video:delete_toggle",
            )
        )
        builder.row(("📊 Сколько видео осталось", "ai:block:drive_video:status"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
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
    def ai_image_watermark_toggle(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("✅ Да", "ai:block:image_gen:watermark:yes"))
            .row(("❌ Нет", "ai:block:image_gen:watermark:no"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_image_text_toggle(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("✅ Да", "ai:block:image_gen:text:yes"))
            .row(("❌ Нет", "ai:block:image_gen:text:no"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

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
                ("🖼 Картинка по теме поста", f"ai:block:{block_id}:mode:from_topic")
            )
            builder.row(
                ("📝 Картинка по тексту поста", f"ai:block:{block_id}:mode:from_post")
            )
            builder.row(
                ("📰 Фото из новости → AI", f"ai:block:{block_id}:mode:from_news")
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
            .row(("✅ Да, добавить «Подпишись»", "ai:block:post_gen:link:yes"))
            .row(("❌ Нет, без ссылки", "ai:block:post_gen:link:no"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_post_gen_related_toggle(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("✅ Да, добавить другие каналы", "ai:block:post_gen:related:yes"))
            .row(("❌ Нет", "ai:block:post_gen:related:no"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_post_gen_related_picker(
        cls,
        owner_channels: list[Any],
        *,
        current_channel_id: int | None,
        selected_channel_ids: set[int],
        has_entries: bool,
    ) -> dict[str, Any]:
        builder = cls()
        for ch in owner_channels:
            ch_id = getattr(ch, "id", None)
            if ch_id is None or ch_id == current_channel_id:
                continue
            title = (getattr(ch, "title", None) or "Канал")[:35]
            prefix = "☑" if ch_id in selected_channel_ids else "☐"
            builder.row((f"{prefix} {title}", f"ai:block:post_gen:related:pick:{ch_id}"))
        builder.row(("➕ Добавить вручную", "ai:block:post_gen:related:manual"))
        if has_entries:
            builder.row(("✅ Готово", "ai:block:post_gen:related:done"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

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
    def ai_topic_queue_menu(
        cls,
        queue: list[str] | None = None,
        *,
        block: str = "post_gen",
        topic_gen_extra: str = "",
    ) -> dict[str, Any]:
        builder = cls()
        items = list(queue or [])
        prefix = f"ai:block:{block}:topics"
        builder.row(("➕ Добавить вручную", f"{prefix}:add"))
        builder.row(("🤖 Сгенерировать темы", f"{prefix}:generate"))
        extra = (topic_gen_extra or "").strip()
        if extra:
            preview = extra if len(extra) <= 28 else extra[:27] + "…"
            builder.row((f"✏️ Пожелания — {preview}", f"{prefix}:extra"))
        else:
            builder.row(("✏️ Пожелания к генерации", f"{prefix}:extra"))
        # Show delete buttons for first items only (callback payload limits).
        for i, topic in enumerate(items[:8]):
            short = topic if len(topic) <= 36 else topic[:35] + "…"
            builder.row((f"🗑 {i + 1}. {short}", f"{prefix}:del:{i}"))
        if items:
            builder.row(("🧹 Очистить очередь", f"{prefix}:clear"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_topic_count_menu(cls, *, block: str = "post_gen") -> dict[str, Any]:
        prefix = f"ai:block:{block}:topics"
        back = "ai:edit:topic_queue" if block == "post_gen" else "ai:edit:story_topics"
        return (
            cls()
            .row(("7", f"{prefix}:gen:7"), ("14", f"{prefix}:gen:14"))
            .row(("21", f"{prefix}:gen:21"), ("30", f"{prefix}:gen:30"))
            .row(("✏️ Своё число", f"{prefix}:gen:custom"))
            .row(("Назад", back))
            .build()
        )

    @classmethod
    def ai_topic_queue_review(cls, *, block: str = "post_gen") -> dict[str, Any]:
        prefix = f"ai:block:{block}:topics"
        return (
            cls()
            .row(("✅ Утвердить и добавить", f"{prefix}:approve"))
            .row(("🔄 Перегенерировать", f"{prefix}:regen"))
            .row(("↩ Отмена", f"{prefix}:cancel_review"))
            .build()
        )

    @classmethod
    def ai_sunor_gen_menu(cls, block: dict) -> dict[str, Any]:
        enabled = bool(block.get("enabled"))
        mode = str(block.get("music_mode") or "inspiration")
        builder = cls()
        builder.row(
            (
                f"{'✅' if enabled else '⬜'} Включить блок",
                "ai:block:sunor_gen:toggle",
            )
        )
        builder.row(("🌙 Пресет «Колыбельная»", "ai:block:sunor_gen:preset:lullaby"))
        for mid, label in (
            ("inspiration", "Inspiration"),
            ("custom", "Custom (со словами)"),
            ("instrumental", "Instrumental"),
        ):
            prefix = "✓ " if mode == mid else ""
            builder.row((f"{prefix}{label}", f"ai:block:sunor_gen:mode:{mid}"))
        inst = bool(block.get("make_instrumental"))
        builder.row(
            (
                f"{'✅' if inst else '⬜'} Instrumental",
                "ai:block:sunor_gen:toggle:instrumental",
            )
        )
        builder.row(("✏️ Tags (стиль)", "ai:block:sunor_gen:input:tags"))
        builder.row(("✏️ Prompt / lyrics", "ai:block:sunor_gen:input:prompt"))
        builder.row(("✏️ Inspiration описание", "ai:block:sunor_gen:input:gpt"))
        builder.row(("✏️ Negative tags", "ai:block:sunor_gen:input:negative"))
        builder.row(("✏️ Название", "ai:block:sunor_gen:input:title"))
        lyrics = bool(block.get("lyrics_enabled"))
        builder.row(
            (
                f"{'✅' if lyrics else '⬜'} Sunor Lyrics API",
                "ai:block:sunor_gen:toggle:lyrics",
            )
        )
        builder.row(("✏️ Lyrics prompt", "ai:block:sunor_gen:input:lyrics"))
        src = str(block.get("prompt_source") or "config")
        builder.row(
            (
                f"{'✓ ' if src == 'config' else ''}Текст из config",
                "ai:block:sunor_gen:source:config",
            )
        )
        builder.row(
            (
                f"{'✓ ' if src == 'story_gen' else ''}Текст из story_gen",
                "ai:block:sunor_gen:source:story",
            )
        )
        for m in (0, 3, 5, 7, 10):
            if m == 0:
                builder.row(("Длина: авто", "ai:block:sunor_gen:duration:off"))
            else:
                cur = int(block.get("target_duration_sec") or 0) // 60
                prefix = "✓ " if cur == m else ""
                builder.row((f"{prefix}{m} мин", f"ai:block:sunor_gen:duration:{m}"))
        ext = bool(block.get("extend_enabled"))
        builder.row(
            (
                f"{'✅' if ext else '⬜'} Extend (удлинять)",
                "ai:block:sunor_gen:toggle:extend",
            )
        )
        builder.row(("✏️ Continue prompt", "ai:block:sunor_gen:input:continue"))
        cover = bool(block.get("attach_cover_image", True))
        builder.row(
            (
                f"{'✅' if cover else '⬜'} Обложка Suno",
                "ai:block:sunor_gen:toggle:cover",
            )
        )
        builder.row(("← К блокам", "ai:sunor_gen:back"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_story_gen_mode_select(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("🤖 AI каждый запуск", "ai:block:story_gen:mode:ai"))
            .row(("📄 Готовая сказка", "ai:block:story_gen:mode:fixed"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_audio_type_select(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("📖 Сказка", "ai:block:tts_gen:type:fairy_tale"))
            .row(("🎙 Подкаст (скоро)", "ai:block:tts_gen:type:podcast"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_story_gen_minutes_select(cls) -> dict[str, Any]:
        builder = cls()
        for m in (3, 4, 5, 6, 7):
            builder.row((f"{m} мин", f"ai:block:tts_gen:minutes:{m}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_story_gen_review(cls, mode: str) -> dict[str, Any]:
        builder = cls()
        if mode == "ai":
            builder.row(("✅ Утвердить бриф", "ai:story_gen:approve"))
            builder.row(("🔄 Пример сказки", "ai:story_gen:preview"))
            builder.row(("✏️ Другой бриф", "ai:story_gen:edit_input"))
        else:
            builder.row(("✅ Сохранить", "ai:story_gen:approve"))
            builder.row(("✏️ Изменить текст", "ai:story_gen:edit_input"))
        builder.row(("↩ Назад к блокам", "ai:story_gen:cancel"))
        return builder.build()

    @classmethod
    def ai_tts_provider_select(cls, current: str = "sunor") -> dict[str, Any]:
        builder = cls()
        for pid, label in (
            ("sunor", "Sunor (Suno V5.5) — видео-сказка"),
            ("openai", "OpenAI TTS (только аудио)"),
        ):
            prefix = "✅ " if pid == current else ""
            builder.row((f"{prefix}{label}", f"ai:block:tts_gen:provider:{pid}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_tts_voice_select(
        cls, current: str = "dasha", provider: str = "speechkit"
    ) -> dict[str, Any]:
        from app.application.pipeline.tts_voices import (
            OPENAI_TTS_VOICES,
            SPEECHKIT_VOICES,
            TTS_PROVIDER_SPEECHKIT,
        )

        voices = (
            SPEECHKIT_VOICES
            if provider == TTS_PROVIDER_SPEECHKIT
            else OPENAI_TTS_VOICES
        )
        builder = cls()
        for voice_id, label in voices:
            prefix = "✅ " if voice_id == current else ""
            builder.row((f"{prefix}{label}", f"ai:block:tts_gen:voice:{voice_id}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_tts_speed_select(cls, current: float = 0.9) -> dict[str, Any]:
        from app.application.pipeline.tts_voices import TTS_SPEEDS

        builder = cls()
        for speed in TTS_SPEEDS:
            prefix = "✅ " if abs(float(current) - speed) < 0.001 else ""
            builder.row((f"{prefix}{speed}", f"ai:block:tts_gen:speed:{speed}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_tts_pitch_select(cls, current: float = 0) -> dict[str, Any]:
        from app.application.pipeline.tts_voices import TTS_PITCH_SHIFTS

        builder = cls()
        current_f = float(current)
        matched = False
        for pitch in TTS_PITCH_SHIFTS:
            selected = abs(current_f - pitch) < 0.001
            if selected:
                matched = True
            prefix = "✅ " if selected else ""
            label = f"{pitch:+g}" if pitch != 0 else "0"
            builder.row((f"{prefix}{label}", f"ai:block:tts_gen:pitch:{pitch}"))
        custom_prefix = "✅ " if not matched else ""
        builder.row((f"{custom_prefix}✏️ Своё число", "ai:block:tts_gen:pitch:custom"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_tts_role_select(
        cls, current: str = "neutral", voice: str = "dasha"
    ) -> dict[str, Any]:
        from app.application.pipeline.tts_voices import roles_for_voice

        builder = cls()
        for role_id, label in roles_for_voice(voice):
            prefix = "✅ " if role_id == current else ""
            builder.row((f"{prefix}{label}", f"ai:block:tts_gen:role:{role_id}"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_tts_instructions_select(cls, current: str = "bedtime") -> dict[str, Any]:
        builder = cls()
        for preset_id, label in (
            ("bedtime", "🌙 На ночь"),
            ("warm", "☀️ Тепло и живо"),
            ("whisper", "🤫 Почти шёпотом"),
            ("custom", "✏️ Свой стиль"),
        ):
            prefix = "✅ " if preset_id == current else ""
            builder.row(
                (f"{prefix}{label}", f"ai:block:tts_gen:style:{preset_id}")
            )
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_schedule_freq_select(cls, max_user_id: int | None = None) -> dict[str, Any]:
        builder = cls()
        if high_freq_allowed(max_user_id):
            builder.row(("8 раз в день", "ai:block:schedule:freq:8x_day"))
            builder.row(("7 раз в день", "ai:block:schedule:freq:7x_day"))
            builder.row(("6 раз в день", "ai:block:schedule:freq:6x_day"))
        builder.row(("5 раз в день", "ai:block:schedule:freq:5x_day"))
        builder.row(("4 раза в день", "ai:block:schedule:freq:4x_day"))
        builder.row(("3 раза в день", "ai:block:schedule:freq:3x_day"))
        builder.row(("2 раза в день", "ai:block:schedule:freq:2x_day"))
        builder.row(("1 раз в день", "ai:block:schedule:freq:daily"))
        builder.row(("2 раза в неделю", "ai:block:schedule:freq:2x_week"))
        builder.row(("1 раз в неделю", "ai:block:schedule:freq:weekly"))
        builder.row(("Назад к блокам", "ai:back_to_blocks"))
        builder.row(("На главную", "main_menu"))
        return builder.build()

    @classmethod
    def ai_schedule_time_picker(cls, slot_info: str = "") -> dict[str, Any]:
        # slot_info kept for call-site compat; shown in message text instead.
        _ = slot_info
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
    def ai_schedule_per_slot_toggle(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("Да, разные промпты", "ai:block:schedule:per_slot:yes"))
            .row(("Нет, общий бриф", "ai:block:schedule:per_slot:no"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_schedule_slot_prompt_actions(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("Добавить к общему брифу", "ai:block:schedule:slot_prompt:append"))
            .row(("Как общий бриф", "ai:block:schedule:slot_prompt:skip"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )

    @classmethod
    def ai_schedule_slot_image_actions(cls) -> dict[str, Any]:
        return (
            cls()
            .row(("Без добавки", "ai:block:schedule:slot_image:skip"))
            .row(("Назад к блокам", "ai:back_to_blocks"))
            .row(("На главную", "main_menu"))
            .build()
        )
