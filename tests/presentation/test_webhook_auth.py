import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.config import settings
from app.presentation.api.webhook import max_webhook


def _request(secret: str = "") -> MagicMock:
    request = MagicMock()
    headers = MagicMock()
    headers.get = MagicMock(
        side_effect=lambda key, default="": secret if key == "X-Max-Bot-Api-Secret" else default
    )
    request.headers = headers
    return request


@pytest.mark.asyncio
async def test_max_webhook_rejects_when_secret_not_configured():
    old = settings.app.webhook_secret
    settings.app.webhook_secret = ""
    try:
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock(return_value=["ok"])
        result = await max_webhook({}, _request("anything"), dispatcher=dispatcher)
        assert result == {"processed": 1}
    finally:
        settings.app.webhook_secret = old


@pytest.mark.asyncio
async def test_max_webhook_rejects_invalid_secret():
    old = settings.app.webhook_secret
    settings.app.webhook_secret = "correct-secret"
    try:
        with pytest.raises(HTTPException) as exc:
            await max_webhook({}, _request("wrong-secret"), dispatcher=AsyncMock())
        assert exc.value.status_code == 403
    finally:
        settings.app.webhook_secret = old


@pytest.mark.asyncio
async def test_max_webhook_accepts_valid_secret():
    old = settings.app.webhook_secret
    settings.app.webhook_secret = "correct-secret"
    dispatcher = AsyncMock()
    dispatcher.dispatch = AsyncMock(return_value=["ok"])
    try:
        with patch("app.presentation.api.webhook.get_redis", new_callable=AsyncMock) as get_redis:
            redis = AsyncMock()
            redis.set = AsyncMock(return_value=True)
            get_redis.return_value = redis
            result = await max_webhook(
                {"update_type": "message_created", "message": {"message_id": 1}},
                _request("correct-secret"),
                dispatcher=dispatcher,
            )
        assert result == {"processed": 1}
        dispatcher.dispatch.assert_awaited_once()
    finally:
        settings.app.webhook_secret = old
