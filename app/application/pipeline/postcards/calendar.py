"""Russia postcard calendar: MSK date lookup for official and daily holidays."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
_CALENDAR_PATH = Path(__file__).with_name("ru_calendar_2026.json")

BIRTHDAY_TITLE = "День рождения"
BIRTHDAY_SHORT_LABEL = "С днём рождения!"


@dataclass(frozen=True)
class HolidayEntry:
    title: str
    short_label: str
    tier: str = ""


@dataclass(frozen=True)
class DayEntry:
    date: date
    official: HolidayEntry | None
    daily: HolidayEntry


def msk_today(now: datetime | None = None) -> date:
    """Return today's calendar date in Europe/Moscow."""
    if now is None:
        now = datetime.now(tz=MSK)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=MSK)
    else:
        now = now.astimezone(MSK)
    return now.date()


def _parse_holiday(raw: Any, default_tier: str = "") -> HolidayEntry | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    label = str(raw.get("short_label") or "").strip()
    if not title or not label:
        return None
    tier = str(raw.get("tier") or default_tier).strip()
    return HolidayEntry(title=title, short_label=label, tier=tier)


@lru_cache(maxsize=4)
def _load_year(year: int) -> dict[str, DayEntry]:
    path = Path(__file__).with_name(f"ru_calendar_{year}.json")
    if not path.exists():
        if year == 2026:
            path = _CALENDAR_PATH
        else:
            raise FileNotFoundError(f"Postcard calendar not found for year={year}: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    days_raw = payload.get("days") or []
    out: dict[str, DayEntry] = {}
    for item in days_raw:
        if not isinstance(item, dict):
            continue
        date_s = str(item.get("date") or "").strip()
        if not date_s:
            continue
        d = date.fromisoformat(date_s)
        daily = _parse_holiday(item.get("daily"), default_tier="folk")
        if daily is None:
            continue
        official = _parse_holiday(item.get("official"), default_tier="official")
        out[date_s] = DayEntry(date=d, official=official, daily=daily)
    return out


def get_day(day: date | None = None, *, now: datetime | None = None) -> DayEntry:
    """Lookup calendar row for a date (defaults to MSK today)."""
    if day is None:
        day = msk_today(now)
    by_date = _load_year(day.year)
    key = day.isoformat()
    entry = by_date.get(key)
    if entry is None:
        # Soft fallback so pipeline never crashes on missing year file gaps
        return DayEntry(
            date=day,
            official=None,
            daily=HolidayEntry(
                title="День тёплых пожеланий",
                short_label="С теплом и заботой!",
                tier="folk",
            ),
        )
    return entry


def official_for(day: date | None = None, *, now: datetime | None = None) -> HolidayEntry | None:
    return get_day(day, now=now).official


def daily_for(day: date | None = None, *, now: datetime | None = None) -> HolidayEntry:
    return get_day(day, now=now).daily


def birthday_entry() -> HolidayEntry:
    return HolidayEntry(
        title=BIRTHDAY_TITLE,
        short_label=BIRTHDAY_SHORT_LABEL,
        tier="folk",
    )
