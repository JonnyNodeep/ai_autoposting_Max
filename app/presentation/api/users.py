from fastapi import APIRouter, Depends, HTTPException

from app.infrastructure.database.session import get_session as get_db_session
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.presentation.api.dependencies import require_api_token
from app.presentation.schemas.user import UserResponse
from app.presentation.schemas.subscription import SubscriptionResponse

users_router = APIRouter(
    prefix="/api/users",
    tags=["Users"],
    dependencies=[Depends(require_api_token)],
)


@users_router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: int) -> UserResponse:
    async for session in get_db_session():
        repo = SQLAlchemyUserRepository(session)
        user = await repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return UserResponse(
            id=user.id,
            max_user_id=user.max_user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            is_active=user.is_active,
        )


@users_router.get("/{user_id}/subscription", response_model=SubscriptionResponse)
async def get_user_subscription(user_id: int) -> SubscriptionResponse:
    async for session in get_db_session():
        repo = SQLAlchemySubscriptionRepository(session)
        sub = await repo.get_active_by_user(user_id)
        if not sub:
            raise HTTPException(status_code=404, detail="No active subscription")
        return SubscriptionResponse(
            id=sub.id,
            tier=sub.tier.value,
            status=sub.status.value,
            channels_limit=sub.channels_limit,
            expires_at=sub.expires_at.isoformat(),
        )
