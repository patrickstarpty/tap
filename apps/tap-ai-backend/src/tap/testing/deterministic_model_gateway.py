"""Deterministic ModelGateway for the explicitly isolated fake E2E profile."""

from __future__ import annotations

import asyncio
import json
import re
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
from tap.modules.chat.application.plan_answer import _is_relation_question
from tap.modules.chat.domain.answer_plan import derive_agent_intent
from tap.testing.deterministic_model import (
    _ANSWER_DELAY_SECONDS,
    _CANCELLATION_QUERY_SUFFIX,
    _first_evidence_sentence,
    deterministic_vector,
)

# Task 9 fix round 1: a test-only marker forcing the fake model to abstain
# (see `_abstention_requested`), mirroring `_CANCELLATION_QUERY_SUFFIX`'s
# `[e2e-cancel]`. Both only ever reach this deterministic gateway, which is
# wired up for the explicitly isolated fake E2E profile only.
_ABSTENTION_QUERY_SUFFIX = " [e2e-abstain]"

# Task 7: a test-only marker that lets an isolated E2E journey assert a
# selected Skill actually reached the model prompt. Unlike `[e2e-cancel]`/
# `[e2e-abstain]` (matched against the *query*), this is matched against the
# assembled *prompt* -- the Skill instruction text, wrapped in its
# `<skill name=... revision=...>` block by `_skill_blocks`
# (`modules/knowledge/adapters/litellm.py`), lands in the prompt, not the
# context. The marker is echoed back as plain, uncited text so a deterministic
# answer's existing `[S1]`/`[R1]` inline citations stay intact and
# `parse_inline_answer`'s `_MARKER` (only `[S1]`-`[R999]`-shaped) never
# mistakes it for one.
_SKILL_MARKER = re.compile(r"\[e2e-skill:[^\[\]]+\]")


def _skill_marker_in_prompt(prompt: str) -> str | None:
    match = _SKILL_MARKER.search(prompt)
    return match.group(0) if match else None


# Longer than `DraftBatcher`'s default `max_delay_s` (0.05s); see `chat_stream`.
_PRE_HOLD_FLUSH_PAUSE_SECONDS = 0.2

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


def _query_ends_with(context: str, suffix: str) -> bool:
    """`KnowledgeModelGateway.answer` carries the question under the JSON `query` key
    (`src/tap/modules/knowledge/adapters/litellm.py`); match an E2E marker there so an
    unrelated evidence or relation record ending in similar text cannot trigger it.
    Other structured contexts (e.g. prompt suggestions) do not carry a separately
    addressable question, so fall back to a plain substring check on the raw context --
    a production marker never occurs there outside of this fake E2E contract."""

    try:
        parsed = json.loads(context)
    except (ValueError, TypeError):
        parsed = None
    query = parsed.get("query") if isinstance(parsed, dict) else None
    if isinstance(query, str):
        return query.endswith(suffix)
    return isinstance(context, str) and suffix in context


def _cancellation_requested(context: str) -> bool:
    return _query_ends_with(context, _CANCELLATION_QUERY_SUFFIX)


def _abstention_requested(context: str) -> bool:
    """Task 9 fix round 1: the real stack has no relevance/score cutoff at
    retrieval (`AuthorizedRetrieval._hit_is_in_execution` admits every hit
    whose family/corpus/embedding version match once no `SCOPE` resource is
    selected -- an unrestricted question almost always finds *some*
    evidence in whatever is already published), so there is no
    content-only way to force a genuinely empty-evidence abstain
    deterministically against a shared E2E corpus. This marker lets the
    isolated browser E2E force the *model* to abstain instead (exactly the
    `verify_answer` path a real model takes when it writes no inline
    `[S1]`/`[R1]` markers at all): `KnowledgeModelGateway.answer`'s
    platform instruction already tells a real model to answer with
    `INSUFFICIENT_EVIDENCE` when no evidence supports the answer
    (`modules/knowledge/adapters/litellm.py`), so this only mirrors that
    contract for the fake model instead of fabricating a new one."""

    return _query_ends_with(context, _ABSTENTION_QUERY_SUFFIX)


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
        forming incrementally. Metering and recording happen in `chat` itself.

        The `[e2e-cancel]` cancellation delay (Task 9) runs here now, immediately
        before the *last* piece, instead of inside `_post` before any content
        exists. `DraftBatcher` (Task 6) only ever sends text past its
        `holdback_chars` margin, so a Stop click must land after several pieces
        have already been paced out at the normal 0.02s cadence -- exactly as
        they are for every other piece -- for a non-empty draft chunk to have
        already reached Redis by the time the delay (and then a cancel) lands."""

        result = await self.chat(request)
        text = result.output if isinstance(result.output, str) else ""
        pieces = _chunk_into(text)
        cancellation_delay = (
            self._answer_delay_seconds
            if self._answer_delay_seconds and _cancellation_requested(request.context)
            else None
        )
        last_index = len(pieces) - 1
        for index, piece in enumerate(pieces):
            if cancellation_delay is not None:
                if index == last_index - 1:
                    # `DraftBatcher` only flushes on a push, once `max_delay_s`
                    # has elapsed since its last flush; event-loop jitter can
                    # make it flush one piece early and then skip the last
                    # pre-hold piece, leaving the pre-hold text unflushed until
                    # after the hold. Pausing past that window guarantees this
                    # push flushes, so a draft is visible before Stop is clicked.
                    await asyncio.sleep(_PRE_HOLD_FLUSH_PAUSE_SECONDS)
                elif index == last_index:
                    await asyncio.sleep(cancellation_delay)
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
            # The `[e2e-cancel]` delay used to run here, before any content existed;
            # it now runs in `chat_stream`, after the first chunk is yielded, so a
            # partial draft is visible before the delay (Task 9).
            content = "Grounded"
            # Resolved once; the grounded-answer branch below may fold it
            # into a cited claim instead and reset this to `None` so the
            # trailing-echo check further down does not double it.
            skill_marker = _skill_marker_in_prompt(request.prompt)
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
            elif (
                request.operation is ModelOperation.STRUCTURED
                and isinstance(request.schema, dict)
                and request.schema.get("title") == "AnswerPlan"
            ):
                # The planner (`ModelGatewayPlanner`) now consults the model
                # on every `retrieve`/`graph` turn, not only `complex_query`
                # ones (Task 4); echo back a schema-conformant suggestion
                # that keeps the planner's own deterministic route/intent
                # (never overriding it) with a bounded, non-authoritative
                # observability classification, instead of falling through
                # to the generic `{"answer": "Grounded"}` stub below, which
                # would always degrade this plan to `invalid-plan`.
                try:
                    data = json.loads(request.context)
                    if not isinstance(data, dict):
                        data = {}
                except ValueError:
                    data = {}
                raw_question = data.get("question")
                question: str = raw_question if isinstance(raw_question, str) else ""
                source_ids = [item for item in data.get("sourceIds", []) if isinstance(item, str)]
                # Mirror the planner's own relation heuristic instead of
                # hard-coding `retrieve`: a fake suggestion that silently
                # overrode a relation question's `graph` route to `retrieve`
                # would change which seam (relation extraction vs. plain
                # retrieval) the turn exercises, purely because the model
                # got consulted -- not because any answer actually changed.
                intent, route = (
                    ("relation", "graph")
                    if _is_relation_question(question)
                    else ("factual_lookup", "retrieve")
                )
                queries = (
                    [
                        {
                            "id": "q1",
                            "text": question,
                            "depends_on": [],
                            "evidence_goal": question,
                            "source_ids": source_ids,
                        }
                    ]
                    if source_ids
                    else []
                )
                content = json.dumps(
                    {
                        "intent": intent,
                        "route": route,
                        "confidence": 0.9,
                        "standalone_query": question,
                        "missing": [],
                        "queries": queries,
                        "agent_intent": derive_agent_intent(intent, route, len(queries)),
                        "intent_reason": "",
                    }
                )
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
                claims = (
                    []
                    if _abstention_requested(request.context)
                    else _grounded_claims_from_context(request.context)
                )
                if skill_marker is not None and claims:
                    # Fold the marker into the *last* claim's own cited
                    # sentence (before its closing punctuation) instead of
                    # appending it as a separate, uncited trailing fragment.
                    # `parse_inline_answer`'s rendering is claim-driven --
                    # PR 3's inline-citation UI is built from `claims`, not
                    # the raw joined text -- so a marker outside every
                    # claim's span is not reliably guaranteed to reach the
                    # rendered answer across every sentence shape, while a
                    # marker inside a real, cited claim always does.
                    last = dict(claims[-1])
                    text = last["text"]
                    last["text"] = (
                        f"{text[:-1]} {skill_marker}{text[-1]}"
                        if text and text[-1] in ".!?。！？"
                        else f"{text} {skill_marker}"
                    )
                    claims = [*claims[:-1], last]
                    skill_marker = None
                content = (
                    " ".join(
                        item["text"] + "".join(f"[{label}]" for label in item["evidenceLabels"])
                        for item in claims
                    )
                    if claims
                    else "INSUFFICIENT_EVIDENCE"
                )
            if request.operation is not ModelOperation.STRUCTURED:
                # Structured content is a closed JSON document (PromptSuggestions,
                # `{answer: ...}`, ...); appending plain text would corrupt it, and
                # a Skill is only ever governed into the Markdown/CHAT answer path
                # (`_skill_blocks` in `modules/knowledge/adapters/litellm.py`). The
                # grounded branch above already folded the marker into a cited
                # claim when one existed, resetting `skill_marker` to `None` so
                # it is not echoed twice; every other content shape (plain
                # "Grounded", `INSUFFICIENT_EVIDENCE`, a claims-less grounded
                # context) still gets the plain trailing echo.
                if skill_marker is not None:
                    content = f"{content} {skill_marker}"
            body = {
                "model": "fake/deterministic-chat-v1",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": len(request.context.split()),
                    "completion_tokens": len(content.split()),
                },
            }
        return body, httpx.Headers()
