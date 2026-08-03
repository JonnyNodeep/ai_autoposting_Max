from fastapi import APIRouter, Depends, HTTPException, Query

from app.infrastructure.database.session import get_session
from app.infrastructure.repositories.usage_stats_repository import UsageStatsRepository
from app.infrastructure.services.openai_costs_client import OpenAICostsClient
from app.presentation.api.dependencies import require_api_token

admin_router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    dependencies=[Depends(require_api_token)],
)


@admin_router.get("/stats")
async def get_stats() -> dict:
    async for session in get_session():
        repo = UsageStatsRepository(session)
        return await repo.get_stats()


@admin_router.get("/users")
async def get_users(limit: int = 50) -> list[dict]:
    async for session in get_session():
        repo = UsageStatsRepository(session)
        users = await repo.get_all_users(limit)
        return [
            {
                "id": u.id,
                "max_user_id": u.max_user_id,
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]


@admin_router.get("/subscriptions")
async def get_subscriptions(limit: int = 50) -> list[dict]:
    async for session in get_session():
        repo = UsageStatsRepository(session)
        subs = await repo.get_all_subscriptions(limit)
        return [
            {
                "id": s.id,
                "user_id": s.user_id,
                "tier": s.tier,
                "status": s.status,
                "channels_limit": s.channels_limit,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            }
            for s in subs
        ]


@admin_router.get("/costs")
async def get_costs(days: int = Query(default=30, ge=1, le=180)) -> dict:
    costs_client = OpenAICostsClient()
    if costs_client.configured:
        try:
            return await costs_client.get_costs(days)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"OpenAI Costs API error: {exc}") from exc

    async for session in get_session():
        repo = UsageStatsRepository(session)
        return await repo.get_openai_costs(days)


@admin_router.get("/channels/members")
async def get_channel_member_stats(days: int = Query(default=1, ge=1, le=180)) -> dict:
    async for session in get_session():
        repo = UsageStatsRepository(session)
        totals = await repo.get_member_event_counts(days)
        by_channel = await repo.get_member_event_counts_by_channel(days, limit=50)
        return {**totals, "by_channel": by_channel}
