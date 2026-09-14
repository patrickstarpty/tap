"""Shared contract checks for governed model gateway implementations."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.models import (
    ModelCapability,
    ModelDescriptor,
    ModelOperation,
    ModelRequest,
    schema_digest,
    text_digest,
)


async def assert_catalog_conformance(gateway: object) -> None:
    catalog = await gateway.catalog(VALIDATION_SCOPE)  # type: ignore[attr-defined]
    assert catalog[0] == (
        ModelDescriptor(
            alias="tapper-chat",
            display_name="Qwen Plus",
            capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
            enabled=True,
        )
    )


async def assert_gateway_conformance(gateway):
    await assert_catalog_conformance(gateway)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    base = ModelRequest(
        VALIDATION_SCOPE,
        "tapper-chat",
        ModelOperation.CHAT,
        "Use supplied context.",
        text_digest("Use supplied context."),
        "Safe evidence",
        1,
        "conformance-1",
    )
    chat = await gateway.chat(base)
    assert chat.output == "Grounded"
    embedding = await gateway.embed(
        replace(base, alias="tapper-embedding", operation=ModelOperation.EMBED)
    )
    assert len(embedding.output) == 2
    structured = await gateway.generate_structured(
        replace(
            base,
            operation=ModelOperation.STRUCTURED,
            schema=schema,
            schema_digest=schema_digest(schema),
        )
    )
    assert structured.output == {"answer": "Grounded"}
    for result in (chat, embedding, structured):
        assert result.audit.scope == VALIDATION_SCOPE
        assert result.audit.idempotency_key == "conformance-1"
        assert result.audit.actual_model == result.actual_model
        assert result.audit.usage == result.usage
        assert result.actual_provider
        assert result.actual_model.startswith(result.actual_provider + "/")
        assert result.actual_model.split("/", 1)[1] not in {"tapper-chat", "tapper-embedding"}
        assert result.actual_provider not in {"tapper-chat", "tapper-embedding"}
        assert not hasattr(result, "reasoning_content")
    for invalid in (
        replace(base, alias="unknown"),
        replace(base, scope=replace(VALIDATION_SCOPE, project_id="other")),
        replace(base, prompt_digest=text_digest("tampered")),
        replace(base, context="password=private-secret"),
        replace(base, timeout_seconds=10000),
    ):
        with pytest.raises(ValueError):
            await gateway.chat(invalid)
    with pytest.raises(ValueError):
        await gateway.catalog(replace(VALIDATION_SCOPE, project_id="other"))
