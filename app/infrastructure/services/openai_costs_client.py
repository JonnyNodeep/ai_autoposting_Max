from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from loguru import logger

from app.config import settings


class OpenAICostsClient:
    """Fetch organization spend via OpenAI Costs API (requires Admin API key)."""

    def __init__(self, admin_api_key: str | None = None) -> None:
        self._api_key = admin_api_key or settings.openai.admin_api_key

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def get_costs(self, days: int = 30) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("OPENAI_ADMIN_API_KEY is not configured")

        now = datetime.now(UTC)
        start = now - timedelta(days=days)
        start_time = int(start.timestamp())
        end_time = int(now.timestamp())

        total_cost = 0.0
        buckets = 0
        by_line_item: dict[str, float] = {}
        page: str | None = None

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            while True:
                params: dict[str, Any] = {
                    "start_time": start_time,
                    "end_time": end_time,
                    "bucket_width": "1d",
                    "limit": min(max(days, 1), 180),
                    "group_by": ["line_item"],
                }
                if page:
                    params["page"] = page

                response = await client.get(
                    "https://api.openai.com/v1/organization/costs",
                    headers=headers,
                    params=params,
                )
                if response.status_code >= 400:
                    logger.error(
                        f"OpenAI Costs API status={response.status_code} "
                        f"body={response.text[:500]}"
                    )
                response.raise_for_status()
                payload = response.json()

                for bucket in payload.get("data") or []:
                    buckets += 1
                    for result in bucket.get("results") or []:
                        amount = result.get("amount") or {}
                        value = float(amount.get("value") or 0)
                        total_cost += value
                        line_item = str(result.get("line_item") or "other")
                        by_line_item[line_item] = by_line_item.get(line_item, 0.0) + value

                page = payload.get("next_page")
                if not page:
                    break

        top_items = sorted(by_line_item.items(), key=lambda x: x[1], reverse=True)[:8]
        return {
            "days": days,
            "total_cost": round(total_cost, 4),
            "currency": "usd",
            "buckets": buckets,
            "by_line_item": {k: round(v, 4) for k, v in top_items},
            "source": "openai_costs_api",
        }
