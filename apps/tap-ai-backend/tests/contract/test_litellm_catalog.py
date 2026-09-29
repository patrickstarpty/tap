from __future__ import annotations

import httpx
import pytest

from tap.modules.ai.adapters.litellm_catalog import LiteLLMCatalog, LiteLLMModel, ModelRoles
from tap.modules.ai.domain.models import ModelGatewayUnavailable


def model_info_response(*entries: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"data": list(entries)})


def deployment(
    model_name: str,
    *,
    mode: str | None = "chat",
    supports_vision: bool | None = None,
    supports_response_schema: bool | None = None,
    supports_function_calling: bool | None = None,
    tapper_display_name: str | None = None,
) -> dict[str, object]:
    model_info: dict[str, object] = {
        "mode": mode,
        "supports_vision": supports_vision,
        "supports_response_schema": supports_response_schema,
        "supports_function_calling": supports_function_calling,
        "tapper_display_name": tapper_display_name,
    }
    return {
        "model_name": model_name,
        "litellm_params": {"model": f"dashscope/{model_name}"},
        "model_info": model_info,
    }


def catalog_with_handler(handler, **kwargs) -> LiteLLMCatalog:
    return LiteLLMCatalog(
        base_url="https://litellm.example",
        api_key="test-key",
        client=httpx.AsyncClient(
            base_url="https://litellm.example", transport=httpx.MockTransport(handler)
        ),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_parses_chat_and_embedding_models():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/model/info"
        return model_info_response(
            deployment(
                "qwen-plus",
                mode="chat",
                supports_response_schema=True,
                tapper_display_name="Qwen Plus",
            ),
            deployment("text-embedding-v4", mode="embedding"),
        )

    catalog = catalog_with_handler(handler)
    routes = await catalog.routes()

    assert routes.get("qwen-plus") == LiteLLMModel(
        "qwen-plus", "Qwen Plus", "chat", False, True, False
    )
    assert routes.get("text-embedding-v4") is not None
    assert routes.get("text-embedding-v4").mode == "embedding"
    await catalog.aclose()


@pytest.mark.asyncio
async def test_display_name_defaults_to_model_name():
    def handler(request: httpx.Request) -> httpx.Response:
        return model_info_response(deployment("qwen-max", mode="chat"))

    catalog = catalog_with_handler(handler)
    routes = await catalog.routes()

    model = routes.get("qwen-max")
    assert model is not None
    assert model.display_name == "qwen-max"
    await catalog.aclose()


@pytest.mark.asyncio
async def test_ignores_entries_without_supported_mode():
    def handler(request: httpx.Request) -> httpx.Response:
        return model_info_response(
            deployment("no-mode", mode=None),
            deployment("image-gen", mode="image_generation"),
        )

    catalog = catalog_with_handler(handler)
    routes = await catalog.routes()

    assert routes.get("no-mode") is None
    assert routes.get("image-gen") is None
    await catalog.aclose()


@pytest.mark.asyncio
async def test_duplicate_deployments_merge_with_capability_intersection():
    def handler(request: httpx.Request) -> httpx.Response:
        return model_info_response(
            deployment("qwen-plus", mode="chat", supports_vision=True),
            deployment("qwen-plus", mode="chat", supports_vision=False),
        )

    catalog = catalog_with_handler(handler)
    routes = await catalog.routes()

    assert len(routes.models) == 1
    model = routes.get("qwen-plus")
    assert model is not None
    assert model.supports_vision is False
    await catalog.aclose()


@pytest.mark.asyncio
async def test_duplicate_deployments_with_mismatched_mode_are_dropped():
    def handler(request: httpx.Request) -> httpx.Response:
        return model_info_response(
            deployment("weird", mode="chat"),
            deployment("weird", mode="embedding"),
        )

    catalog = catalog_with_handler(handler)
    routes = await catalog.routes()

    assert routes.get("weird") is None
    await catalog.aclose()


@pytest.mark.asyncio
async def test_caches_for_ttl_then_refreshes():
    calls = 0
    current_time = 1000.0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return model_info_response(deployment("qwen-plus"))

    catalog = catalog_with_handler(handler, ttl_seconds=60.0, clock=lambda: current_time)

    await catalog.routes()
    current_time += 59
    await catalog.routes()
    assert calls == 1

    current_time += 2  # total 61s elapsed
    await catalog.routes()
    assert calls == 2
    await catalog.aclose()


@pytest.mark.asyncio
async def test_refresh_failure_keeps_last_good_routes():
    calls = 0
    current_time = 1000.0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return model_info_response(deployment("qwen-plus"))
        return httpx.Response(500, json={"error": "boom"})

    catalog = catalog_with_handler(handler, ttl_seconds=60.0, clock=lambda: current_time)

    first = await catalog.routes()
    assert first.get("qwen-plus") is not None

    current_time += 61
    second = await catalog.routes()

    assert second.get("qwen-plus") is not None
    assert calls == 2
    await catalog.aclose()


@pytest.mark.asyncio
async def test_malformed_refresh_keeps_last_good_routes():
    calls = 0
    current_time = 1000.0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return model_info_response(deployment("qwen-plus"))
        return httpx.Response(200, json={"data": "not-a-list"})

    catalog = catalog_with_handler(handler, ttl_seconds=60.0, clock=lambda: current_time)

    first = await catalog.routes()
    assert first.get("qwen-plus") is not None

    current_time += 61
    second = await catalog.routes()

    assert second.get("qwen-plus") is not None
    assert calls == 2
    await catalog.aclose()


@pytest.mark.asyncio
async def test_never_loaded_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    catalog = catalog_with_handler(handler)

    with pytest.raises(ModelGatewayUnavailable):
        await catalog.routes()
    await catalog.aclose()


@pytest.mark.asyncio
async def test_sends_bearer_key():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return model_info_response(deployment("qwen-plus"))

    catalog = catalog_with_handler(handler)
    await catalog.routes()

    assert captured["authorization"] == "Bearer test-key"
    await catalog.aclose()


def test_roles_report_missing_and_wrong_mode():
    from tap.modules.ai.adapters.litellm_catalog import LiteLLMRoutes

    routes = LiteLLMRoutes(
        models={
            "qwen-plus": LiteLLMModel("qwen-plus", "Qwen Plus", "chat", False, True, False),
        }
    )
    roles = ModelRoles(
        default_chat_model="missing-model",
        embedding_model="qwen-plus",
        vision_model=None,
    )

    problems = roles.problems(routes)

    assert any("TAPPER_DEFAULT_CHAT_MODEL" in problem for problem in problems)
    assert any("TAPPER_EMBEDDING_MODEL" in problem for problem in problems)


def test_vision_role_requires_supports_vision():
    from tap.modules.ai.adapters.litellm_catalog import LiteLLMRoutes

    routes = LiteLLMRoutes(
        models={
            "qwen-plus": LiteLLMModel("qwen-plus", "Qwen Plus", "chat", False, True, False),
        }
    )
    roles = ModelRoles(
        default_chat_model="qwen-plus",
        embedding_model="qwen-plus",
        vision_model="qwen-plus",
    )

    problems = roles.problems(routes)

    assert any("TAPPER_VISION_MODEL" in problem for problem in problems)
    assert "qwen-plus does not support vision" in problems[0] or any(
        "does not support vision" in problem for problem in problems
    )
