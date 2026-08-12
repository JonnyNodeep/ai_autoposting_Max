"""One-off: sync clean RSS keyword lists from Redis FSM into pipeline runs."""

from __future__ import annotations

import asyncio
import copy
import json

import redis.asyncio as aioredis
from sqlalchemy import select

from app.application.pipeline.rss_monitor import normalize_news_rss
from app.config import settings
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.models.pipeline_run import PipelineRunModel

MAX_USER_ID = 214051271
CHANNEL_ID = 12
FSM_KEY = f"ai_studio:{MAX_USER_ID}"


async def main() -> None:
    r = aioredis.from_url(settings.redis.redis_url, decode_responses=True)
    raw = await r.get(FSM_KEY)
    if not raw:
        raise SystemExit("FSM state not found in Redis")

    state = json.loads(raw)
    if state.get("channel_id") != CHANNEL_ID:
        raise SystemExit(f"Unexpected channel_id={state.get('channel_id')}")

    blocks = state.setdefault("blocks", {})
    news = normalize_news_rss(blocks.get("news_rss") or {})
    news["keywords_source"] = "manual"
    blocks["news_rss"] = news
    state["blocks"] = blocks

    await r.set(FSM_KEY, json.dumps(state, ensure_ascii=False))

    inc = news["include_keywords"]
    exc = news["exclude_keywords"]
    print(f"Redis FSM synced: +{len(inc)} / -{len(exc)}")

    async with async_session_factory() as session:
        result = await session.execute(
            select(PipelineRunModel).where(PipelineRunModel.channel_id == CHANNEL_ID)
        )
        runs = result.scalars().all()
        for run in runs:
            cfg = copy.deepcopy(run.blocks_config or {})
            old_news = cfg.get("news_rss") or {}
            cfg["news_rss"] = normalize_news_rss({**old_news, **news})
            run.blocks_config = cfg
        await session.commit()
        print(f"Updated pipeline_runs for channel {CHANNEL_ID}: {len(runs)} rows")

    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
