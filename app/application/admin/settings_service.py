"""App settings store (key/value JSON in Postgres)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models.app_setting import AppSettingModel

KEY_MAX_USERS = "max_users"
KEY_BILLING_PRICES = "billing_prices"
KEY_RSS_WHITELIST = "feature_rss_whitelist"
KEY_VIDEO_WHITELIST = "feature_video_whitelist"
KEY_AUDIO_WHITELIST = "feature_audio_whitelist"
KEY_DRIVE_WHITELIST = "feature_drive_whitelist"
KEY_HIGH_FREQ_WHITELIST = "feature_high_freq_whitelist"

DEFAULT_BILLING_PRICES: dict[str, Any] = {
    "base": {"solo": 490, "creator": 1990, "studio": 3490},
    "per_post": {"solo": 12, "creator": 11, "studio": 10},
    "channels": {"solo": 1, "creator": 5, "studio": 10},
    "posts_per_day_options": [1, 2, 3, 5],
}


class AppSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str, default: Any = None) -> Any:
        row = await self._session.get(AppSettingModel, key)
        if row is None:
            return default
        return row.value

    async def set(self, key: str, value: Any) -> None:
        row = await self._session.get(AppSettingModel, key)
        if row is None:
            self._session.add(AppSettingModel(key=key, value=value))
        else:
            row.value = value
        await self._session.flush()

    async def get_max_users(self) -> int:
        raw = await self.get(KEY_MAX_USERS, 10)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 10

    async def set_max_users(self, value: int) -> None:
        await self.set(KEY_MAX_USERS, max(0, int(value)))

    async def get_billing_prices(self) -> dict[str, Any]:
        raw = await self.get(KEY_BILLING_PRICES, DEFAULT_BILLING_PRICES)
        if not isinstance(raw, dict):
            return dict(DEFAULT_BILLING_PRICES)
        merged = dict(DEFAULT_BILLING_PRICES)
        for k, v in raw.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        return merged

    async def set_billing_prices(self, value: dict[str, Any]) -> None:
        await self.set(KEY_BILLING_PRICES, value)

    async def get_whitelist(self, key: str) -> str:
        raw = await self.get(key, "")
        return str(raw or "")

    async def set_whitelist(self, key: str, value: str) -> None:
        await self.set(key, (value or "").strip())
