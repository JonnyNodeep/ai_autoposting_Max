"""Shared schedule frequency keys, labels, and slot counts."""

from __future__ import annotations

HIGH_FREQ_KEYS = frozenset({"6x_day", "7x_day", "8x_day"})

FREQ_SLOTS: dict[str, int] = {
    "2x_day": 2,
    "3x_day": 3,
    "4x_day": 4,
    "5x_day": 5,
    "6x_day": 6,
    "7x_day": 7,
    "8x_day": 8,
}

FREQ_LABELS: dict[str, str] = {
    "daily": "1 раз в день",
    "2x_day": "2 раза в день",
    "3x_day": "3 раза в день",
    "4x_day": "4 раза в день",
    "5x_day": "5 раз в день",
    "6x_day": "6 раз в день",
    "7x_day": "7 раз в день",
    "8x_day": "8 раз в день",
    "2x_week": "2 раза в неделю",
    "weekly": "1 раз в неделю",
}

FREQ_LABELS_SHORT: dict[str, str] = {
    "daily": "1×/день",
    "2x_day": "2×/день",
    "3x_day": "3×/день",
    "4x_day": "4×/день",
    "5x_day": "5×/день",
    "6x_day": "6×/день",
    "7x_day": "7×/день",
    "8x_day": "8×/день",
    "2x_week": "2×/нед.",
    "weekly": "1×/нед.",
}


def is_high_freq(freq: str) -> bool:
    return str(freq or "") in HIGH_FREQ_KEYS


def is_multi_slot_freq(freq: str) -> bool:
    return str(freq or "") in FREQ_SLOTS


def expected_slots(freq: str) -> int:
    return FREQ_SLOTS.get(str(freq or ""), 1)


def freq_label(freq: str, *, short: bool = False) -> str:
    labels = FREQ_LABELS_SHORT if short else FREQ_LABELS
    key = str(freq or "")
    return labels.get(key, key)
