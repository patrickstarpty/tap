"""Strict direct Bailian embedding research transport contracts."""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

import httpx
import pytest

from tap.operations.milvus.bailian import (
    BailianEmbeddingAdapter,
    BailianEmbeddingConfig,
    BailianEmbeddingUnavailable,
)
from tap.operations.milvus.embeddings import EMBEDDING_ALIAS, EMBEDDING_DIMENSION


def config(**changes: object) -> BailianEmbeddingConfig:
    values: dict[str, object] = {
        "api_base": ("https://ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
        "api_key": "PRIVATE_PROVIDER_KEY",
        "deadline_seconds": 1.0,
    }
    values.update(changes)
    return BailianEmbeddingConfig(**values)  # type: ignore[arg-type]


def response_body(*, model: object = "text-embedding-v4", dimension: int = 1536) -> object:
    return {
        "id": "request-17",
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": 0,
                "embedding": [0.001] * dimension,
            }
        ],
        "model": model,
        "usage": {"prompt_tokens": 4, "total_tokens": 4},
    }


@pytest.mark.asyncio
async def test_direct_request_uses_raw_model_exact_dimensions_and_calculated_cny() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "request-17"},
            json=response_body(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BailianEmbeddingAdapter(config(), client=client).embed("sanitized text")

    assert requests[0].url.path == "/compatible-mode/v1/embeddings"
    assert requests[0].headers["authorization"] == "Bearer PRIVATE_PROVIDER_KEY"
    assert json.loads(requests[0].content) == {
        "model": "text-embedding-v4",
        "input": "sanitized text",
        "dimensions": 1536,
        "encoding_format": "float",
    }
    assert result.model_id == EMBEDDING_ALIAS
    assert result.provider_model_id == "text-embedding-v4"
    assert result.provider_request_id == "request-17"
    assert len(result.vector) == EMBEDDING_DIMENSION
    assert result.usage is not None
    assert result.usage.response_cost_usd is None
    assert result.usage.calculated_cost_cny == Decimal("0.000002")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        response_body(model="other-model"),
        response_body(dimension=1024),
        {**response_body(), "usage": {"prompt_tokens": True, "total_tokens": 4}},
        {**response_body(), "usage": {"prompt_tokens": 5, "total_tokens": 4}},
        {**response_body(), "usage": {"prompt_tokens": 1, "total_tokens": 1_000}},
        {**response_body(), "id": "different-request"},
        {**response_body(), "id": "unsafe request id"},
        {**response_body(), "extra": "widened"},
    ],
    ids=(
        "model",
        "dimension",
        "usage-type",
        "usage-order",
        "usage-cost-drift",
        "request-id-mismatch",
        "request-id-shape",
        "widened",
    ),
)
async def test_direct_response_rejects_model_dimension_usage_and_shape_drift(body: object) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-request-id": "request-17"}, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BailianEmbeddingUnavailable):
            await BailianEmbeddingAdapter(config(), client=client).embed("sanitized text")


@pytest.mark.asyncio
async def test_direct_failure_and_repr_never_expose_endpoint_key_or_text() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            text=(
                "PRIVATE_PROVIDER_KEY sanitized text "
                "ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com"
            ),
        )

    direct_config = config()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BailianEmbeddingUnavailable) as caught:
            await BailianEmbeddingAdapter(direct_config, client=client).embed("sanitized text")

    exposed = repr(direct_config) + str(caught.value)
    assert "PRIVATE_PROVIDER_KEY" not in exposed
    assert "sanitized text" not in exposed
    assert "cn-beijing.maas.aliyuncs.com" not in exposed


@pytest.mark.asyncio
async def test_direct_httpx_info_log_redacts_the_workspace_endpoint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        logging.getLogger("httpcore.connection").debug(
            "connect_tcp.started host=%r server_hostname=%r",
            "ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com",
            "ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com",
        )
        return httpx.Response(
            200,
            headers={"x-request-id": "request-17"},
            json=response_body(),
        )

    caplog.set_level(logging.DEBUG)
    direct_config = config()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await BailianEmbeddingAdapter(direct_config, client=client).embed("sanitized text")

    assert "cn-beijing.maas.aliyuncs.com" not in caplog.text
    assert "PRIVATE_PROVIDER_KEY" not in caplog.text
    assert "sanitized text" not in caplog.text


@pytest.mark.asyncio
async def test_direct_deadline_and_cancellation_preserve_bounded_semantics() -> None:
    started = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(60)
        return httpx.Response(200, json=response_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BailianEmbeddingUnavailable, match="deadline"):
            await BailianEmbeddingAdapter(config(deadline_seconds=0.001), client=client).embed(
                "sanitized text"
            )

        task = asyncio.create_task(
            BailianEmbeddingAdapter(config(deadline_seconds=1), client=client).embed(
                "sanitized text"
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
