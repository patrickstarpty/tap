from httpx import ASGITransport, AsyncClient
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Route


@pytest.mark.asyncio
async def test_tap_backend_has_health_without_ai_routes() -> None:
    from tap_platform.app import create_app

    await assert_tap_surface(create_app())


async def assert_tap_surface(app: FastAPI) -> None:
    # Exhaustive allowlist: also inspect routes omitted from OpenAPI (mounts,
    # websockets, hidden handlers). Add future TAP routes deliberately here.
    assert sorted(
        (route.path, tuple(sorted(route.methods)))
        for route in app.routes
        if type(route) in (Route, APIRoute)
    ) == [
        ("/docs", ("GET", "HEAD")),
        ("/docs/oauth2-redirect", ("GET", "HEAD")),
        ("/health/live", ("GET",)),
        ("/openapi.json", ("GET", "HEAD")),
        ("/redoc", ("GET", "HEAD")),
    ]
    assert len(app.routes) == 5
    for route in app.routes:
        if isinstance(route, APIRoute):
            assert route.endpoint.__module__.startswith("tap_platform.")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://tap"
    ) as client:
        health = await client.get("/health/live")
        schema = (await client.get("/openapi.json")).json()
        ai_route = await client.post("/api/v1/projects/demo/knowledge/answers", json={})

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "product": "tap"}
    assert ai_route.status_code == 404
    assert schema["info"]["title"] == "TAP API"
    assert {path: set(operations) for path, operations in schema["paths"].items()} == {
        "/health/live": {"get"}
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exposure", ["ai-answer", "hidden", "mount", "websocket", "openapi", "handler"]
)
async def test_route_guard_rejects_added_non_tap_surface(exposure: str) -> None:
    from tap_platform.app import create_app

    app = create_app()

    async def extra_route():
        return {"unexpected": True}

    if exposure == "ai-answer":
        app.add_api_route(
            "/api/v1/projects/{project_id}/knowledge/answers",
            extra_route,
            methods=["POST"],
        )
    elif exposure == "hidden":
        app.add_api_route("/hidden", extra_route, include_in_schema=False)
    elif exposure == "mount":
        app.mount("/foreign-product", FastAPI())
    elif exposure == "websocket":
        app.add_api_websocket_route("/foreign-stream", extra_route)
    elif exposure == "openapi":
        app.openapi()["paths"]["/ai-only-contract"] = {"post": {}}
    else:
        for route in app.routes:
            if isinstance(route, APIRoute):
                route.endpoint = extra_route

    with pytest.raises(AssertionError):
        await assert_tap_surface(app)
