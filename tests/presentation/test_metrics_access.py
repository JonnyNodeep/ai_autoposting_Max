import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import create_app


@pytest.mark.asyncio
async def test_metrics_requires_token():
    old_app = settings.app.api_token
    old_admin = settings.admin.api_token
    settings.app.api_token = "metrics-token"
    settings.admin.api_token = ""
    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get("/metrics")
            assert denied.status_code == 403

            ok = await client.get("/metrics", headers={"X-API-Token": "metrics-token"})
            assert ok.status_code == 200
            data = ok.json()
            assert "uptime_seconds" in data
            assert "postgres_connected" in data
            assert "redis_connected" in data
    finally:
        settings.app.api_token = old_app
        settings.admin.api_token = old_admin
