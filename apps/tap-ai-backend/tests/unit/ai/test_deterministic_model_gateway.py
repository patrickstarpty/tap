"""The fake E2E profile's `chat_stream` must chunk its deterministic answer into
8-character pieces and still end with the full, non-streaming `ModelResult`."""

from __future__ import annotations

import hashlib

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
from tap.modules.ai.adapters.litellm_catalog import ModelRoles
from tap.modules.ai.domain.models import ModelOperation, ModelRequest, ModelResult, ModelTextDelta
from tap.testing.deterministic_model_gateway import DeterministicModelGateway, _chunk_into


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _request() -> ModelRequest:
    return ModelRequest(
        scope=VALIDATION_SCOPE,
        alias="qwen-plus",
        operation=ModelOperation.CHAT,
        prompt="Use supplied context.",
        prompt_digest=digest("Use supplied context."),
        context="Safe evidence",
        timeout_seconds=1,
        idempotency_key="deterministic-stream-1",
    )


async def _redact(text: str) -> str:
    return text


def _gateway() -> DeterministicModelGateway:
    config = LiteLLMModelGatewayConfig(
        base_url="https://litellm.example",
        api_key="x" * 20,
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=2,
    )
    return DeterministicModelGateway(
        config, scope=VALIDATION_SCOPE, redact=_redact, answer_delay_seconds=0
    )


def test_chunk_into_splits_into_eight_character_pieces_covering_the_whole_text():
    pieces = _chunk_into("ABCDEFGHIJKLMNOPQRSTUVWXY")  # 25 chars: 8 + 8 + 8 + 1

    assert pieces == ["ABCDEFGH", "IJKLMNOP", "QRSTUVWX", "Y"]
    assert all(len(piece) <= 8 for piece in pieces)
    assert "".join(pieces) == "ABCDEFGHIJKLMNOPQRSTUVWXY"


def test_chunk_into_empty_text_yields_no_pieces():
    assert _chunk_into("") == []


@pytest.mark.asyncio
async def test_deterministic_chat_stream_yields_deltas_then_the_full_result():
    gateway = _gateway()

    events = []
    async for event in gateway.chat_stream(_request()):
        events.append(event)

    deltas = [event for event in events if isinstance(event, ModelTextDelta)]
    results = [event for event in events if isinstance(event, ModelResult)]

    assert len(results) == 1
    assert events[-1] is results[0]
    assert all(len(delta.text) <= 8 for delta in deltas)
    assert "".join(delta.text for delta in deltas) == results[0].output
    assert results[0].output == "Grounded"


@pytest.mark.asyncio
async def test_deterministic_gateway_echoes_skill_marker() -> None:
    """A Skill's instruction text (wrapped in `_skill_blocks`, see
    `modules/knowledge/adapters/litellm.py`) lands in the model *prompt*, not the
    *context* the `[e2e-cancel]`/`[e2e-abstain]` markers use -- so an isolated
    E2E journey asserting a selected Skill reached the model needs its own
    prompt-side marker, echoed back as plain, uncited text."""

    import json

    gateway = _gateway()
    context = json.dumps(
        {
            "untrusted": {
                "evidence": [
                    {
                        "label": "S1",
                        "content": "Authorization requires the verified project policy.",
                    }
                ]
            }
        }
    )
    prompt = (
        'You are Tapper.\n\n<skill name="Demo" revision="skill-1">\n'
        "[e2e-skill:demo-token]\n</skill>"
    )
    request = ModelRequest(
        scope=VALIDATION_SCOPE,
        alias="qwen-plus",
        operation=ModelOperation.CHAT,
        prompt=prompt,
        prompt_digest=digest(prompt),
        context=context,
        timeout_seconds=1,
        idempotency_key="deterministic-skill-marker-1",
    )

    result = await gateway.chat(request)

    assert result.output.endswith("[e2e-skill:demo-token]")
    assert "[S1]" in result.output
