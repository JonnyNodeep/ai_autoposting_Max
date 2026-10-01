"""Tests for Astro Oracle (horoscope) context, preset, and brief injection."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.application.pipeline.horoscope.context import (
    build_astro_context,
    format_astro_context_block,
    midday_focus,
    moon_phase_ru,
    msk_today,
    sign_of_day,
)
from app.application.pipeline.horoscope_presets import (
    EVENING_UTC,
    MIDDAY_UTC,
    MORNING_UTC,
    build_horoscope_blocks_ui,
    build_horoscope_blocks_v2,
    slot_msk_label,
)
from app.application.pipeline.normalize import normalize_blocks_config, steps_to_ui_dict


MSK = ZoneInfo("Europe/Moscow")


def test_msk_today_uses_moscow():
    now = datetime(2026, 10, 1, 22, 30, tzinfo=MSK)
    assert msk_today(now) == date(2026, 10, 1)


def test_moon_phase_stable_buckets():
    # Known-ish new moon vicinity around 2000-01-06 epoch
    assert moon_phase_ru(date(2000, 1, 6)) == "новолуние"
    waxing = moon_phase_ru(date(2000, 1, 13))
    assert waxing in ("растущая луна", "полнолуние", "новолуние", "убывающая луна")
    # Same date always same label
    d = date(2026, 10, 1)
    assert moon_phase_ru(d) == moon_phase_ru(d)


def test_midday_focus_deterministic():
    d = date(2026, 10, 1)  # Thursday
    assert midday_focus(d) == midday_focus(d)
    assert midday_focus(d) != midday_focus(date(2026, 10, 2))


def test_build_astro_context_weekday():
    now = datetime(2026, 10, 1, 12, 0, tzinfo=MSK)  # Thursday
    ctx = build_astro_context(now)
    assert ctx.weekday_ru == "четверг"
    assert ctx.weekday_index == 3
    assert ctx.sign_of_day == sign_of_day(date(2026, 10, 1))
    assert ctx.moon_phase_ru
    assert ctx.midday_focus


def test_format_astro_context_includes_midday_only_for_midday_slot():
    ctx = build_astro_context(datetime(2026, 10, 1, 12, 0, tzinfo=MSK))
    morning = format_astro_context_block(ctx, slot_time=MORNING_UTC)
    midday = format_astro_context_block(ctx, slot_time=MIDDAY_UTC)
    assert "Дата (МСК)" in morning
    assert "Фаза луны" in morning
    assert "Фокус совместимости" not in morning
    assert "Фокус совместимости" in midday
    assert ctx.midday_focus in midday


def test_horoscope_preset_normalize():
    ui = build_horoscope_blocks_ui()
    v2 = normalize_blocks_config(ui)
    sched = v2["schedule"]
    assert sched["horoscope_pipeline"] is True
    assert sched["meditation_pipeline"] is False
    assert sched["postcard_pipeline"] is False
    assert sched["podcast_pipeline"] is False
    assert sched["per_slot_prompts"] is True
    assert sched["times"] == [MORNING_UTC, MIDDAY_UTC, EVENING_UTC]
    assert MORNING_UTC in sched["slot_prompts"]
    assert MIDDAY_UTC in sched["slot_prompts"]
    assert EVENING_UTC in sched["slot_prompts"]
    assert sched["slot_topic_queues"] == {}

    post = next(s for s in v2["steps"] if s["type"] == "post_gen")
    assert post["enabled"] is True
    assert post["config"]["add_channel_link"] is False
    assert post["config"]["mode"] == "ai"

    img_prompt = next(s for s in v2["steps"] if s["type"] == "image_prompt")
    assert img_prompt["enabled"] is True
    assert img_prompt["config"]["mode"] == "from_topic"
    assert "заголовк" in (img_prompt["config"].get("instruction") or "").lower()
    assert "питомц" in (img_prompt["config"].get("instruction") or "").lower()

    img = next(s for s in v2["steps"] if s["type"] == "image_gen")
    assert img["enabled"] is True
    assert img["config"]["allow_text"] is False

    ui2 = steps_to_ui_dict(v2)
    assert ui2["schedule"]["horoscope_pipeline"] is True
    assert slot_msk_label(MORNING_UTC) == "08:17"
    assert slot_msk_label(MIDDAY_UTC) == "14:08"
    assert slot_msk_label(EVENING_UTC) == "20:01"


def test_horoscope_excludes_other_pipelines():
    ui = build_horoscope_blocks_ui()
    ui["schedule"]["meditation_pipeline"] = True
    ui["schedule"]["postcard_pipeline"] = True
    v2 = normalize_blocks_config(ui)
    assert v2["schedule"]["horoscope_pipeline"] is True
    assert v2["schedule"]["meditation_pipeline"] is False
    assert v2["schedule"]["postcard_pipeline"] is False


def test_build_horoscope_blocks_v2_roundtrip():
    v2 = build_horoscope_blocks_v2()
    assert v2["version"] == 2
    assert v2["schedule"]["horoscope_pipeline"] is True


@pytest.mark.asyncio
async def test_runner_injects_astro_context_into_brief(monkeypatch):
    from app.application.pipeline.runner import PipelineRunner
    from app.application.pipeline.context import PipelineContext

    captured: dict = {}

    async def fake_generate_post_text(openai_client, brief, channel_title, **kwargs):
        captured["brief"] = brief
        captured["forbid_subscribe_cta"] = kwargs.get("forbid_subscribe_cta")
        return "тест пост", "тема"

    async def fake_fetch_recent(*_a, **_k):
        return []

    monkeypatch.setattr(
        "app.application.pipeline.generate_post.generate_post_text",
        fake_generate_post_text,
    )
    # runner imports generate_post_text at module level — patch where used
    import app.application.pipeline.runner as runner_mod

    monkeypatch.setattr(runner_mod, "generate_post_text", fake_generate_post_text)
    monkeypatch.setattr(runner_mod, "fetch_recent_post_topics", fake_fetch_recent)

    ui = build_horoscope_blocks_ui()
    v2 = normalize_blocks_config(ui)

    ctx = MagicMock(spec=PipelineContext)
    ctx.meta = {"slot_time": MIDDAY_UTC}
    ctx.target = "channel"
    ctx.channel = MagicMock(max_chat_id=None)
    ctx.channel_title = "Астро"
    ctx.openai_client = MagicMock()
    ctx.max_client = MagicMock()
    ctx.run_id = 1
    ctx.notify = AsyncMock()
    ctx.post_text = ""

    runner = PipelineRunner.__new__(PipelineRunner)
    await runner._preseed_post_text(ctx, v2)

    assert "Контекст дня" in captured["brief"]
    assert "Фокус совместимости" in captured["brief"]
    assert captured["forbid_subscribe_cta"] is True
    assert ctx.meta.get("horoscope_weekday")
    assert ctx.meta.get("horoscope_midday_focus")
    assert ctx.post_text == "тест пост"
