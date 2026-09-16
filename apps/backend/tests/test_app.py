from httpx import ASGITransport, AsyncClient
import pytest


@pytest.mark.asyncio
async def test_tap_backend_has_health_without_ai_routes() -> None:
    from tap_platform.app import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://tap"
    ) as client:
        health = await client.get("/health/live")
        ai_route = await client.get("/api/v1/projects/demo/answers")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "product": "tap"}
    assert ai_route.status_code == 404
