"""Deterministic ModelGateway for the explicitly isolated fake E2E profile."""

from __future__ import annotations

import json
from typing import Any

import httpx

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.adapters.litellm import (
    LiteLLMModelGateway,
    LiteLLMModelGatewayConfig,
    Redact,
)
from tap.modules.ai.adapters.litellm_catalog import LiteLLMCatalog, LiteLLMModel, LiteLLMRoutes
from tap.modules.ai.domain.models import ModelOperation, ModelRequest
from tap.testing.deterministic_model import _first_evidence_sentence, deterministic_vector

BUILT_IN_ROUTES = LiteLLMRoutes(
    {
        "qwen-plus": LiteLLMModel("qwen-plus", "Qwen Plus", "chat", True, True, False),
        "text-embedding-v4": LiteLLMModel(
            "text-embedding-v4", "text-embedding-v4", "embedding", False, False, False
        ),
    }
)


class StaticLiteLLMCatalog(LiteLLMCatalog):
    """Fixed routes for fake and test profiles; never calls `/v1/model/info`."""

    def __init__(self, routes: LiteLLMRoutes = BUILT_IN_ROUTES) -> None:
        self._static_routes = routes

    async def routes(self) -> LiteLLMRoutes:
        return self._static_routes

    async def fresh_routes(self) -> LiteLLMRoutes:
        return self._static_routes

    def cached_routes(self) -> LiteLLMRoutes | None:
        return self._static_routes

    async def aclose(self) -> None:
        return None


class DeterministicModelGateway(LiteLLMModelGateway):
    def __init__(
        self, config: LiteLLMModelGatewayConfig, *, scope: ProjectScopeContext, redact: Redact
    ) -> None:
        super().__init__(config, scope=scope, redact=redact, catalog=StaticLiteLLMCatalog())

    async def _post(
        self, request: ModelRequest, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], httpx.Headers]:
        if request.operation is ModelOperation.EMBED:
            vector = (
                deterministic_vector(request.context)
                if self._config.embedding_dimension == 1536
                else tuple([1.0] + [0.0] * (self._config.embedding_dimension - 1))
            )
            body: dict[str, Any] = {
                "model": "fake/deterministic-embedding-v1",
                "data": [{"index": 0, "embedding": list(vector)}],
                "usage": {"prompt_tokens": len(request.context.split())},
            }
        else:
            content = "Grounded"
            if request.operation is ModelOperation.STRUCTURED:
                try:
                    evidence = json.loads(request.context).get("evidence", [])
                except (ValueError, AttributeError):
                    evidence = []
                claims: list[dict[str, Any]] = []
                used = set()
                for item in evidence:
                    sentence = _first_evidence_sentence(item["content"])
                    if sentence and sentence not in used:
                        used.add(sentence)
                        claims.append({"text": sentence, "evidenceLabels": [item["label"]]})
                    if len(claims) == 5:
                        break
                output = (
                    {"answer": "\n\n".join(item["text"] for item in claims), "claims": claims}
                    if claims
                    else {"answer": "Grounded"}
                )
                content = json.dumps(output)
            body = {
                "model": "fake/deterministic-chat-v1",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": len(request.context.split()),
                    "completion_tokens": len(content.split()),
                },
            }
        return body, httpx.Headers()
