from fastapi import APIRouter, Depends

from app.infrastructure.database.session import get_session
from app.infrastructure.repositories.usage_stats_repository import UsageStatsRepository
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
async def get_costs(days: int = 30) -> dict:
    async for session in get_session():
        repo = UsageStatsRepository(session)
        return await repo.get_openai_costs(days)
