"""Strict LiteLLM model-route, size, deadline, and output contracts."""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal

import httpx
import pytest

from tap.modules.knowledge.adapters.litellm import (
    LiteLLMAdapter,
    LiteLLMConfig,
    ModelUnavailable,
)
from tap.modules.knowledge.domain.models import (
    CodeAnchor,
    ContentRole,
    Evidence,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)

SOURCE_HASH = "sha256:" + "a" * 64
CHUNK_HASH = "sha256:" + "b" * 64


def config(**changes: object) -> LiteLLMConfig:
    values: dict[str, object] = {
        "base_url": "https://litellm.example",
        "api_key": "not-a-real-key",
        "embedding_model_id": "tap-embed-fixed-v1",
        "answer_model_id": "tap-answer-fixed-v1",
        "answer_profile_id": "grounded-answer-v2",
        "embedding_dimension": 2,
        "allowed_embedding_model_labels": frozenset(
            {
                "tap-embed-fixed-v1",
                "provider-embed-v1",
                "gateway-embed-v1",
            }
        ),
        "allowed_answer_model_labels": frozenset(
            {
                "tap-answer-fixed-v1",
                "provider-answer-v1",
                "gateway-answer-v1",
            }
        ),
        "allowed_retrieval_profile_ids": frozenset({"quick-hybrid-v1", "deep-hybrid-v1"}),
        "deadline_seconds": 1,
        "max_retries": 0,
        "max_connections": 1,
        "max_request_bytes": 16_384,
        "max_response_bytes": 16_384,
        "max_evidence_count": 4,
        "max_evidence_content_chars": 2_000,
        "max_total_evidence_chars": 4_000,
        "max_output_tokens": 512,
        "max_answer_chars": 2_000,
        "max_claims": 4,
        "max_claim_chars": 500,
        "max_labels_per_claim": 4,
    }
    values.update(changes)
    return LiteLLMConfig(**values)  # type: ignore[arg-type]


def evidence(*, label: str = "S1", content: str = "Current policy is required.") -> Evidence:
    return Evidence(
        family=SourceFamily.CODE,
        chunk_id="h_" + "1" * 64,
        logical_chunk_id="h_" + "2" * 64,
        title="authorize",
        content=content,
        source=SourceRevisionRef(
            source_id="repo:checkout:payment.py",
            source_type="code",
            revision_kind=RevisionKind.GIT_COMMIT,
            revision="a" * 40,
            source_content_hash=SOURCE_HASH,
            anchor=CodeAnchor(
                repo="checkout",
                path="payment.py",
                symbol="authorize",
                line_start=10,
                line_end=25,
            ),
        ),
        chunk_content_hash=CHUNK_HASH,
        content_role=ContentRole.SOURCE,
        citation_id="citation-1",
        evidence_label=label,
        index_revision=IndexRevision(
            physical_index="kb-code-v1-20260824",
            schema_version="search-schema-v1",
            corpus_version="corpus-17",
        ),
        embedding_model_version="tap-embed-fixed-v1",
        acl_decision_id="decision-17",
        score=1 / 61,
    )


def embedding_response(
    *,
    vector: list[object] | None = None,
    model: str = "provider-embed-v1",
) -> dict[str, object]:
    return {
        "id": "embedding-17",
        "model": model,
        "data": [{"embedding": vector if vector is not None else [0.25, 0.5]}],
    }


def embedding_response_with_usage(
    *,
    usage: object | None = None,
) -> dict[str, object]:
    body = embedding_response()
    body["usage"] = {"prompt_tokens": 4, "total_tokens": 4} if usage is None else usage
    return body


def answer_response(
    *,
    answer: object = "Grounded answer.",
    claims: object | None = None,
    model: str = "provider-answer-v1",
) -> dict[str, object]:
    generated = {
        "answer": answer,
        "claims": (
            claims
            if claims is not None
            else [{"text": "Grounded answer.", "evidenceLabels": ["S1"]}]
        ),
    }
    return {
        "id": "completion-17",
        "model": model,
        "choices": [{"message": {"content": json.dumps(generated)}}],
    }


def malformed_evidence(field: str) -> Evidence:
    """Simulate a runtime annotation bypass after domain construction."""
    item = evidence()
    target = item.source if field == "revision" else item
    object.__setattr__(target, field, 17)
    return item


def runtime_mutated_evidence(*, field: str, value: object, source_field: bool) -> Evidence:
    """Bypass frozen construction to exercise the immediate provider-egress boundary."""
    item = evidence()
    target = item.source if source_field else item
    object.__setattr__(target, field, value)
    return item


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        embedding_response(vector=[0.25]),
        embedding_response(vector=[0.25, float("nan")]),
        embedding_response(vector=[0.25, True]),
        embedding_response(model="unknown-provider-model"),
    ],
    ids=("dimension", "non-finite", "boolean", "unknown-model"),
)
async def test_embedding_rejects_wrong_vector_space_or_unknown_model(
    body: dict[str, object],
) -> None:
    """Float coercion/unchecked labels would let an incompatible vector cross the port."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        # The wire can contain JavaScript constants even though httpx's `json=`
        # convenience encoder correctly refuses to produce them.
        return httpx.Response(
            200,
            content=json.dumps(body, allow_nan=True).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(), client=client)
        with pytest.raises(ModelUnavailable):
            await adapter.embed("authorization [REDACTED]")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body_model", "gateway_model"),
    [
        ("provider-answer-v1", "gateway-embed-v1"),
        ("provider-embed-v1", "gateway-answer-v1"),
        ("provider-embed-v1", "unknown-gateway-model"),
    ],
    ids=("answer-body-on-embedding", "answer-gateway-on-embedding", "unknown-gateway"),
)
async def test_embedding_rejects_cross_route_body_and_gateway_model_labels(
    body_model: str,
    gateway_model: str,
) -> None:
    """A same-dimension vector from the wrong route must not be relabeled as configured."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-litellm-model-id": gateway_model},
            json=embedding_response(model=body_model),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(), client=client)
        with pytest.raises(ModelUnavailable) as caught:
            await adapter.embed("authorization [REDACTED]")

    assert "not-a-real-key" not in str(caught.value)
    assert body_model not in str(caught.value)
    assert gateway_model not in str(caught.value)


@pytest.mark.asyncio
async def test_embedding_parses_standard_usage_and_exact_decimal_response_cost() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "x-request-id": "request-17",
                "x-litellm-response-cost": "0.000001",
            },
            json=embedding_response_with_usage(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await LiteLLMAdapter(config(), client=client).embed("authorization [REDACTED]")

    assert result.usage is not None
    assert result.usage.input_tokens == 4
    assert result.usage.total_tokens == 4
    assert result.usage.response_cost_usd == Decimal("0.000001")
    assert json.loads(requests[0].content) == {
        "model": "tap-embed-fixed-v1",
        "input": "authorization [REDACTED]",
        "dimensions": 2,
    }


@pytest.mark.asyncio
async def test_research_embedding_requests_1536_and_rejects_provider_default_1024() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0.000001"},
            json=embedding_response_with_usage(usage={"prompt_tokens": 4, "total_tokens": 4})
            | {"data": [{"embedding": [0.001] * 1024}]},
        )

    research_config = config(embedding_dimension=1536)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelUnavailable):
            await LiteLLMAdapter(research_config, client=client).embed("authorization [REDACTED]")

    assert json.loads(requests[0].content)["dimensions"] == 1536


@pytest.mark.asyncio
async def test_missing_cost_header_remains_explicit_none_for_research_to_reject() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=embedding_response_with_usage())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await LiteLLMAdapter(config(), client=client).embed("authorization [REDACTED]")

    assert result.usage is not None
    assert result.usage.response_cost_usd is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"prompt_tokens": True, "total_tokens": 4},
        {"prompt_tokens": -1, "total_tokens": 4},
        {"prompt_tokens": 4.0, "total_tokens": 4},
        {"prompt_tokens": 5, "total_tokens": 4},
        {"prompt_tokens": 1_000_001, "total_tokens": 1_000_001},
        {"prompt_tokens": 4, "total_tokens": float("nan")},
        {"prompt_tokens": 4, "total_tokens": float("inf")},
    ],
    ids=(
        "missing",
        "boolean",
        "negative",
        "float",
        "total-less-than-prompt",
        "overflow",
        "nan",
        "infinity",
    ),
)
async def test_embedding_usage_rejects_missing_non_integer_non_finite_and_overflow(
    usage: object | None,
) -> None:
    body = embedding_response()
    if usage is not None:
        body["usage"] = usage

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0.000001"},
            content=json.dumps(body, allow_nan=True).encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelUnavailable):
            await LiteLLMAdapter(config(), client=client).embed("authorization [REDACTED]")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_cost",
    [
        "-0.1",
        "NaN",
        "Infinity",
        "101",
        "1e-6",
        "+0.1",
        " 0.1",
        "0.1234567890123456789",
        "9" * 257,
    ],
    ids=(
        "negative",
        "nan",
        "infinity",
        "bound",
        "exponent",
        "plus",
        "whitespace",
        "precision",
        "header-overflow",
    ),
)
async def test_embedding_cost_header_rejects_noncanonical_nonfinite_and_overflow(
    raw_cost: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": raw_cost},
            json=embedding_response_with_usage(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelUnavailable):
            await LiteLLMAdapter(config(), client=client).embed("authorization [REDACTED]")


@pytest.mark.asyncio
async def test_embedding_response_duplicate_usage_keys_fail_closed() -> None:
    body = (
        '{"id":"embedding-17","model":"provider-embed-v1",'
        '"data":[{"embedding":[0.25,0.5]}],'
        '"usage":{"prompt_tokens":4,"prompt_tokens":5,"total_tokens":5}}'
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0.000001"},
            content=body.encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelUnavailable):
            await LiteLLMAdapter(config(), client=client).embed("authorization [REDACTED]")


@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:4000", "http://localhost:4000", "https://litellm.example"],
)
def test_litellm_config_accepts_https_or_exact_loopback_http(base_url: str) -> None:
    assert config(base_url=base_url).base_url == base_url


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1",
        "http://127.0.0.1:4000/path",
        "http://localhost:4000?query=1",
        "http://user@localhost:4000",
        "http://127.0.0.2:4000",
        "http://0.0.0.0:4000",
        "http://[::1]:4000",
        "https://user:secret@litellm.example",
    ],
)
def test_litellm_config_rejects_nonexact_loopback_http(base_url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS or exact loopback HTTP"):
        config(base_url=base_url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body_model", "gateway_model"),
    [
        ("provider-embed-v1", "gateway-answer-v1"),
        ("provider-answer-v1", "gateway-embed-v1"),
        ("provider-answer-v1", "unknown-gateway-model"),
    ],
    ids=("embedding-body-on-answer", "embedding-gateway-on-answer", "unknown-gateway"),
)
async def test_answer_rejects_cross_route_body_and_gateway_model_labels(
    body_model: str,
    gateway_model: str,
) -> None:
    """Answer parsing must bind both provider and gateway identities to its route."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-litellm-model-id": gateway_model},
            json=answer_response(model=body_model),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(), client=client)
        with pytest.raises(ModelUnavailable) as caught:
            await adapter.answer(
                "authorization [REDACTED]",
                (evidence(),),
                "quick-hybrid-v1",
            )

    assert "not-a-real-key" not in str(caught.value)
    assert body_model not in str(caught.value)
    assert gateway_model not in str(caught.value)


def test_embedding_and_answer_route_model_allowlists_must_be_disjoint() -> None:
    with pytest.raises(ValueError, match="disjoint"):
        LiteLLMConfig(
            base_url="https://litellm.example",
            api_key="not-a-real-key",
            embedding_model_id="tap-embed-fixed-v1",
            answer_model_id="tap-answer-fixed-v1",
            answer_profile_id="grounded-answer-v2",
            embedding_dimension=2,
            allowed_embedding_model_labels=frozenset(
                {"tap-embed-fixed-v1", "shared-provider-model"}
            ),
            allowed_answer_model_labels=frozenset({"tap-answer-fixed-v1", "shared-provider-model"}),
            allowed_retrieval_profile_ids=frozenset({"quick-hybrid-v1"}),
        )


@pytest.mark.asyncio
async def test_fixed_profiles_output_tokens_and_retry_identity_cannot_be_caller_selected() -> None:
    """Caller profile/model widening or a new retry identity must fail this request contract."""
    attempts = 0
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        requests.append(request)
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            headers={
                "x-request-id": "provider-request-17",
                "x-litellm-call-id": "gateway-call-17",
                "x-litellm-model-id": "gateway-answer-v1",
                "x-untrusted-header": "must-not-cross",
            },
            json=answer_response(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(max_retries=1), client=client)
        result = await adapter.answer(
            "authorization [REDACTED]",
            (evidence(),),
            "quick-hybrid-v1",
        )

    payloads = [json.loads(request.content) for request in requests]
    assert attempts == 2
    assert all(payload["model"] == "tap-answer-fixed-v1" for payload in payloads)
    assert all(payload["max_tokens"] == 512 for payload in payloads)
    assert all("dimensions" not in payload for payload in payloads)
    assert all(
        payload["metadata"] == {"tapAnswerProfile": "grounded-answer-v2"} for payload in payloads
    )
    assert all(
        "every claim text must be copied exactly as one complete paragraph in answer"
        in payload["messages"][0]["content"]
        for payload in payloads
    )
    assert requests[0].headers["x-tap-request-id"] == requests[1].headers["x-tap-request-id"]
    assert result.provider_request_id == "provider-request-17"
    assert result.gateway_call_id == "gateway-call-17"
    assert result.gateway_model_id == "gateway-answer-v1"
    assert result.provider_model_id == "provider-answer-v1"

    calls_before_unknown = attempts
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(), client=client)
        with pytest.raises(ModelUnavailable):
            await adapter.answer(
                "authorization [REDACTED]",
                (evidence(),),
                "caller-invented-profile",
            )
    assert attempts == calls_before_unknown


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        [],
        [{"text": "x" * 501, "evidenceLabels": ["S1"]}],
        [{"text": "claim", "evidenceLabels": []}],
        [{"text": "claim", "evidenceLabels": ["S1"] * 5}],
        [{"text": "claim", "evidenceLabels": ["S99"]}],
        [{"text": f"claim-{index}", "evidenceLabels": ["S1"]} for index in range(5)],
    ],
    ids=("empty", "claim-length", "no-label", "label-count", "unknown-label", "claim-count"),
)
async def test_grounded_answer_structure_is_closed_and_bounded(claims: object) -> None:
    """Unchecked provider JSON would produce an unsupported or unbounded answer graph."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answer_response(claims=claims))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(), client=client)
        with pytest.raises(ModelUnavailable):
            await adapter.answer(
                "authorization [REDACTED]",
                (evidence(),),
                "quick-hybrid-v1",
            )


@pytest.mark.asyncio
async def test_empty_answer_evidence_and_request_payload_bounds_fail_before_or_during_egress() -> (
    None
):
    """Unbounded evidence/payloads or an empty non-abstained answer must be rejected."""
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=answer_response(answer=""))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(max_request_bytes=1_024), client=client)
        with pytest.raises(ModelUnavailable):
            await adapter.answer("q" * 8_001, (evidence(),), "quick-hybrid-v1")
        with pytest.raises(ModelUnavailable):
            await adapter.answer(
                "authorization",
                tuple(evidence(label=f"S{index}") for index in range(5)),
                "quick-hybrid-v1",
            )
        with pytest.raises(ModelUnavailable):
            await adapter.answer(
                "authorization",
                (evidence(content="x" * 2_001),),
                "quick-hybrid-v1",
            )
        with pytest.raises(ModelUnavailable):
            await adapter.answer(
                "authorization",
                (evidence(content="x" * 900),),
                "quick-hybrid-v1",
            )
        with pytest.raises(ModelUnavailable):
            await adapter.answer(
                "authorization",
                (evidence(),),
                "quick-hybrid-v1",
            )
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_evidence",
    [
        malformed_evidence("content"),
        malformed_evidence("revision"),
        malformed_evidence("chunk_content_hash"),
    ],
)
async def test_non_string_evidence_provenance_fails_before_egress(
    invalid_evidence: Evidence,
) -> None:
    """Runtime annotation bypasses must not serialize malformed evidence to the model."""
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=answer_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(), client=client)
        with pytest.raises(ModelUnavailable):
            await adapter.answer(
                "authorization",
                (invalid_evidence,),
                "quick-hybrid-v1",
            )

    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "source_field"),
    [
        ("revision_kind", "git_commit", True),
        ("revision", "A" * 40, True),
        ("revision", "g" * 40, True),
        ("revision", "a" * 39, True),
        ("source_content_hash", "sha256:" + "A" * 64, True),
        ("source_content_hash", "sha256:" + "g" * 64, True),
        ("source_content_hash", "sha256:" + "a" * 63, True),
        ("chunk_content_hash", "sha256:" + "A" * 64, False),
        ("chunk_content_hash", "sha256:" + "g" * 64, False),
        ("chunk_content_hash", "sha256:" + "b" * 63, False),
    ],
    ids=(
        "open-revision-kind",
        "git-uppercase",
        "git-non-hex",
        "git-wrong-length",
        "source-hash-uppercase",
        "source-hash-non-hex",
        "source-hash-wrong-length",
        "chunk-hash-uppercase",
        "chunk-hash-non-hex",
        "chunk-hash-wrong-length",
    ),
)
async def test_runtime_canonical_provenance_lookalikes_fail_before_litellm_egress(
    field: str,
    value: object,
    source_field: bool,
) -> None:
    """Constructor-time checks cannot authorize a later mutated provider payload."""
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=answer_response())

    mutated = runtime_mutated_evidence(
        field=field,
        value=value,
        source_field=source_field,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(), client=client)
        with pytest.raises(ModelUnavailable) as caught:
            await adapter.answer(
                "authorization",
                (mutated,),
                "quick-hybrid-v1",
            )

    assert calls == 0
    assert "not-a-real-key" not in str(caught.value)
    assert str(value) not in str(caught.value)


class DelayedStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes, delay: float) -> None:
        self.content = content
        self.delay = delay

    async def __aiter__(self):
        await asyncio.sleep(self.delay)
        yield self.content


@pytest.mark.asyncio
async def test_response_stream_byte_limit_and_outer_deadline_include_body_read_and_parse() -> None:
    """Buffer-first or socket-only deadlines would accept one of these responses."""

    async def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b"x" * 2_000 + b"}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized)) as client:
        adapter = LiteLLMAdapter(config(max_response_bytes=512), client=client)
        with pytest.raises(ModelUnavailable):
            await adapter.embed("authorization")

    encoded = json.dumps(embedding_response()).encode()

    async def delayed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=DelayedStream(encoded, 0.05))

    async with httpx.AsyncClient(transport=httpx.MockTransport(delayed)) as client:
        adapter = LiteLLMAdapter(config(deadline_seconds=0.001), client=client)
        with pytest.raises(ModelUnavailable):
            await adapter.embed("authorization")


@pytest.mark.asyncio
async def test_outer_deadline_starts_before_evidence_and_request_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = LiteLLMAdapter._bounded_evidence

    def slow_evidence(
        adapter: LiteLLMAdapter,
        values: tuple[Evidence, ...],
    ) -> list[dict[str, str]]:
        time.sleep(0.01)
        return original(adapter, values)

    monkeypatch.setattr(LiteLLMAdapter, "_bounded_evidence", slow_evidence)

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=answer_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(config(deadline_seconds=0.001), client=client)
        with pytest.raises(ModelUnavailable):
            await adapter.answer(
                "authorization",
                (evidence(),),
                "quick-hybrid-v1",
            )

    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("embedding_dimension", True),
        ("max_request_bytes", 1.5),
        ("max_response_bytes", float("inf")),
        ("max_output_tokens", 0),
        ("max_claims", 1.5),
    ],
)
def test_litellm_config_rejects_non_strict_or_unbounded_limits(
    field: str,
    value: object,
) -> None:
    """Runtime annotation bypasses must not disable a provider resource bound."""
    with pytest.raises((TypeError, ValueError)):
        config(**{field: value})
