"""Deterministic ModelGateway for the explicitly isolated fake E2E profile."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
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
from tap.modules.ai.domain.models import (
    ModelOperation,
    ModelRequest,
    ModelStreamEvent,
    ModelTextDelta,
)
from tap.modules.ai.ports.model_calls import ModelCallRecorder
from tap.testing.deterministic_model import (
    _ANSWER_DELAY_SECONDS,
    _CANCELLATION_QUERY_SUFFIX,
    _first_evidence_sentence,
    deterministic_vector,
)

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


def _chunk_into(text: str, size: int = 8) -> list[str]:
    """Split `text` into `size`-character pieces, in order, covering every character
    exactly once (the last piece may be shorter)."""

    return [text[index : index + size] for index in range(0, len(text), size)]


def _cancellation_requested(context: str) -> bool:
    """`KnowledgeModelGateway.answer` carries the question under the JSON `query` key
    (`src/tap/modules/knowledge/adapters/litellm.py`); match the E2E cancel marker there
    so an unrelated evidence or relation record ending in similar text cannot trigger it.
    Other structured contexts (e.g. prompt suggestions) do not carry a separately
    addressable question, so fall back to a plain substring check on the raw context --
    the production marker never occurs there outside of this fake E2E contract."""

    try:
        parsed = json.loads(context)
    except (ValueError, TypeError):
        parsed = None
    query = parsed.get("query") if isinstance(parsed, dict) else None
    if isinstance(query, str):
        return query.endswith(_CANCELLATION_QUERY_SUFFIX)
    return isinstance(context, str) and _CANCELLATION_QUERY_SUFFIX in context


def _is_grounded_answer_context(context: str) -> bool:
    """True only for `KnowledgeModelGateway.answer`'s context shape (PR 4's
    `untrusted_context` always nests evidence under `untrusted.evidence`); a
    direct model-only `chat()` call -- with or without a governed template --
    never has an `untrusted` key, so it is never mistaken for a grounded
    answer here."""

    try:
        parsed = json.loads(context)
    except (ValueError, TypeError):
        return False
    return (
        isinstance(parsed, dict)
        and isinstance(parsed.get("untrusted"), dict)
        and "evidence" in parsed["untrusted"]
    )


def _grounded_claims_from_context(context: str) -> list[dict[str, Any]]:
    """Fabricate the same cited claims this fake has always produced from the
    `untrusted.evidence`/`untrusted.relations` records, shared by the closed
    JSON `{answer, claims}` (STRUCTURED) path and the inline-marker Markdown
    (CHAT, PR 3) path."""

    try:
        parsed_context = json.loads(context)
        untrusted = parsed_context.get("untrusted", {})
        evidence = untrusted.get("evidence", [])
        relations = untrusted.get("relations", [])
    except (ValueError, AttributeError):
        evidence = []
        relations = []
    claims: list[dict[str, Any]] = []
    used: set[str] = set()
    # A relation question's fake answer must still cite the edge
    # (`R1`), not only chunk evidence, or the `R`-label reconciliation
    # in `relation_claims.py` strips every edge citation and the
    # conversation never shows a relation citation chip. The claim
    # text names both endpoints verbatim -- the minimum
    # `claim_mentions_both_endpoints` requires to keep the label.
    #
    # The relation sentence built below can come out byte-identical
    # to the first sentence of the S-labeled chunk the relation's
    # own evidence resolved to in this turn (rule extraction keeps
    # case and articles, so e.g. "Underwriting review requires
    # health disclosure." matches verbatim) -- `used` then makes
    # the evidence loop below skip that S label's claim entirely,
    # silently dropping a citation for a chunk that *did* ground
    # the answer. Folding every such S label directly onto the
    # relation claim's `evidenceLabels` keeps the citation even
    # when its own paragraph is suppressed as a duplicate.
    if relations:
        first_relation = relations[0]
        relation_label = (
            first_relation.get("relationLabel")
            or first_relation.get("relationType")
            or "relates to"
        )
        sentence = f"{first_relation['subject']} {relation_label} {first_relation['object']}."
        used.add(sentence)
        evidence_label_ids = {item["label"] for item in evidence}
        relation_s_labels = dict.fromkeys(
            entry["label"]
            for entry in first_relation.get("evidence", [])
            if "label" in entry and entry["label"] in evidence_label_ids
        )
        claims.append(
            {
                "text": sentence,
                "evidenceLabels": [first_relation["label"], *relation_s_labels],
            }
        )
    for item in evidence:
        sentence = _first_evidence_sentence(item["content"])
        if sentence and sentence not in used:
            used.add(sentence)
            claims.append({"text": sentence, "evidenceLabels": [item["label"]]})
        if len(claims) == 5:
            break
    return claims


class DeterministicModelGateway(LiteLLMModelGateway):
    def __init__(
        self,
        config: LiteLLMModelGatewayConfig,
        *,
        scope: ProjectScopeContext,
        redact: Redact,
        recorder: ModelCallRecorder | None = None,
        answer_delay_seconds: float = _ANSWER_DELAY_SECONDS,
    ) -> None:
        super().__init__(
            config,
            scope=scope,
            redact=redact,
            catalog=StaticLiteLLMCatalog(),
            recorder=recorder,
        )
        if (
            isinstance(answer_delay_seconds, bool)
            or not isinstance(answer_delay_seconds, int | float)
            or not 0 <= answer_delay_seconds <= 5
        ):
            raise ValueError("deterministic E2E answer delay must be zero to five seconds")
        self._answer_delay_seconds = float(answer_delay_seconds)

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Chunk the deterministic non-streaming chat answer into 8-character pieces,
        pacing them with a short sleep so the fake E2E profile can observe a draft
        forming incrementally. Metering and recording happen in `chat` itself."""

        result = await self.chat(request)
        text = result.output if isinstance(result.output, str) else ""
        for piece in _chunk_into(text):
            yield ModelTextDelta(piece)
            await asyncio.sleep(0.02)
        yield result

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
            if self._answer_delay_seconds and _cancellation_requested(request.context):
                await asyncio.sleep(self._answer_delay_seconds)
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
                # Baseline conformance for any other structured schema (test
                # design, graph extraction, chat planning, ...): a minimal,
                # schema-agnostic completion. Grounded answers no longer request
                # STRUCTURED output at all (PR 3 streams inline-marked Markdown
                # instead, handled below), so this never needs evidence/relations.
                content = json.dumps({"answer": "Grounded"})
            elif _is_grounded_answer_context(request.context):
                # `KnowledgeModelGateway.answer` (PR 3) now streams a Markdown chat
                # answer with inline `[S1]`/`[R1]` markers instead of requesting the
                # closed `{answer, claims}` JSON schema; build the same claims this
                # fake has always produced from evidence/relations, but render them
                # as inline-marked sentences so `parse_inline_answer` grounds them
                # exactly as the JSON path used to. A plain `chat()` (model-only,
                # no retrieval) never carries `untrusted.evidence`, so it still gets
                # the unchanged plain "Grounded" answer below.
                claims = _grounded_claims_from_context(request.context)
                content = (
                    " ".join(
                        item["text"] + "".join(f"[{label}]" for label in item["evidenceLabels"])
                        for item in claims
                    )
                    if claims
                    else "INSUFFICIENT_EVIDENCE"
                )
            body = {
                "model": "fake/deterministic-chat-v1",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": len(request.context.split()),
                    "completion_tokens": len(content.split()),
                },
            }
        return body, httpx.Headers()
