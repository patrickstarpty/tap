"""Deterministic ModelGateway for the explicitly isolated fake E2E profile."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.adapters.litellm import LiteLLMModelGateway, LiteLLMModelGatewayConfig, Redact
from tap.modules.ai.domain.models import ModelOperation, ModelRequest
from tap.testing.deterministic_model import _first_evidence_sentence, deterministic_vector


class DeterministicModelGateway(LiteLLMModelGateway):
    def __init__(
        self, config: LiteLLMModelGatewayConfig, *, scope: ProjectScopeContext, redact: Redact
    ) -> None:
        super().__init__(
            replace(config, chat_model="fake/tapper-chat", embedding_model="fake/tapper-embedding"),
            scope=scope,
            redact=redact,
        )

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
                "model": "fake/tapper-embedding",
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
                "model": "fake/tapper-chat",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": len(request.context.split()),
                    "completion_tokens": len(content.split()),
                },
            }
        return body, httpx.Headers()
