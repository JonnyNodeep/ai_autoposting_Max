import pytest
from httpx import AsyncClient, ASGITransport

from app.main import create_app


@pytest.mark.asyncio
async def test_health_check():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code in (200, 503)
        data = response.json()
        assert "status" in data
        assert "details" in data
