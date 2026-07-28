import pytest
from unittest.mock import AsyncMock, patch
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.mark.asyncio
async def test_health_check_without_max_probe():
    app = create_app()
    transport = ASGITransport(app=app)

    with (
        patch("app.presentation.api.health.get_session") as get_session,
        patch("app.presentation.api.health.get_redis", new_callable=AsyncMock) as get_redis,
    ):
        session = AsyncMock()
        session.execute = AsyncMock()

        async def _session_gen():
            yield session

        get_session.side_effect = lambda: _session_gen()
        redis = AsyncMock()
        redis.ping = AsyncMock(return_value=True)
        get_redis.return_value = redis

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["details"]["postgres"] == "ok"
    assert data["details"]["redis"] == "ok"
    assert "max_api" not in data["details"]
