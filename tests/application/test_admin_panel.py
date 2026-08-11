"""Tests for admin panel helpers: discount, beta, grant, broadcast segments, auth."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.admin.broadcasts import resolve_segment_recipients
from app.application.admin.grant_generations import GrantGenerationsUseCase
from app.application.billing.pricing import apply_discount, calc_price
from app.domain.entities.subscription import Subscription
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.presentation.admin.auth import verify_password


def test_apply_discount_math():
    before, final = apply_discount(1000, 0)
    assert before == 1000 and final == 1000
    before, final = apply_discount(1000, 50)
    assert before == 1000 and final == 500
    before, final = apply_discount(1570, 10)
    assert before == 1570 and final == 1413
    before, final = apply_discount(100, 100)
    assert final == 1  # floor at 1 rub


def test_verify_password_empty(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings.admin, "web_password", "")
    assert verify_password("x") is False
    monkeypatch.setattr(settings.admin, "web_password", "secret")
    assert verify_password("secret") is True
    assert verify_password("wrong") is False


@pytest.mark.asyncio
async def test_grant_generations():
    sub = Subscription(
        id=1,
        user_id=5,
        status=SubscriptionStatus.ACTIVE,
        generations_quota=30,
        generations_used=10,
    )
    sub_repo = AsyncMock()
    sub_repo.get_active_by_user.return_value = sub
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    uc = GrantGenerationsUseCase(session, sub_repo)
    result = await uc.execute(5, 20, reason="bonus", actor="admin")
    assert result["generations_quota"] == 50
    assert sub_repo.update.called


@pytest.mark.asyncio
async def test_grant_generations_no_sub():
    sub_repo = AsyncMock()
    sub_repo.get_active_by_user.return_value = None
    session = AsyncMock()
    uc = GrantGenerationsUseCase(session, sub_repo)
    with pytest.raises(ValueError):
        await uc.execute(1, 10, reason="x")


@pytest.mark.asyncio
async def test_resolve_segment_all_active():
    session = AsyncMock()

    class U:
        id = 1
        max_user_id = 100

    result = MagicMock()
    result.scalars.return_value.all.return_value = [U()]
    session.execute = AsyncMock(return_value=result)

    recipients = await resolve_segment_recipients(session, "all_active")
    assert recipients == [{"user_id": 1, "max_user_id": 100}]


@pytest.mark.asyncio
async def test_resolve_segment_unknown():
    session = AsyncMock()
    with pytest.raises(ValueError):
        await resolve_segment_recipients(session, "nope")


def test_solo_price_unchanged_baseline():
    assert calc_price("solo", 3) == 1570


@pytest.mark.asyncio
async def test_beta_can_register_when_under_cap(monkeypatch):
    from app.application.admin.beta_cap import BetaCapService

    session = AsyncMock()
    svc = BetaCapService(session)
    svc._settings.get_max_users = AsyncMock(return_value=10)
    svc._users.get_by_max_user_id = AsyncMock(return_value=None)
    svc._users.count_active = AsyncMock(return_value=3)
    monkeypatch.setattr(
        "app.application.admin.beta_cap.is_admin_max_user", lambda _uid: False
    )
    assert await svc.can_register(111) is True


@pytest.mark.asyncio
async def test_beta_blocks_when_full(monkeypatch):
    from app.application.admin.beta_cap import BetaCapService

    session = AsyncMock()
    svc = BetaCapService(session)
    svc._settings.get_max_users = AsyncMock(return_value=10)
    svc._users.get_by_max_user_id = AsyncMock(return_value=None)
    svc._users.count_active = AsyncMock(return_value=10)
    monkeypatch.setattr(
        "app.application.admin.beta_cap.is_admin_max_user", lambda _uid: False
    )
    assert await svc.can_register(222) is False


@pytest.mark.asyncio
async def test_beta_admin_always_passes(monkeypatch):
    from app.application.admin.beta_cap import BetaCapService

    session = AsyncMock()
    svc = BetaCapService(session)
    monkeypatch.setattr(
        "app.application.admin.beta_cap.is_admin_max_user", lambda _uid: True
    )
    assert await svc.can_register(999) is True
