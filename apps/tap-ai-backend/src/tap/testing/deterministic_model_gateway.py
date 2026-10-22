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
    ModelMessage,
    ModelOperation,
    ModelRequest,
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCall,
)
from tap.modules.ai.domain.tools import tool_for_wire_name
from tap.modules.ai.ports.model_calls import ModelCallRecorder
from tap.modules.chat.application.plan_answer import (
    is_comparison_question,
    is_relation_question,
)
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

# Task 11/12: markers an isolated E2E journey (or the intent golden fake
# gate) can put in the question text to force the fake `AnswerPlan` stub's
# `agent_intent` observability classification (never the authoritative
# `intent`/`route`, which stay driven by `is_relation_question`/
# `is_comparison_question` as before) without needing a real model. Matched
# against the raw question text, the same string the stub already decodes
# out of `request.context`'s `question` key.
_OUT_OF_SCOPE_MARKER = "[e2e-out-of-scope]"
_TOOL_MARKER_PREFIX = "[e2e-tool:"

# Task 12: isolated E2E/integration marker the fake gateway matches against
# the question text (same `query` field the markers above decode out of
# `request.context`) to force the model's answer branch into a `CLARIFY:`
# reply, exercising the `answerKind = "clarification"` path end to end
# without a real model.
_CLARIFY_MARKER = "[e2e-clarify]"
_CLARIFY_REPLY = "CLARIFY: 请说明你指的是哪个产品或版本？"

# Final review C-1: `[e2e-insights-only]` makes the grounded answer cite only
# the Insights (`I<n>`) records in the pool -- no S/R claim at all -- so the
# insights-only answer shape (Decision C: no claims, non-empty
# `insightsFacts`) is exercised end to end without a real model.
_INSIGHTS_ONLY_MARKER = "[e2e-insights-only]"

# Task 12: one marker produces one native tool call per model round --
# `[e2e-tool:<toolId>:<argument text>]`, matched in the order it appears in
# the question. `toolId` is the dotted tool id (`knowledge.search`,
# `graph.relations`, `insights.query`), never the online wire/function name.
_TOOL_CALL_MARKER = re.compile(r"\[e2e-tool:([^:\[\]]+):([^\[\]]*)\]")

# Task 12: `insights.query`'s input contract has no free-text `query` field
# (spec 4.4/`_INSIGHTS_QUERY_SCHEMA`), so an `[e2e-tool:insights.query:...]`
# marker always produces this fixed, schema-valid call instead of echoing
# its argument text -- `from_date < to_date`, an IANA `timezone`, and an
# `asOf` `datetime.fromisoformat`-parseable instant (see
# `StructuredInsightsTool.invoke` / `MetricQuery.__post_init__`,
# `modules/ai/ports/insights.py`).
_FIXED_INSIGHTS_ARGUMENTS: dict[str, Any] = {
    "metricIds": ["first_pass_rate"],
    "filters": {},
    "from": "2026-01-01",
    "to": "2026-01-31",
    "timezone": "UTC",
    "asOf": "2026-01-31T00:00:00Z",
}


def _question_from_context(context: str) -> str:
    """The question text under the top-level `query` key `trusted_value`
    (`KnowledgeModelGateway.answer`) always carries -- shared by every
    marker this fake gateway matches against the question, independent of
    whether `context` also carries an `untrusted.evidence` grounded-answer
    section (Task 12's tool-calling markers must still work before any
    evidence/relation record has been fabricated)."""

    try:
        parsed = json.loads(context)
    except (ValueError, TypeError):
        return ""
    query = parsed.get("query") if isinstance(parsed, dict) else None
    return query if isinstance(query, str) else ""


def _tool_call_markers(question: str) -> list[tuple[str, str]]:
    """Every `[e2e-tool:<toolId>:<argument text>]` marker in `question`, in
    the order it appears (first-to-last == the order the isolated E2E/
    integration test wrote them into the question)."""

    return [(match.group(1), match.group(2)) for match in _TOOL_CALL_MARKER.finditer(question)]


def _consumed_tool_marker_keys(
    messages: tuple[ModelMessage, ...],
) -> set[tuple[str, str | None]]:
    """The `(toolId, argument text)` pairs already produced as a native tool
    call in an earlier round of this same turn, decoded from the assistant
    `ModelMessage`s the gateway's native tool-calling protocol folds prior
    rounds into (`_transcript_messages`, `modules/knowledge/adapters/
    litellm.py`). `insights.query` has no `query` argument of its own
    (its call is always the fixed `_FIXED_INSIGHTS_ARGUMENTS`), so any prior
    `insights.query` call consumes every `[e2e-tool:insights.query:...]`
    marker regardless of its argument text -- keyed on `None` for both
    sides instead."""

    consumed: set[tuple[str, str | None]] = set()
    for message in messages:
        if message.role != "assistant":
            continue
        for call in message.tool_calls:
            definition = tool_for_wire_name(call.name)
            if definition is None:
                continue
            tool_id = definition.tool_id
            if tool_id == "insights.query":
                consumed.add((tool_id, None))
                continue
            try:
                parsed_args = json.loads(call.arguments_json)
            except (ValueError, TypeError):
                parsed_args = None
            query = parsed_args.get("query") if isinstance(parsed_args, dict) else None
            consumed.add((tool_id, query if isinstance(query, str) else None))
    return consumed


def _called_tool_ids(messages: tuple[ModelMessage, ...]) -> frozenset[str]:
    """Tool ids this turn has already issued a native tool call for, in any
    prior round -- used to keep a relation-question's automatic
    `graph.relations` call (branch 2 of the fake tool-calling contract) from
    firing more than once per turn even once its own marker-consumption key
    (which `insights.query`-style calls do not carry) would not otherwise
    prevent a repeat."""

    called: set[str] = set()
    for message in messages:
        if message.role != "assistant":
            continue
        for call in message.tool_calls:
            definition = tool_for_wire_name(call.name)
            if definition is not None:
                called.add(definition.tool_id)
    return frozenset(called)


def _tool_call_arguments(tool_id: str, argument_text: str) -> dict[str, Any] | None:
    if tool_id in ("knowledge.search", "graph.relations"):
        return {"query": argument_text}
    if tool_id == "insights.query":
        return dict(_FIXED_INSIGHTS_ARGUMENTS)
    return None


def _fake_tool_call(request: ModelRequest) -> ModelToolCall | None:
    """The fake gateway's deterministic tool-calling decision for one model
    round (Task 12), evaluated fresh from `request` alone -- no state is
    kept across calls, mirroring every other marker this fake matches.

    1. The first `[e2e-tool:<toolId>:...]` marker in the question not yet
       consumed by an earlier round's native tool call produces that call.
    2. Failing that, a relation question (`is_relation_question`, the
       planner's own public heuristic -- Task 4's ruling that the fake
       reuses it rather than duplicating it) whose turn offers
       `graph.relations` and has not already called it gets one automatic
       `graph.relations` call, so the relation-answer E2E journey still
       exercises the tool-calling path instead of falling back to the old
       inline-relation answer.
    3. Otherwise `None`: the caller falls through to its existing final-
       answer logic.
    """

    offered_wire_names = {spec.tool_id: spec.wire_name for spec in request.tools}
    if not offered_wire_names:
        return None
    question = _question_from_context(request.context)
    prior_tool_messages = sum(1 for message in request.messages if message.role == "tool")
    consumed = _consumed_tool_marker_keys(request.messages)
    for tool_id, argument_text in _tool_call_markers(question):
        if tool_id not in offered_wire_names:
            continue
        key = (tool_id, None) if tool_id == "insights.query" else (tool_id, argument_text)
        if key in consumed:
            continue
        arguments = _tool_call_arguments(tool_id, argument_text)
        if arguments is None:
            continue
        return ModelToolCall(
            f"fake-{prior_tool_messages}",
            offered_wire_names[tool_id],
            json.dumps(arguments),
        )
    if (
        "graph.relations" in offered_wire_names
        and "graph.relations" not in _called_tool_ids(request.messages)
        and is_relation_question(question)
    ):
        return ModelToolCall(
            f"fake-{prior_tool_messages}",
            offered_wire_names["graph.relations"],
            json.dumps({"query": question}),
        )
    return None


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
        insights = untrusted.get("insights", [])
    except (ValueError, AttributeError):
        evidence = []
        relations = []
        insights = []
    claims: list[dict[str, Any]] = []
    used: set[str] = set()
    if _INSIGHTS_ONLY_MARKER in _question_from_context(context):
        evidence = []
        relations = []
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
    # Task 8/12: an `insights.query` record (`I<n>`) never grounds a
    # knowledge citation -- `AuthorizedRetrieval.verify_answer`
    # (`application/retrieve.py`) only ever surfaces it through
    # `insights_facts` once some claim's `evidenceLabels` actually cites
    # the label, so the fake must produce one (distinct from every S/R
    # claim above, since `len(claims) == 5` there must not starve this).
    for item in insights:
        if len(claims) == 5:
            break
        sentence = f"{item['metricId']} is {item['value']}."
        if sentence not in used:
            used.add(sentence)
            claims.append({"text": sentence, "evidenceLabels": [item["label"]]})
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
            # Task 12: native tool calling only ever happens for a CHAT
            # request that was actually offered `tools` this round (the
            # gateway's own `_validate` rejects `tools` on any other
            # operation) -- checked before every other branch below so a
            # pending marker/relation-question call wins even on a request
            # that would otherwise also match PromptSuggestions/AnswerPlan's
            # STRUCTURED shape (impossible in practice, since those never
            # carry `tools`, but this keeps the precedence explicit).
            if request.operation is ModelOperation.CHAT and request.tools:
                tool_call = _fake_tool_call(request)
                if tool_call is not None:
                    body = {
                        "model": "fake/deterministic-chat-v1",
                        "choices": [
                            {
                                "message": {
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": tool_call.call_id,
                                            "function": {
                                                "name": tool_call.name,
                                                "arguments": tool_call.arguments_json,
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": len(request.context.split()),
                            "completion_tokens": 0,
                        },
                    }
                    return body, httpx.Headers()
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
                # Mirror the planner's own relation/comparison heuristics
                # (imported, not duplicated) instead of hard-coding
                # `retrieve`/`factual_lookup`: a fake suggestion that
                # silently overrode a relation question's `graph` route, or
                # a comparison question's `comparison` intent and
                # `multi_step_qa` agent intent, would change which seam the
                # turn exercises purely because the model got consulted --
                # not because any answer actually changed.
                # Task 11: `[e2e-out-of-scope]` is authoritative, not just
                # observability -- it also forces the legacy `intent`/`route`
                # so `AnswerPlan.__post_init__`'s out-of-scope agreement
                # invariant (`agent_intent == "out_of_scope"` iff
                # `intent == "out_of_scope"`) holds.
                if _OUT_OF_SCOPE_MARKER in question:
                    intent, route = "out_of_scope", "direct"
                elif is_relation_question(question):
                    intent, route = "relation", "graph"
                elif is_comparison_question(question):
                    intent, route = "comparison", "retrieve"
                else:
                    intent, route = "factual_lookup", "retrieve"
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
                    if source_ids and route in ("retrieve", "graph")
                    else []
                )
                # Task 11: `[e2e-tool:` only overrides the observability-only
                # `agent_intent` (never limits which tools a turn may use,
                # per the planner prompt) -- the out-of-scope marker above
                # already took the authoritative branch, so it also wins
                # here even if a question somehow carried both markers.
                if intent == "out_of_scope":
                    agent_intent = "out_of_scope"
                elif _TOOL_MARKER_PREFIX in question:
                    agent_intent = "multi_step_qa"
                else:
                    agent_intent = derive_agent_intent(intent, route, len(queries))
                content = json.dumps(
                    {
                        "intent": intent,
                        "route": route,
                        "confidence": 0.9,
                        "standalone_query": question,
                        "missing": [],
                        "queries": queries,
                        "agent_intent": agent_intent,
                        "intent_reason": "deterministic fake",
                    }
                )
            elif request.operation is ModelOperation.STRUCTURED:
                # Baseline conformance for any other structured schema (test
                # design, graph extraction, chat planning, ...): a minimal,
                # schema-agnostic completion. Grounded answers no longer request
                # STRUCTURED output at all (PR 3 streams inline-marked Markdown
                # instead, handled below), so this never needs evidence/relations.
                content = json.dumps({"answer": "Grounded"})
            elif _CLARIFY_MARKER in _question_from_context(request.context):
                # Task 12: `[e2e-clarify]` forces the same `CLARIFY:`-prefixed
                # reply shape a real model takes when it cannot answer
                # without more information (`_CLARIFY_PREFIX`,
                # `modules/knowledge/adapters/litellm.py`'s `answer`): the
                # caller strips the prefix and returns `answerKind =
                # "clarification"` with no claims/citations. Checked before
                # the grounded-answer branch so it fires even when the
                # turn's evidence pool is non-empty.
                content = _CLARIFY_REPLY
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
