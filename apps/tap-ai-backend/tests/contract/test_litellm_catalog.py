from __future__ import annotations

import asyncio

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


@pytest.mark.asyncio
async def test_keeps_deployment_id_to_upstream_model_map():
    def handler(request: httpx.Request) -> httpx.Response:
        plus = deployment("qwen-plus", supports_response_schema=True)
        plus["model_info"]["id"] = "deployment-plus"
        plus_backup = deployment("qwen-plus", supports_response_schema=True)
        plus_backup["model_info"]["id"] = "deployment-plus-backup"
        plus_backup["litellm_params"] = {"model": "openai/gpt-4o-mini"}
        no_id = deployment("text-embedding-v4", mode="embedding")
        return model_info_response(plus, plus_backup, no_id)

    catalog = catalog_with_handler(handler)
    routes = await catalog.routes()

    assert dict(routes.deployments) == {
        "deployment-plus": "dashscope/qwen-plus",
        "deployment-plus-backup": "openai/gpt-4o-mini",
    }
    assert routes.upstream_model("deployment-plus") == "dashscope/qwen-plus"
    assert routes.upstream_model("unknown") is None
    await catalog.aclose()


@pytest.mark.asyncio
async def test_fresh_routes_fails_after_refresh_failure_while_routes_keeps_cache():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return model_info_response(deployment("qwen-plus"))
        return httpx.Response(500, json={"error": "boom"})

    catalog = catalog_with_handler(handler)

    assert (await catalog.fresh_routes()).get("qwen-plus") is not None
    with pytest.raises(ModelGatewayUnavailable):
        await catalog.fresh_routes()
    assert (await catalog.routes()).get("qwen-plus") is not None
    assert catalog.cached_routes() is not None
    await catalog.aclose()


@pytest.mark.asyncio
async def test_fresh_routes_success_updates_cache():
    names = iter(("qwen-plus", "qwen-max"))

    def handler(request: httpx.Request) -> httpx.Response:
        return model_info_response(deployment(next(names)))

    catalog = catalog_with_handler(handler)
    await catalog.routes()
    await catalog.fresh_routes()

    cached = catalog.cached_routes()
    assert cached is not None and cached.get("qwen-max") is not None
    assert (await catalog.routes()).get("qwen-max") is not None
    await catalog.aclose()


def test_catalog_has_explicit_default_request_timeout():
    catalog = LiteLLMCatalog(base_url="https://litellm.example", api_key="test-key")

    assert catalog.timeout_seconds == 5.0
    with pytest.raises(ValueError):
        LiteLLMCatalog(base_url="https://litellm.example", api_key="k", timeout_seconds=0)


@pytest.mark.asyncio
async def test_catalog_request_carries_explicit_timeout():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.extensions["timeout"])
        return model_info_response(deployment("qwen-plus"))

    catalog = catalog_with_handler(handler, timeout_seconds=2.5)
    await catalog.routes()

    assert captured["read"] == 2.5
    await catalog.aclose()


@pytest.mark.asyncio
async def test_entries_violating_contract_limits_are_skipped_and_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return model_info_response(
            deployment("qwen-plus", tapper_display_name="Qwen Plus"),
            deployment("qwen-long", tapper_display_name="x" * 129),
            deployment("m" * 129),
            deployment("Bad_Name"),
            deployment("text-embedding-v4", mode="embedding"),
        )

    catalog = catalog_with_handler(handler)
    routes = await catalog.routes()

    assert set(routes.models) == {"qwen-plus", "text-embedding-v4"}
    assert routes.skipped == {
        "qwen-long": "display name exceeds 128 characters",
        "m" * 129: "name exceeds 128 characters",
        "Bad_Name": "name must be lowercase letters, digits, dots or hyphens",
    }
    await catalog.aclose()


@pytest.mark.asyncio
async def test_catalog_keeps_first_32_models_in_litellm_order():
    def handler(request: httpx.Request) -> httpx.Response:
        return model_info_response(
            *(deployment(f"model-{index:02d}") for index in range(33)),
            deployment("image-gen", mode="image_generation"),
        )

    catalog = catalog_with_handler(handler)
    routes = await catalog.routes()

    assert list(routes.models) == [f"model-{index:02d}" for index in range(32)]
    assert routes.skipped == {"model-32": "catalog limit 32 reached"}
    await catalog.aclose()


def test_default_chat_role_requires_response_schema():
    from tap.modules.ai.adapters.litellm_catalog import LiteLLMRoutes

    routes = LiteLLMRoutes(
        models={
            "qwen-flash": LiteLLMModel("qwen-flash", "Qwen Flash", "chat", False, False, False),
            "text-embedding-v4": LiteLLMModel(
                "text-embedding-v4", "text-embedding-v4", "embedding", False, False, False
            ),
        }
    )

    problems = ModelRoles("qwen-flash", "text-embedding-v4", None).problems(routes)

    assert problems == ("TAPPER_DEFAULT_CHAT_MODEL=qwen-flash does not support response schema",)


def test_vision_role_requires_response_schema():
    from tap.modules.ai.adapters.litellm_catalog import LiteLLMRoutes

    routes = LiteLLMRoutes(
        models={
            "qwen-plus": LiteLLMModel("qwen-plus", "Qwen Plus", "chat", False, True, False),
            "vl": LiteLLMModel("vl", "VL", "chat", True, False, False),
            "text-embedding-v4": LiteLLMModel(
                "text-embedding-v4", "text-embedding-v4", "embedding", False, False, False
            ),
        }
    )

    problems = ModelRoles("qwen-plus", "text-embedding-v4", "vl").problems(routes)

    assert problems == ("TAPPER_VISION_MODEL=vl does not support response schema",)


@pytest.mark.asyncio
async def test_routes_is_not_blocked_by_in_flight_fresh_routes():
    now = [0.0]
    release_health = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 2:
            # The health poll's fetch stalls until the test releases it.
            await release_health.wait()
        return model_info_response(deployment("qwen-plus", supports_response_schema=True))

    catalog = catalog_with_handler(handler, ttl_seconds=10, clock=lambda: now[0])
    await catalog.routes()
    now[0] = 11.0
    health = asyncio.create_task(catalog.fresh_routes())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    routes = await asyncio.wait_for(catalog.routes(), timeout=1)

    assert routes.get("qwen-plus") is not None
    assert not health.done()
    release_health.set()
    assert (await health).get("qwen-plus") is not None
    await catalog.aclose()


@pytest.mark.asyncio
async def test_slow_fresh_routes_does_not_replace_a_newer_cache():
    now = [0.0]
    release_health = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await release_health.wait()
            return model_info_response(deployment("old-model", supports_response_schema=True))
        return model_info_response(deployment("new-model", supports_response_schema=True))

    catalog = catalog_with_handler(handler, ttl_seconds=10, clock=lambda: now[0])
    health = asyncio.create_task(catalog.fresh_routes())
    await asyncio.sleep(0)
    now[0] = 1.0
    await asyncio.wait_for(catalog.routes(), timeout=1)
    release_health.set()
    await health

    assert catalog.cached_routes().get("new-model") is not None
    await catalog.aclose()


@pytest.mark.asyncio
async def test_equal_timestamp_fresh_routes_does_not_replace_a_newer_cache():
    release_health = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await release_health.wait()
            return model_info_response(deployment("old-model", supports_response_schema=True))
        return model_info_response(deployment("new-model", supports_response_schema=True))

    # A coarse clock: the health fetch start and the newer refresh share one timestamp.
    catalog = catalog_with_handler(handler, ttl_seconds=10, clock=lambda: 5.0)
    health = asyncio.create_task(catalog.fresh_routes())
    await asyncio.sleep(0)
    await asyncio.wait_for(catalog.routes(), timeout=1)
    release_health.set()
    await health

    assert catalog.cached_routes().get("new-model") is not None
    await catalog.aclose()


@pytest.mark.parametrize("api_key", ["", "k" * 4097])
def test_catalog_rejects_missing_or_oversized_credential(api_key):
    with pytest.raises(ValueError, match="bounded credential"):
        LiteLLMCatalog(base_url="https://litellm.example", api_key=api_key)


@pytest.mark.parametrize("ttl_seconds", [0, -1.0])
def test_catalog_rejects_non_positive_ttl(ttl_seconds):
    with pytest.raises(ValueError, match="positive ttl"):
        LiteLLMCatalog(
            base_url="https://litellm.example", api_key="test-key", ttl_seconds=ttl_seconds
        )
