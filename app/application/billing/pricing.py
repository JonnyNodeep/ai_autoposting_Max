"""Subscription pricing: tier base + posts_per_day package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PERIOD_DAYS = 30
POSTS_PER_DAY_OPTIONS = (1, 2, 3, 5)

TIER_ORDER = ("solo", "creator", "studio")

TIER_CHANNELS = {
    "solo": 1,
    "creator": 5,
    "studio": 10,
}

TIER_BASE = {
    "solo": 490,
    "creator": 1990,
    "studio": 3490,
}

TIER_PER_POST = {
    "solo": 12,
    "creator": 11,
    "studio": 10,
}

TIER_LABELS = {
    "solo": "Solo — 1 канал",
    "creator": "Creator — до 5 каналов",
    "studio": "Studio — до 10 каналов",
}

_runtime_prices: dict[str, Any] | None = None


def set_runtime_prices(data: dict[str, Any] | None) -> None:
    """Override in-memory price tables (from app_settings)."""
    global _runtime_prices
    _runtime_prices = data


def get_runtime_prices() -> dict[str, Any] | None:
    return _runtime_prices


def _bases() -> dict[str, int]:
    if _runtime_prices and isinstance(_runtime_prices.get("base"), dict):
        return {**TIER_BASE, **{k: int(v) for k, v in _runtime_prices["base"].items()}}
    return dict(TIER_BASE)


def _per_posts() -> dict[str, int]:
    if _runtime_prices and isinstance(_runtime_prices.get("per_post"), dict):
        return {
            **TIER_PER_POST,
            **{k: int(v) for k, v in _runtime_prices["per_post"].items()},
        }
    return dict(TIER_PER_POST)


def _channels() -> dict[str, int]:
    if _runtime_prices and isinstance(_runtime_prices.get("channels"), dict):
        return {
            **TIER_CHANNELS,
            **{k: int(v) for k, v in _runtime_prices["channels"].items()},
        }
    return dict(TIER_CHANNELS)


def posts_per_day_options() -> tuple[int, ...]:
    if _runtime_prices and isinstance(_runtime_prices.get("posts_per_day_options"), list):
        vals = [int(x) for x in _runtime_prices["posts_per_day_options"]]
        if vals:
            return tuple(vals)
    return POSTS_PER_DAY_OPTIONS


@dataclass(frozen=True, slots=True)
class PriceQuote:
    tier: str
    posts_per_day: int
    period_days: int
    quota: int
    amount: int
    channels: int
    label: str

    @property
    def description(self) -> str:
        return (
            f"{self.label}, {self.posts_per_day} пуб./день "
            f"({self.quota} на {self.period_days} дн.)"
        )


def calc_quota(posts_per_day: int, days: int = PERIOD_DAYS) -> int:
    ppd = max(1, int(posts_per_day))
    return ppd * max(1, int(days))


def calc_price(tier: str, posts_per_day: int) -> int:
    key = (tier or "solo").strip().lower()
    bases = _bases()
    per = _per_posts()
    if key not in bases:
        key = "solo"
    ppd = int(posts_per_day)
    options = posts_per_day_options()
    if ppd not in options:
        raise ValueError(f"posts_per_day must be one of {options}")
    return int(bases[key]) + ppd * PERIOD_DAYS * int(per[key])


def quote(tier: str, posts_per_day: int) -> PriceQuote:
    key = (tier or "solo").strip().lower()
    bases = _bases()
    channels = _channels()
    if key not in bases:
        key = "solo"
    ppd = int(posts_per_day)
    options = posts_per_day_options()
    if ppd not in options:
        raise ValueError(f"posts_per_day must be one of {options}")
    return PriceQuote(
        tier=key,
        posts_per_day=ppd,
        period_days=PERIOD_DAYS,
        quota=calc_quota(ppd),
        amount=calc_price(key, ppd),
        channels=int(channels.get(key, TIER_CHANNELS.get(key, 1))),
        label=TIER_LABELS.get(key, key),
    )


def apply_discount(amount: int, discount_percent: int) -> tuple[int, int]:
    """Return (amount_before_discount, final_amount)."""
    before = max(0, int(amount))
    pct = max(0, min(100, int(discount_percent or 0)))
    if pct <= 0 or before <= 0:
        return before, before
    final = max(1, int(round(before * (100 - pct) / 100)))
    return before, final


def tier_rank(tier: str) -> int:
    key = (tier or "solo").strip().lower()
    try:
        return TIER_ORDER.index(key)
    except ValueError:
        return 0


def is_upgrade(
    current_tier: str,
    current_posts: int,
    new_tier: str,
    new_posts: int,
) -> bool:
    """True if new package is strictly better (more channels and/or more posts)."""
    better_tier = tier_rank(new_tier) > tier_rank(current_tier)
    better_posts = int(new_posts) > int(current_posts)
    same_or_better_tier = tier_rank(new_tier) >= tier_rank(current_tier)
    same_or_better_posts = int(new_posts) >= int(current_posts)
    if not (same_or_better_tier and same_or_better_posts):
        return False
    return better_tier or better_posts


def prorated_upgrade_amount(
    old_tier: str,
    old_posts: int,
    new_tier: str,
    new_posts: int,
    remaining_days: float,
) -> int:
    old_price = calc_price(old_tier, old_posts)
    new_price = calc_price(new_tier, new_posts)
    if new_price <= old_price:
        return 0
    ratio = max(0.0, min(1.0, float(remaining_days) / PERIOD_DAYS))
    return max(1, int(round((new_price - old_price) * ratio)))


def remaining_days(expires_at, *, now=None) -> float:
    from datetime import UTC, datetime

    if now is None:
        now = datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    delta = (expires_at - now).total_seconds() / 86400.0
    return max(0.0, delta)
