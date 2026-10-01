"""MSK date, moon phase, and deterministic midday focus for horoscope posts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

WEEKDAY_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)

ZODIAC_SIGNS = (
    "Овен",
    "Телец",
    "Близнецы",
    "Рак",
    "Лев",
    "Дева",
    "Весы",
    "Скорпион",
    "Стрелец",
    "Козерог",
    "Водолей",
    "Рыбы",
)

RELATION_ROLES = (
    "ты + партнёр",
    "ты + босс",
    "ты + мама",
    "ты + близкий друг",
    "ты + коллега",
    "ты + ты сам(а)",
)

# Approximate synodic month; epoch near a known new moon (2000-01-06).
_SYNODIC_DAYS = 29.530588853
_NEW_MOON_EPOCH = date(2000, 1, 6)


@dataclass(frozen=True)
class AstroContext:
    day: date
    weekday_ru: str
    weekday_index: int  # 0=Mon .. 6=Sun
    moon_phase_ru: str
    midday_focus: str
    sign_of_day: str


def msk_today(now: datetime | None = None) -> date:
    if now is None:
        now = datetime.now(tz=MSK)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=MSK)
    else:
        now = now.astimezone(MSK)
    return now.date()


def moon_phase_ru(day: date) -> str:
    """Return a soft four-phase label for editorial use (not ephemeris-grade)."""
    age = (day - _NEW_MOON_EPOCH).days % _SYNODIC_DAYS
    # Buckets: new ~0, waxing, full ~14.77, waning
    if age < 1.85 or age >= 27.68:
        return "новолуние"
    if age < 12.92:
        return "растущая луна"
    if age < 16.61:
        return "полнолуние"
    return "убывающая луна"


def _pair_from_day(day: date) -> tuple[str, str]:
    n = day.toordinal()
    a = ZODIAC_SIGNS[n % 12]
    b = ZODIAC_SIGNS[(n // 3 + 5) % 12]
    if a == b:
        b = ZODIAC_SIGNS[(n + 7) % 12]
    return a, b


def midday_focus(day: date) -> str:
    """Deterministic compatibility angle for the midday slot."""
    weekday = day.weekday()  # Mon=0
    a, b = _pair_from_day(day)
    role = RELATION_ROLES[(day.toordinal() + weekday) % len(RELATION_ROLES)]
    # Prefer sign pairs on Mon/Wed/Fri/Sat; role frames on Tue/Thu/Sun
    if weekday in (1, 3, 6):
        return f"{role} (опора на черты {a} и {b})"
    return f"пара знаков: {a} + {b}"


def sign_of_day(day: date) -> str:
    return ZODIAC_SIGNS[day.toordinal() % 12]


def build_astro_context(now: datetime | None = None) -> AstroContext:
    day = msk_today(now)
    idx = day.weekday()
    return AstroContext(
        day=day,
        weekday_ru=WEEKDAY_RU[idx],
        weekday_index=idx,
        moon_phase_ru=moon_phase_ru(day),
        midday_focus=midday_focus(day),
        sign_of_day=sign_of_day(day),
    )


def format_astro_context_block(
    ctx: AstroContext,
    *,
    slot_time: str | None = None,
    midday_utc: str = "11:08",
) -> str:
    """Prefix for the generation brief."""
    date_s = ctx.day.strftime("%d.%m.%Y")
    lines = [
        "Контекст дня (обязательно учесть):",
        f"— Дата (МСК): {date_s}, {ctx.weekday_ru}",
        f"— Фаза луны: {ctx.moon_phase_ru}",
        f"— Знак дня: {ctx.sign_of_day}",
    ]
    if slot_time and str(slot_time).strip() == midday_utc:
        lines.append(f"— Фокус совместимости сегодня: {ctx.midday_focus}")
    return "\n".join(lines)
