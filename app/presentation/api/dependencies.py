from collections.abc import AsyncGenerator

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.infrastructure.database.session import get_session
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository


def require_api_token(x_api_token: str = Header(default="")) -> None:
    expected_token = settings.app.api_token or settings.admin.api_token
    if expected_token and x_api_token != expected_token:
        raise HTTPException(status_code=403, detail="Forbidden")


async def get_user_repo(
    session: AsyncSession = None,  # type: ignore
) -> AsyncGenerator[SQLAlchemyUserRepository, None]:
    if session is None:
        async for s in get_session():
            yield SQLAlchemyUserRepository(s)
    else:
        yield SQLAlchemyUserRepository(session)


async def get_channel_repo(
    session: AsyncSession = None,  # type: ignore
) -> AsyncGenerator[SQLAlchemyChannelRepository, None]:
    if session is None:
        async for s in get_session():
            yield SQLAlchemyChannelRepository(s)
    else:
        yield SQLAlchemyChannelRepository(session)


async def get_subscription_repo(
    session: AsyncSession = None,  # type: ignore
) -> AsyncGenerator[SQLAlchemySubscriptionRepository, None]:
    if session is None:
        async for s in get_session():
            yield SQLAlchemySubscriptionRepository(s)
    else:
        yield SQLAlchemySubscriptionRepository(session)
