"""Per-request billing user (DB users.id) for OpenAI cost logging."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from typing import AsyncIterator, Iterator

_billing_user_id: ContextVar[int | None] = ContextVar("billing_user_id", default=None)


def get_billing_user_id() -> int | None:
    return _billing_user_id.get()


def set_billing_user_id(user_id: int | None) -> Token:
    return _billing_user_id.set(int(user_id) if user_id is not None else None)


def reset_billing_user_id(token: Token) -> None:
    _billing_user_id.reset(token)


@contextmanager
def billing_user(user_id: int | None) -> Iterator[int | None]:
    """Bind DB user id for OpenAI cost attribution within the block."""
    if user_id is None:
        yield None
        return
    token = set_billing_user_id(int(user_id))
    try:
        yield int(user_id)
    finally:
        reset_billing_user_id(token)


@asynccontextmanager
async def billing_user_for_max_id(session, max_user_id: int | None) -> AsyncIterator[int | None]:
    """Resolve MAX platform id → users.id and bind billing context."""
    user_id: int | None = None
    if max_user_id:
        from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository

        user = await SQLAlchemyUserRepository(session).get_by_max_user_id(int(max_user_id))
        if user is not None and user.id is not None:
            user_id = int(user.id)
    with billing_user(user_id):
        yield user_id
