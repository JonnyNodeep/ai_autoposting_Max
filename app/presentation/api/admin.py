from fastapi import APIRouter, Header, HTTPException

from app.infrastructure.database.session import get_session
from app.infrastructure.repositories.usage_stats_repository import UsageStatsRepository
from app.config import settings

admin_router = APIRouter(prefix="/api/admin", tags=["Admin"])


def _check_admin(api_token: str = Header(None)) -> None:
    if not settings.admin.api_token or api_token != settings.admin.api_token:
        raise HTTPException(status_code=403, detail="Forbidden")


@admin_router.get("/stats")
async def get_stats(api_token: str = Header(None)) -> dict:
    _check_admin(api_token)
    async for session in get_session():
        repo = UsageStatsRepository(session)
        return await repo.get_stats()


@admin_router.get("/users")
async def get_users(api_token: str = Header(None), limit: int = 50) -> list[dict]:
    _check_admin(api_token)
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
async def get_subscriptions(api_token: str = Header(None), limit: int = 50) -> list[dict]:
    _check_admin(api_token)
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
async def get_costs(days: int = 30, api_token: str = Header(None)) -> dict:
    _check_admin(api_token)
    async for session in get_session():
        repo = UsageStatsRepository(session)
        return await repo.get_openai_costs(days)
