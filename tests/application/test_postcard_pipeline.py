"""Tests for Living Postcards calendar, topics, copy, and preset."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.application.pipeline.normalize import normalize_blocks_config, steps_to_ui_dict
from app.application.pipeline.postcards.calendar import (
    daily_for,
    get_day,
    msk_today,
    official_for,
)
from app.application.pipeline.postcards.copy import (
    MAX_POEM_CHARS,
    postcard_image_instruction,
    trim_poem,
)
from app.application.pipeline.postcards.presets import build_postcard_blocks_ui
from app.application.pipeline.postcards.topics import (
    DAILY_UTC,
    EVENING_UTC,
    MORNING_UTC,
    OFFICIAL_UTC,
    resolve_postcard_topic,
)


MSK = ZoneInfo("Europe/Moscow")


def test_calendar_has_365_days():
    # load via lookups across year
    days = []
    d = date(2026, 1, 1)
    while d.year == 2026:
        entry = get_day(d)
        assert entry.daily.title
        assert entry.daily.short_label
        days.append(entry.date.isoformat())
        if d.month == 12 and d.day == 31:
            break
        from datetime import timedelta

        d += timedelta(days=1)
    assert len(days) == 365
    assert len(set(days)) == 365


def test_official_womens_day_and_victory():
    assert official_for(date(2026, 3, 8)) is not None
    assert "Марта" in official_for(date(2026, 3, 8)).short_label
    assert official_for(date(2026, 5, 9)) is not None
    assert "Победы" in official_for(date(2026, 5, 9)).short_label


def test_daily_always_present():
    day = daily_for(date(2026, 10, 5))
    assert "учителя" in day.title.lower() or "Учителя" in day.title


def test_msk_today_uses_moscow():
    # 2026-10-01 01:30 UTC == 04:30 MSK same calendar day
    now = datetime(2026, 10, 1, 1, 30, tzinfo=ZoneInfo("UTC"))
    assert msk_today(now) == date(2026, 10, 1)
    # 2026-09-30 21:30 UTC == 2026-10-01 00:30 MSK
    now2 = datetime(2026, 9, 30, 21, 30, tzinfo=ZoneInfo("UTC"))
    assert msk_today(now2) == date(2026, 10, 1)


def test_resolve_official_or_birthday():
    topic = resolve_postcard_topic({}, OFFICIAL_UTC, now=datetime(2026, 3, 8, 12, tzinfo=MSK))
    assert topic.kind == "official"
    assert "8 Марта" in topic.short_label or "Марта" in topic.short_label

    topic2 = resolve_postcard_topic({}, OFFICIAL_UTC, now=datetime(2026, 4, 3, 12, tzinfo=MSK))
    assert topic2.kind == "birthday"
    assert topic2.short_label == "С днём рождения!"


def test_resolve_daily_slot():
    topic = resolve_postcard_topic({}, DAILY_UTC, now=datetime(2026, 10, 5, 12, tzinfo=MSK))
    assert topic.slot_role == "daily"
    assert topic.short_label


def test_resolve_morning_queue_topic():
    topic = resolve_postcard_topic(
        {},
        MORNING_UTC,
        now=datetime(2026, 10, 1, 8, tzinfo=MSK),
        queued_topic="Доброе утро и тёплая семья",
    )
    assert topic.slot_role == "morning"
    assert topic.title.startswith("Доброе утро")


def test_trim_poem_respects_limit():
    long = "\n".join(["Строка поздравления номер %d!" % i for i in range(40)])
    trimmed = trim_poem(long, max_chars=MAX_POEM_CHARS)
    assert len(trimmed) <= MAX_POEM_CHARS
    assert trimmed


def test_postcard_image_instruction_has_label():
    from app.application.pipeline.postcards.topics import PostcardTopic

    t = PostcardTopic(
        kind="official",
        title="8 марта",
        short_label="С 8 Марта!",
        season_hint="весна",
        slot_role="official",
    )
    text = postcard_image_instruction(t)
    assert "С 8 Марта!" in text
    assert "кириллиц" in text.lower()
    assert "питомц" in text.lower()
    assert "только если" in text.lower()


def test_postcard_preset_normalize():
    ui = build_postcard_blocks_ui()
    v2 = normalize_blocks_config(ui)
    assert v2["schedule"]["postcard_pipeline"] is True
    assert not v2["schedule"]["meditation_pipeline"]
    times = v2["schedule"]["times"]
    assert times == [MORNING_UTC, OFFICIAL_UTC, DAILY_UTC, EVENING_UTC]
    types = [s["type"] for s in v2["steps"]]
    assert "motion_fx" in types
    motion = next(s for s in v2["steps"] if s["type"] == "motion_fx")
    assert motion["enabled"] is True
    video = next(s for s in v2["steps"] if s["type"] == "video_gen")
    assert video["enabled"] is False
    ui2 = steps_to_ui_dict(v2)
    assert ui2["schedule"]["postcard_pipeline"] is True
    assert MORNING_UTC in ui2["schedule"]["slot_topic_queues"]
    assert EVENING_UTC in ui2["schedule"]["slot_topic_queues"]


def test_choose_motion_preset():
    from app.application.pipeline.postcards.motion import (
        ALL_OVERLAYS,
        PRESET_GROUPS,
        choose_preset,
    )
    import random

    assert len(ALL_OVERLAYS) == 20
    named = {n for group in PRESET_GROUPS.values() for n in group}
    assert named == set(ALL_OVERLAYS)
    rng = random.Random(0)
    name = choose_preset("morning", rng=rng)
    assert name in PRESET_GROUPS["morning"]
