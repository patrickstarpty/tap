"""Standalone TAP backend entrypoint for non-AI modules."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="TAP API", version="0.1.0")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "product": "tap"}

    return app


app = create_app()
