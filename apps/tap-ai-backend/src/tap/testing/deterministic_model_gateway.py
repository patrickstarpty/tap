"""Deterministic ModelGateway for the explicitly isolated fake E2E profile."""

from __future__ import annotations

import json
from typing import Any

import httpx

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.adapters.litellm import (
    GatewayAttempts,
    LiteLLMModelGateway,
    LiteLLMModelGatewayConfig,
    Redact,
)
from tap.modules.ai.adapters.litellm_catalog import LiteLLMCatalog, LiteLLMModel, LiteLLMRoutes
from tap.modules.ai.domain.models import ModelOperation, ModelRequest
from tap.modules.ai.ports.model_calls import ModelCallRecorder
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
        self,
        config: LiteLLMModelGatewayConfig,
        *,
        scope: ProjectScopeContext,
        redact: Redact,
        recorder: ModelCallRecorder | None = None,
    ) -> None:
        super().__init__(
            config,
            scope=scope,
            redact=redact,
            catalog=StaticLiteLLMCatalog(),
            recorder=recorder,
        )

    async def _post(
        self, request: ModelRequest, payload: dict[str, Any], attempts: GatewayAttempts
    ) -> tuple[dict[str, Any], httpx.Headers]:
        attempts.count = 1
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
            if (
                request.operation is ModelOperation.STRUCTURED
                and isinstance(request.schema, dict)
                and request.schema.get("title") == "PromptSuggestions"
            ):
                try:
                    data = json.loads(request.context)
                    if not isinstance(data, dict):
                        data = {}
                except ValueError:
                    data = {}
                locale = data.get("locale")
                suggestions = [
                    {
                        "question": (
                            f"《{source['name']}》主要包含哪些内容？"
                            if locale == "zh"
                            else f"What does {source['name']} cover?"
                        ),
                        "sourceIds": [source["id"]],
                    }
                    for source in data.get("sources", [])[:8]
                ]
                content = json.dumps({"suggestions": suggestions})
            elif request.operation is ModelOperation.STRUCTURED:
                try:
                    parsed_context = json.loads(request.context)
                    evidence = parsed_context.get("evidence", [])
                    relations = parsed_context.get("relations", [])
                except (ValueError, AttributeError):
                    evidence = []
                    relations = []
                claims: list[dict[str, Any]] = []
                used = set()
                # A relation question's fake answer must still cite the edge
                # (`R1`), not only chunk evidence, or the `R`-label reconciliation
                # in `relation_claims.py` strips every edge citation and the
                # conversation never shows a relation citation chip. The claim
                # text names both endpoints verbatim -- the minimum
                # `claim_mentions_both_endpoints` requires to keep the label.
                if relations:
                    first_relation = relations[0]
                    relation_label = (
                        first_relation.get("relationLabel")
                        or first_relation.get("relationType")
                        or "relates to"
                    )
                    sentence = (
                        f"{first_relation['subject']} {relation_label} {first_relation['object']}."
                    )
                    used.add(sentence)
                    claims.append({"text": sentence, "evidenceLabels": [first_relation["label"]]})
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
