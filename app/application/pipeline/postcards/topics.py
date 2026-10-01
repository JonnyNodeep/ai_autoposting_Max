"""Season hints and postcard topic resolution per schedule slot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.application.pipeline.postcards.calendar import (
    birthday_entry,
    daily_for,
    msk_today,
    official_for,
)
from app.application.pipeline.topic_queue import normalize_topic_queue

# MSK 07:23 / 11:37 / 12:37 / 19:19
MORNING_UTC = "04:23"
OFFICIAL_UTC = "08:37"
DAILY_UTC = "09:37"
EVENING_UTC = "16:19"

SLOT_ROLES: dict[str, str] = {
    MORNING_UTC: "morning",
    OFFICIAL_UTC: "official",
    DAILY_UTC: "daily",
    EVENING_UTC: "evening",
}

SEASON_BY_MONTH: dict[int, str] = {
    1: "зимний уют, снег, мягкий свет",
    2: "поздняя зима, нежный свет, тепло дома",
    3: "ранняя весна, первые цветы, свежий воздух",
    4: "весна, цветение, светлая палитра",
    5: "майская зелень, тепло, праздничное настроение",
    6: "лето, солнечный свет, свежесть",
    7: "середина лета, яркие краски, тепло",
    8: "поздний август, золотистый свет, урожай",
    9: "ранняя осень, золотые листья, уют",
    10: "золотая осень, тёплые тона, мягкий свет",
    11: "поздняя осень, спокойные тона, домашний уют",
    12: "декабрь, зимние огоньки, праздничное тепло",
}


@dataclass(frozen=True)
class PostcardTopic:
    kind: str
    title: str
    short_label: str
    season_hint: str
    slot_role: str


def season_hint_for(day_month: int) -> str:
    return SEASON_BY_MONTH.get(int(day_month), "уютная атмосфера, мягкий свет")


def slot_role_for_time(slot_time: str | None) -> str:
    key = str(slot_time or "").strip()
    return SLOT_ROLES.get(key, "")


def pop_slot_topic_from_schedule(
    schedule: dict[str, Any] | None,
    slot_time: str | None,
) -> tuple[str | None, list[str]]:
    """FIFO pop from schedule.slot_topic_queues[slot_time]; return (topic, remaining)."""
    schedule = schedule or {}
    key = str(slot_time or "").strip()
    queues = dict(schedule.get("slot_topic_queues") or {})
    queue = normalize_topic_queue(queues.get(key) or [])
    if not queue:
        return None, []
    topic = queue[0]
    remaining = queue[1:]
    return topic, remaining


def resolve_postcard_topic(
    schedule: dict[str, Any] | None,
    slot_time: str | None,
    *,
    now: datetime | None = None,
    queued_topic: str | None = None,
) -> PostcardTopic:
    """Resolve topic for a postcard slot using MSK calendar date."""
    schedule = schedule or {}
    day = msk_today(now)
    season = season_hint_for(day.month)
    role = slot_role_for_time(slot_time)
    key = str(slot_time or "").strip()

    if role == "morning" or key == MORNING_UTC:
        title = (queued_topic or "").strip() or "Доброе утро"
        label = _morning_label(title)
        return PostcardTopic(
            kind="queue",
            title=title,
            short_label=label,
            season_hint=season,
            slot_role="morning",
        )

    if role == "evening" or key == EVENING_UTC:
        title = (queued_topic or "").strip() or "Спокойной ночи"
        label = _evening_label(title)
        return PostcardTopic(
            kind="queue",
            title=title,
            short_label=label,
            season_hint=season,
            slot_role="evening",
        )

    if role == "official" or key == OFFICIAL_UTC:
        official = official_for(day, now=now)
        if official is not None:
            return PostcardTopic(
                kind="official",
                title=official.title,
                short_label=official.short_label,
                season_hint=season,
                slot_role="official",
            )
        bday = birthday_entry()
        return PostcardTopic(
            kind="birthday",
            title=bday.title,
            short_label=bday.short_label,
            season_hint=season,
            slot_role="official",
        )

    # daily / default
    daily = daily_for(day, now=now)
    return PostcardTopic(
        kind="daily",
        title=daily.title,
        short_label=daily.short_label,
        season_hint=season,
        slot_role="daily",
    )


def _morning_label(title: str) -> str:
    low = title.lower()
    if "утр" in low:
        return "С добрым утром!"
    if "люб" in low:
        return "С любовью!"
    if "семь" in low:
        return "Семье с теплом!"
    if "друж" in low:
        return "Друзьям с теплом!"
    if "мотив" in low:
        return "Ты справишься!"
    return "С добрым утром!"


def _evening_label(title: str) -> str:
    low = title.lower()
    if "ноч" in low or "спокой" in low:
        return "Спокойной ночи!"
    if "люб" in low or "тепл" in low:
        return "Тепла и любви!"
    if "семь" in low:
        return "Семейного уюта!"
    return "Доброго вечера!"
