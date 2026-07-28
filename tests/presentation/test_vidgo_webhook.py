import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.presentation.api.webhook import vidgo_webhook


def _request(token: str | None = None, body: dict | None = None) -> MagicMock:
    request = MagicMock()
    params = {}
    if token is not None:
        params["token"] = token
    request.query_params = params
    request.json = AsyncMock(return_value=body or {})
    return request


@pytest.mark.asyncio
async def test_vidgo_webhook_rejects_invalid_token():
    old = settings.vidgo.webhook_token
    settings.vidgo.webhook_token = "expected-token"
    try:
        with pytest.raises(HTTPException) as exc:
            await vidgo_webhook(_request(token="wrong", body={"data": {"task_id": "t1", "status": "finished"}}))
        assert exc.value.status_code == 403
    finally:
        settings.vidgo.webhook_token = old


@pytest.mark.asyncio
async def test_vidgo_webhook_stores_verified_result():
    old = settings.vidgo.webhook_token
    settings.vidgo.webhook_token = "expected-token"
    verified = {"task_id": "t1", "status": "finished", "files": [{"file_url": "https://x/v.mp4"}]}
    try:
        with (
            patch("app.infrastructure.services.vidgo_tasks.try_dedup", new_callable=AsyncMock, return_value=True),
            patch("app.infrastructure.services.vidgo_tasks.get_task_meta", new_callable=AsyncMock, return_value={"kind": "ai_test"}),
            patch("app.infrastructure.services.vidgo_tasks.store_result", new_callable=AsyncMock) as store_result,
            patch("app.infrastructure.services.vidgo_client.VidGoClient") as client_cls,
        ):
            client = AsyncMock()
            client.get_task_status = AsyncMock(return_value=verified)
            client.close = AsyncMock()
            client_cls.return_value = client

            result = await vidgo_webhook(
                _request(
                    token="expected-token",
                    body={"data": {"task_id": "t1", "status": "finished"}},
                )
            )

        assert result == {"received": True}
        store_result.assert_awaited_once_with("t1", verified)
    finally:
        settings.vidgo.webhook_token = old


@pytest.mark.asyncio
async def test_vidgo_webhook_dedupes():
    old = settings.vidgo.webhook_token
    settings.vidgo.webhook_token = "expected-token"
    try:
        with patch(
            "app.infrastructure.services.vidgo_tasks.try_dedup",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await vidgo_webhook(
                _request(
                    token="expected-token",
                    body={"data": {"task_id": "t1", "status": "finished"}},
                )
            )
        assert result == {"received": True, "deduped": True}
    finally:
        settings.vidgo.webhook_token = old
