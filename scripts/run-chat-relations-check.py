#!/usr/bin/env python3
"""Opt-in, real-model check of relation-answer quality through the Tapper CHAT path.

Since LangGraph PR 5, chat relation analysis runs only when the model calls the
`graph.relations` tool inside the `model <-> tools` agent loop. The single-shot
gate (`make quality-graph-relations-real`, `run-graph-relations-candidate.py --mode
real`) never enters that loop, so this script asks each relation question in the
Graph golden set through the real HTTP API exactly as the web app does -- a new
durable Conversation/Turn per question (`POST /conversations`, the exact web
`Origin`, frozen published source revisions, no Agent unless `--agent-revision-id`
is given -- the web default) -- then follows the conversation SSE stream to the
Turn's terminal event and records:

- whether a `task.step` for `graph.relations` reached `completed`, and whether the
  tool was offered at all (the trace's `tap.agent.tools` attribute, when present);
- the answer's R-labelled edge citations, answer kind and abstention;
- the models that answered (the trace's successful non-embedding model calls);
- end-to-end latency (POST accepted -> terminal event).

A question whose request is rejected or whose Turn times out is recorded with
outcome `error`/`timeout` (scored as not passed) and the run continues;
observations are rewritten after every question. The API must run with
`TAPPER_GRAPH_REASONING=1`, otherwise `graph.relations` is never offered.
Regression groups are out of scope: only golden relation questions are asked.

Answers are written as `graph-relation-observations-v1` (plus chat-only fields)
and scored with `scripts/evaluate-graph-relations.py`'s own `evaluate()`, so the
`summary`/`questions` metrics mean exactly what they mean in the single-shot
report. `graphRelationsToolCallRate` is added on top.

Gate rule (exit code):

- without `--baseline-observations`: report-only, exit 0 once the report is written;
- with a single-shot real baseline (the `TAPPER_GRAPH_REASONING=1`
  `run-graph-relations-candidate.py --mode real` observations), the baseline must be
  comparable -- same goldenDigest, corpusDigest, graphVersion, real execution,
  graphReasoning True, and every answer's model call uses the baseline's alias and
  (normalized) provider model id, see `model_check`; a call with no recorded
  upstream model is incomparable too unless `--allow-alias-only-model-check` (env
  `TAP_CHAT_RELATIONS_ALLOW_ALIAS_ONLY=1`) accepts alias-only identity, which the
  report records under `gate.aliasOnlyModelCheck` -- or the report is written with
  gate mode `incomparable` and the run exits 2; then, scored
  over the same selected questions, the gate passes (exit 0) only when
  1. `relationPassRateNotBelowBaseline`: chat pass rate >= single-shot pass rate,
  2. `wrongEdgeQuestionsNotAboveBaseline`: chat `wrong` questions <= single-shot's,
  3. `graphRelationsToolCallRate`: the model called `graph.relations` on >=
     `REQUIRED_PERCENT` (80%) of the questions, the relation summary's own threshold;
  otherwise exit 1.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from tap.modules.knowledge.ports.answers import RELATION_CITATION_LABEL_PATTERN
from tap.quality.evidence import canonical_digest
from tap.quality.graph_relations import (
    REQUIRED_PERCENT,
    GoldenQuestion,
    grounded_rate,
    validate_golden,
)
from tap.quality.web_origin import web_origin

OBSERVATIONS_SCHEMA_VERSION = "graph-relation-observations-v1"
REPORT_SCHEMA_VERSION = "chat-relation-report-v1"
ANSWER_PATH = "chat-agent-loop"
GRAPH_RELATIONS_TOOL = "graph.relations"
TOOL_CALL_REQUIRED_PERCENT = REQUIRED_PERCENT
_NON_ANSWER_OPERATIONS = ("embed", "rerank")
_AGENT_TOOLS_ATTRIBUTE = "tap.agent.tools"
_TURN_TERMINAL_EVENTS = {
    "turn.completed": "completed",
    "turn.abstained": "abstained",
    "turn.failed": "failed",
    "turn.canceled": "canceled",
}
_CONVERSATION_TERMINAL_EVENT = "conversation.turn.completed"
_STEP_STATUSES = frozenset({"running", "completed", "rejected", "failed"})


def _load_script(module_name: str, filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Reuse the single-shot evaluator's scoring and the candidate runner's citation
# flattening so both answer paths are scored by exactly the same rules.
_EVALUATOR = _load_script("evaluate_graph_relations", "evaluate-graph-relations.py")
_CANDIDATE = _load_script(
    "run_graph_relations_candidate", "run-graph-relations-candidate.py"
)


def flatten_citation(citation: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten an HTTP/SSE citation's nested `edge` facts onto the citation."""
    return dict(_CANDIDATE._flatten_http_citation(citation))


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# --- SSE parsing ------------------------------------------------------------------


def _parse_frame(frame: str) -> dict[str, Any] | None:
    data = "\n".join(
        line[5:].lstrip() for line in frame.split("\n") if line.startswith("data:")
    )
    if not data:
        return None
    try:
        value = json.loads(data)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("sequence"), int)
        or not isinstance(value.get("turnId"), str)
        or not isinstance(value.get("event"), dict)
    ):
        return None
    return value


def parse_sse(text: str) -> tuple[list[dict[str, Any]], str]:
    """Parse every complete SSE frame in `text`; return envelopes and the partial tail."""
    pending = text.replace("\r\n", "\n")
    envelopes: list[dict[str, Any]] = []
    boundary = pending.find("\n\n")
    while boundary >= 0:
        parsed = _parse_frame(pending[:boundary])
        if parsed is not None:
            envelopes.append(parsed)
        pending = pending[boundary + 2 :]
        boundary = pending.find("\n\n")
    return envelopes, pending


# --- Turn reduction ---------------------------------------------------------------


@dataclass(frozen=True)
class TurnOutcome:
    terminal: str | None
    """Terminal event type seen (`turn.*` wins over `conversation.turn.completed`)."""
    state: str | None
    """completed / abstained / failed / canceled, or timeout / error for a failed ask."""
    final: bool
    """A `turn.*` terminal event was seen (the authoritative answer, if any, is in)."""
    answer: Mapping[str, Any] | None
    steps: tuple[Mapping[str, Any], ...]
    last_sequence: int
    problem: str | None


EMPTY_OUTCOME = TurnOutcome(None, None, False, None, (), 0, None)


def reduce_turn(envelopes: Iterable[Mapping[str, Any]], turn_id: str) -> TurnOutcome:
    """Reduce one Turn's stream envelopes, mirroring the web stream reducer:
    `task.step` upserts by `stepId`; `answer.draft` frames are ignored and never
    advance the sequence. The first `turn.*` terminal event is authoritative; a
    `conversation.turn.completed` only sets the state (from `payload.outcome`)
    until one arrives. A legacy `turn.completed` without an answer is
    `completed` with no answer."""
    terminal: str | None = None
    state: str | None = None
    final = False
    answer: Mapping[str, Any] | None = None
    problem: str | None = None
    steps: dict[str, Mapping[str, Any]] = {}
    last_sequence = 0
    for envelope in envelopes:
        event = envelope.get("event")
        if not isinstance(event, Mapping):
            continue
        event_type = event.get("type")
        payload = event.get("payload")
        if event_type == "answer.draft" or not isinstance(payload, Mapping):
            continue
        sequence = envelope.get("sequence")
        if isinstance(sequence, int):
            last_sequence = max(last_sequence, sequence)
        if envelope.get("turnId") != turn_id or final:
            continue
        if event_type == "task.step":
            step_id = payload.get("stepId")
            if (
                isinstance(step_id, str)
                and isinstance(payload.get("tool"), str)
                and payload.get("status") in _STEP_STATUSES
            ):
                steps[step_id] = dict(payload)
        elif isinstance(event_type, str) and event_type in _TURN_TERMINAL_EVENTS:
            terminal = event_type
            state = _TURN_TERMINAL_EVENTS[event_type]
            final = True
            raw_answer = payload.get("answer")
            if isinstance(raw_answer, Mapping):
                answer = raw_answer
            raw_problem = payload.get("problem")
            if isinstance(raw_problem, Mapping) and isinstance(
                raw_problem.get("title"), str
            ):
                problem = raw_problem["title"]
        elif event_type == _CONVERSATION_TERMINAL_EVENT and terminal is None:
            terminal = _CONVERSATION_TERMINAL_EVENT
            outcome = payload.get("outcome")
            state = outcome if isinstance(outcome, str) else None
    return TurnOutcome(
        terminal=terminal,
        state=state,
        final=final,
        answer=answer,
        steps=tuple(steps.values()),
        last_sequence=last_sequence,
        problem=problem,
    )


# --- Trace facts ------------------------------------------------------------------


def answer_models(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Distinct (alias, upstream model, provider) of the Turn's successful answer
    model calls -- every `modelCalls` entry with status `ok` whose operation is not
    an embedding or rerank call (seed retrieval runs before the model node, so the
    first call is usually an embedding). Streamed calls may carry no
    `upstreamModel` (it is resolved from cached LiteLLM routes only)."""
    models: list[dict[str, Any]] = []
    for call in trace.get("modelCalls", ()) or ():
        if not isinstance(call, Mapping) or call.get("status") != "ok":
            continue
        operation = str(call.get("operation", "")).lower()
        if any(marker in operation for marker in _NON_ANSWER_OPERATIONS):
            continue
        identity = {
            "alias": call.get("modelName"),
            "upstreamModel": call.get("upstreamModel"),
            "provider": call.get("provider"),
        }
        if identity not in models:
            models.append(identity)
    return models


def offered_tools(trace: Mapping[str, Any]) -> list[str] | None:
    """The Turn's visible tool list (`tap.agent.tools` span attribute), or `None`
    when the trace does not record it."""
    found: list[str] | None = None
    for span in trace.get("spans", ()) or ():
        attributes = span.get("attributes") if isinstance(span, Mapping) else None
        if not isinstance(attributes, Mapping):
            continue
        value = attributes.get(_AGENT_TOOLS_ATTRIBUTE)
        if isinstance(value, str):
            tools = [item for item in value.split(",") if item]
            if found is None or len(tools) > len(found):
                found = tools
    return found


# --- Observation building ---------------------------------------------------------


def golden_questions(golden_json: object) -> tuple[GoldenQuestion, ...]:
    return validate_golden(golden_json).questions


def revision_ids_for(
    question: GoldenQuestion,
    corpus_value: Mapping[str, Any],
    published: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Map a question's manifest `expectedSources` to the currently published
    source revisions the web app would freeze (`published-sources[].revisionId`)."""
    by_manifest_id = {
        str(item.get("id")): str(item.get("sourceId"))
        for item in corpus_value.get("sources", ())
        if isinstance(item, Mapping)
    }
    missing = [
        source for source in question.expected_sources if source not in by_manifest_id
    ]
    if missing:
        raise ValueError(f"corpus.json is missing sources for manifest ids {missing!r}")
    published_revisions = {
        str(item.get("sourceId")): str(item.get("revisionId"))
        for item in published
        if isinstance(item, Mapping)
    }
    revisions: list[str] = []
    for manifest_id in question.expected_sources:
        source_id = by_manifest_id[manifest_id]
        if source_id not in published_revisions:
            raise ValueError(
                f"question {question.id}: source {manifest_id} ({source_id}) is not "
                "published; publish it in Library before running the chat check"
            )
        revisions.append(published_revisions[source_id])
    return tuple(revisions)


def answer_entry(
    *, question_id: str, locale: str, outcome: TurnOutcome, latency_ms: int
) -> dict[str, Any]:
    answer = outcome.answer
    citations = (
        [
            flatten_citation(item)
            for item in answer.get("citations", ())
            if isinstance(item, Mapping)
        ]
        if answer is not None
        else []
    )
    relation_labels = sorted(
        {
            str(item.get("evidenceLabel"))
            for item in citations
            if item.get("kind") == "edge"
            and RELATION_CITATION_LABEL_PATTERN.fullmatch(
                str(item.get("evidenceLabel", ""))
            )
        }
    )
    claims = (
        [
            {
                "text": claim.get("text"),
                "citationIds": list(claim.get("citationIds", ())),
            }
            for claim in answer.get("claims", ())
            if isinstance(claim, Mapping)
        ]
        if answer is not None
        else []
    )
    return {
        "questionId": question_id,
        "group": "golden",
        "locale": locale,
        "outcome": outcome.state,
        "terminalEvent": outcome.terminal,
        "error": outcome.problem,
        "abstained": answer is None or answer.get("abstained") is True,
        "abstentionReason": answer.get("abstentionReason") if answer else None,
        "answerKind": answer.get("answerKind") if answer else None,
        "graphContextStatus": answer.get("graphContextStatus") if answer else None,
        "graphRelationsCalled": any(
            step.get("tool") == GRAPH_RELATIONS_TOOL
            and step.get("status") == "completed"
            for step in outcome.steps
        ),
        "toolSteps": [
            {
                "tool": step.get("tool"),
                "status": step.get("status"),
                "toolRound": step.get("toolRound"),
                "durationMs": step.get("durationMs"),
            }
            for step in outcome.steps
        ],
        "relationCitationLabels": relation_labels,
        "claims": claims,
        "citations": citations,
        "latencyMs": latency_ms,
    }


def assemble_observations(
    *,
    golden_json: Mapping[str, Any],
    corpus_value: Mapping[str, Any],
    answers: list[dict[str, Any]],
    graph_version: str | None,
    model_alias: str,
    actual_model: str | None,
    agent_revision_id: str | None,
    started_at: str,
    finished_at: str | None,
    complete: bool = True,
) -> dict[str, Any]:
    return {
        "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
        "answerPath": ANSWER_PATH,
        "executionMode": "real",
        "model": {"alias": model_alias, "actual": actual_model},
        "agentRevisionId": agent_revision_id,
        "goldenDigest": canonical_digest(dict(golden_json)),
        "corpusDigest": corpus_value.get("manifestDigest"),
        "graphVersion": graph_version,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "complete": complete,
        "answers": answers,
    }


# --- Report and gate --------------------------------------------------------------


def _selected_golden(
    golden_json: Mapping[str, Any], question_ids: Sequence[str] | None
) -> dict[str, Any]:
    if not question_ids:
        return dict(golden_json)
    questions = [
        item
        for item in golden_json.get("questions", ())
        if isinstance(item, Mapping) and item.get("id") in question_ids
    ]
    known = {item.get("id") for item in questions}
    unknown = sorted(set(question_ids) - known)
    if unknown:
        raise ValueError(f"unknown golden question ids {unknown!r}")
    return {**golden_json, "questions": questions}


def _golden_answers(
    observations: Mapping[str, Any], question_ids: set[str]
) -> list[Mapping[str, Any]]:
    return [
        answer
        for answer in observations.get("answers", ())
        if isinstance(answer, Mapping)
        and answer.get("group") == "golden"
        and answer.get("questionId") in question_ids
    ]


def _wrong_count(report: Mapping[str, Any]) -> int:
    return sum(1 for row in report["questions"] if row["status"] == "wrong")


def _require_current(
    golden_json: Mapping[str, Any], observations: Mapping[str, Any]
) -> None:
    """Observations (including `--score-only` input) must belong to this golden set."""
    if observations.get("goldenDigest") != canonical_digest(dict(golden_json)):
        raise ValueError(
            "observations goldenDigest does not match the current golden set"
        )
    corpus = golden_json.get("corpus")
    digest = corpus.get("digest") if isinstance(corpus, Mapping) else None
    if observations.get("corpusDigest") != digest:
        raise ValueError(
            "observations corpusDigest does not match the golden corpus.digest"
        )


def _normalized_model(value: object) -> str:
    return str(value).strip().lower() if value is not None else ""


def _same_model(left: str, right: str) -> bool:
    return bool(left) and (
        left == right or left.endswith("/" + right) or right.endswith("/" + left)
    )


_UNASKED_OUTCOMES = frozenset({"timeout", "error"})


def model_check(
    answers: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    *,
    allow_alias_only: bool = False,
) -> dict[str, Any]:
    """Every answered question's model calls must use the baseline's alias and its
    (normalized) provider model id (`answer_provenance.provider_model_id` in the
    single-shot run). A question with any call lacking an upstream model is
    `unresolved`: its provider model identity cannot be checked, which makes the
    baseline gate incomparable unless `allow_alias_only` accepts alias-only identity.
    A question that was never answered (`timeout`/`error`) has no model to compare."""
    baseline_model = baseline.get("model")
    alias = baseline_model.get("alias") if isinstance(baseline_model, Mapping) else None
    actual = _normalized_model(
        baseline_model.get("actual") if isinstance(baseline_model, Mapping) else None
    )
    mismatches: list[dict[str, Any]] = []
    unresolved: list[str] = []
    if not actual:
        mismatches.append(
            {"questionId": None, "reason": "baseline model.actual is blank"}
        )
    for answer in answers:
        question_id = answer.get("questionId")
        if answer.get("outcome") in _UNASKED_OUTCOMES:
            continue
        models = answer.get("answerModels") or []
        if not models:
            mismatches.append(
                {"questionId": question_id, "reason": "no answer model call recorded"}
            )
            continue
        for model in models:
            if model.get("alias") != alias:
                mismatches.append(
                    {
                        "questionId": question_id,
                        "reason": f"alias {model.get('alias')!r} != baseline {alias!r}",
                    }
                )
            upstream = _normalized_model(model.get("upstreamModel"))
            if not upstream:
                if question_id not in unresolved:
                    unresolved.append(str(question_id))
            elif actual and not _same_model(upstream, actual):
                mismatches.append(
                    {
                        "questionId": question_id,
                        "reason": f"upstream model {upstream!r} != baseline {actual!r}",
                    }
                )
    return {
        "baselineAlias": alias,
        "baselineActual": actual or None,
        "mismatches": mismatches,
        "unresolved": unresolved,
        "aliasOnlyAllowed": allow_alias_only,
        "passed": not mismatches and (allow_alias_only or not unresolved),
    }


def _comparability_reasons(
    observations: Mapping[str, Any], baseline: Mapping[str, Any]
) -> list[str]:
    reasons: list[str] = []
    if baseline.get("executionMode") != "real":
        reasons.append("baseline executionMode must be 'real'")
    if baseline.get("graphReasoning") is not True:
        reasons.append(
            "baseline graphReasoning must be True (the single-shot relation run, not "
            "the TAPPER_GRAPH_REASONING=0 regression baseline)"
        )
    for field in ("goldenDigest", "corpusDigest", "graphVersion"):
        if baseline.get(field) is None or baseline.get(field) != observations.get(
            field
        ):
            reasons.append(f"baseline {field} differs from the chat observations")
    return reasons


def _ratio(count: int, total: int, required_percent: int) -> dict[str, object]:
    return {
        "count": count,
        "total": total,
        "actual": f"{count}/{total}",
        "required": f">={required_percent}%",
        "passed": total > 0 and count * 100 >= total * required_percent,
    }


def build_report(
    golden_json: Mapping[str, Any],
    observations: Mapping[str, Any],
    *,
    question_ids: Sequence[str] | None = None,
    baseline: Mapping[str, Any] | None = None,
    allow_alias_only: bool = False,
) -> dict[str, Any]:
    """Score chat observations (and optionally a single-shot baseline) with the
    shared evaluator over the selected golden questions; apply the gate rule."""
    _require_current(golden_json, observations)
    golden_subset = _selected_golden(golden_json, question_ids)
    selected_ids = {str(item.get("id")) for item in golden_subset.get("questions", ())}
    chat_report = _EVALUATOR.evaluate(golden_subset, observations)
    answers = _golden_answers(observations, selected_ids)
    by_question = {str(answer.get("questionId")): answer for answer in answers}

    questions = []
    for row in chat_report["questions"]:
        answer = by_question.get(row["id"], {})
        questions.append(
            {
                **row,
                "graphRelationsCalled": answer.get("graphRelationsCalled") is True,
                "graphRelationsOffered": answer.get("graphRelationsOffered"),
                "relationCitationLabels": answer.get("relationCitationLabels", []),
                "answerKind": answer.get("answerKind"),
                "abstained": answer.get("abstained", True),
                "outcome": answer.get("outcome"),
                "latencyMs": answer.get("latencyMs"),
            }
        )

    called = sum(1 for row in questions if row["graphRelationsCalled"])
    tool_rate = _ratio(called, len(questions), TOOL_CALL_REQUIRED_PERCENT)
    grounded, grounded_total = grounded_rate(answers)
    latencies = [
        int(answer["latencyMs"])
        for answer in answers
        if isinstance(answer.get("latencyMs"), int)
    ]
    summary = chat_report["summary"]
    wrong = _wrong_count(chat_report)

    baseline_section: dict[str, Any] | None = None
    models: dict[str, Any] | None = None
    if baseline is None:
        gate: dict[str, Any] = {"mode": "report-only", "checks": [], "passed": True}
    else:
        models = model_check(answers, baseline, allow_alias_only=allow_alias_only)
        reasons = _comparability_reasons(observations, baseline) + [
            f"model mismatch for {item['questionId']}: {item['reason']}"
            for item in models["mismatches"]
        ]
        if models["unresolved"] and not allow_alias_only:
            reasons.append(
                "provider model unresolved (no upstreamModel on an answer call) for "
                f"{', '.join(models['unresolved'])}; pass --allow-alias-only-model-check "
                "(TAP_CHAT_RELATIONS_ALLOW_ALIAS_ONLY=1) to accept alias-only identity"
            )
        baseline_report = _EVALUATOR.evaluate(golden_subset, baseline)
        baseline_summary = baseline_report["summary"]
        baseline_wrong = _wrong_count(baseline_report)
        baseline_section = {
            "observationsDigest": canonical_digest(dict(baseline)),
            "summary": baseline_summary,
            "wrongCount": baseline_wrong,
            "questions": baseline_report["questions"],
        }
        if reasons:
            gate = {
                "mode": "incomparable",
                "reasons": reasons,
                "checks": [],
                "passed": False,
            }
        else:
            checks = [
                {
                    "id": "relationPassRateNotBelowBaseline",
                    "actual": summary["actual"],
                    "baseline": baseline_summary["actual"],
                    "passed": int(summary["passedCount"])
                    * int(baseline_summary["total"])
                    >= int(baseline_summary["passedCount"]) * int(summary["total"]),
                },
                {
                    "id": "wrongEdgeQuestionsNotAboveBaseline",
                    "actual": wrong,
                    "baseline": baseline_wrong,
                    "passed": wrong <= baseline_wrong,
                },
                {
                    "id": "graphRelationsToolCallRate",
                    "actual": tool_rate["actual"],
                    "required": tool_rate["required"],
                    "passed": tool_rate["passed"],
                },
            ]
            gate = {
                "mode": "baseline",
                "checks": checks,
                # Questions gated on alias-only model identity (opt-in).
                "aliasOnlyModelCheck": list(models["unresolved"]),
                "passed": all(check["passed"] for check in checks),
            }

    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "answerPath": ANSWER_PATH,
        "goldenDigest": canonical_digest(dict(golden_json)),
        "observationsDigest": canonical_digest(dict(observations)),
        "observationsComplete": observations.get("complete", True),
        "graphVersion": observations.get("graphVersion"),
        "model": observations.get("model"),
        "agentRevisionId": observations.get("agentRevisionId"),
        "questionIds": sorted(selected_ids),
        "questions": questions,
        "summary": summary,
        "wrongCount": wrong,
        "graphRelationsToolCallRate": tool_rate,
        "groundedRate": {"grounded": grounded, "total": grounded_total},
        "unansweredCount": sum(
            1 for answer in answers if answer.get("outcome") in _UNASKED_OUTCOMES
        ),
        "latencyMs": {
            "p50": int(statistics.median(latencies)) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "modelCheck": models,
        "baseline": baseline_section,
        "gate": gate,
        "passed": bool(gate["passed"]),
    }


def exit_code(report: Mapping[str, Any]) -> int:
    gate = report.get("gate")
    if isinstance(gate, Mapping) and gate.get("mode") == "incomparable":
        return 2
    return 0 if report.get("passed") else 1


# --- HTTP layer -------------------------------------------------------------------


def runtime_project_id(api: str) -> str:
    request = urllib.request.Request(f"{api}/api/v1/runtime-mode")  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return str(json.loads(response.read().decode("utf-8"))["projectId"])


class TapperApi:
    """The handful of Tapper API calls the web app makes for one chat Turn."""

    def __init__(self, api: str, project_id: str) -> None:
        self._project_root = (
            f"{api.rstrip('/')}/api/v1/projects/{urllib.parse.quote(project_id)}"
        )

    def _get(self, path: str) -> Any:
        request = urllib.request.Request(f"{self._project_root}{path}")  # noqa: S310
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def default_model_alias(self) -> str:
        return str(self._get("/ai/models")["defaultAlias"])

    def published_sources(self) -> list[dict[str, Any]]:
        items = self._get("/knowledge/published-sources").get("items", [])
        return [item for item in items if isinstance(item, dict)]

    def graph_version(self) -> str | None:
        body = self._get("/knowledge/graph/project")
        version = body.get("graphVersion")
        return str(version) if version is not None else None

    def turn_trace(self, conversation_id: str, turn_id: str) -> dict[str, Any]:
        return dict(
            self._get(
                f"/conversations/{urllib.parse.quote(conversation_id)}"
                f"/turns/{urllib.parse.quote(turn_id)}/trace"
            )
        )

    def create_conversation(
        self,
        *,
        message: str,
        model_alias: str,
        source_revision_ids: Sequence[str],
        agent_revision_id: str | None,
    ) -> dict[str, Any]:
        body = {
            "message": message,
            "modelAlias": model_alias,
            "sourceRevisionIds": list(source_revision_ids),
            "documentRevisionIds": [],
            "agentRevisionId": agent_revision_id,
            "skillRevisionIds": [],
        }
        request = urllib.request.Request(  # noqa: S310
            f"{self._project_root}/conversations",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": str(uuid.uuid4()),
                "Origin": web_origin(),
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return dict(json.loads(response.read().decode("utf-8")))

    def open_stream(self, conversation_id: str, last_sequence: int) -> Iterable[bytes]:
        headers = {"Accept": "text/event-stream"}
        if last_sequence > 0:
            headers["Last-Event-ID"] = str(last_sequence)
        request = urllib.request.Request(  # noqa: S310
            f"{self._project_root}/conversations/"
            f"{urllib.parse.quote(conversation_id)}/stream",
            headers=headers,
        )
        response = urllib.request.urlopen(request, timeout=60)  # noqa: S310

        def chunks() -> Iterable[bytes]:
            with response:
                while True:
                    line = response.readline()
                    if not line:
                        return
                    yield line

        return chunks()


StreamOpener = Callable[[str, int], Iterable[bytes]]


class TurnTimeout(TimeoutError):
    """The Turn did not reach a terminal event before the deadline."""

    def __init__(self, turn_id: str, outcome: TurnOutcome) -> None:
        super().__init__(f"turn {turn_id} did not finish before the deadline")
        self.outcome = outcome


def follow_turn(
    open_stream: StreamOpener,
    conversation_id: str,
    turn_id: str,
    *,
    deadline_seconds: float,
    reconnect_delay_seconds: float = 1.0,
) -> TurnOutcome:
    """Follow the conversation SSE stream until `turn_id` is terminal, resuming
    from the last authoritative sequence (`Last-Event-ID`) when the stream drops.

    A 4xx when opening the stream is raised at once (retrying cannot fix it); 5xx,
    connection errors and read timeouts reconnect until the deadline, which raises
    `TurnTimeout` carrying the partial outcome. A `conversation.turn.completed`
    without its `turn.*` event is accepted once the stream closes."""
    deadline = time.monotonic() + deadline_seconds
    envelopes: list[dict[str, Any]] = []
    outcome = EMPTY_OUTCOME
    while True:
        before = outcome.last_sequence
        pending = ""
        try:
            for chunk in open_stream(conversation_id, outcome.last_sequence):
                parsed, pending = parse_sse(pending + chunk.decode("utf-8"))
                envelopes.extend(parsed)
                if parsed:
                    outcome = reduce_turn(envelopes, turn_id)
                    if outcome.final:
                        return outcome
                if time.monotonic() > deadline:
                    break
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                raise
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
            pass
        outcome = reduce_turn(envelopes, turn_id)
        if outcome.terminal is not None:
            return outcome
        if time.monotonic() > deadline:
            raise TurnTimeout(turn_id, outcome)
        if outcome.last_sequence == before:
            time.sleep(reconnect_delay_seconds)


# --- Real run ---------------------------------------------------------------------


def _describe_error(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} {error.reason}"
    return f"{type(error).__name__}: {error}"


def ask_questions(
    api: Any,
    questions: Sequence[GoldenQuestion],
    revisions: Mapping[str, Sequence[str]],
    *,
    model_alias: str,
    agent_revision_id: str | None,
    turn_timeout_seconds: float,
    on_answer: Callable[[dict[str, Any]], None],
) -> list[dict[str, Any]]:
    """Ask each question as a new Conversation; a rejected request or a timed-out
    Turn is recorded (outcome `error`/`timeout`, scored as not passed) and the run
    moves on. `on_answer` sees every entry as soon as it exists."""
    answers: list[dict[str, Any]] = []
    for question in questions:
        started = time.monotonic()
        conversation_id: str | None = None
        turn_id: str | None = None
        try:
            accepted = api.create_conversation(
                message=question.question,
                model_alias=model_alias,
                source_revision_ids=revisions[question.id],
                agent_revision_id=agent_revision_id,
            )
            conversation_id = str(accepted["conversationId"])
            turn_id = str(accepted["turnId"])
            outcome = follow_turn(
                api.open_stream,
                conversation_id,
                turn_id,
                deadline_seconds=turn_timeout_seconds,
            )
        except TurnTimeout as error:
            outcome = replace(error.outcome, state="timeout", problem=str(error))
        except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
            outcome = replace(
                EMPTY_OUTCOME, state="error", problem=_describe_error(error)
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        entry = answer_entry(
            question_id=question.id,
            locale=question.locale,
            outcome=outcome,
            latency_ms=latency_ms,
        )
        entry["conversationId"] = conversation_id
        entry["turnId"] = turn_id
        entry["answerModels"] = []
        entry["offeredTools"] = None
        if conversation_id is not None and turn_id is not None:
            try:
                trace = api.turn_trace(conversation_id, turn_id)
                entry["answerModels"] = answer_models(trace)
                entry["offeredTools"] = offered_tools(trace)
            except (urllib.error.URLError, OSError, KeyError, ValueError):
                pass
        tools = entry["offeredTools"]
        entry["graphRelationsOffered"] = (
            GRAPH_RELATIONS_TOOL in tools if tools is not None else None
        )
        answers.append(entry)
        on_answer(entry)
        print(
            f"{question.id}: {entry['outcome']} graphRelationsCalled="
            f"{entry['graphRelationsCalled']} R={entry['relationCitationLabels']} "
            f"{latency_ms}ms",
            file=sys.stderr,
        )
    return answers


def _first_upstream(answers: Sequence[Mapping[str, Any]]) -> str | None:
    for answer in answers:
        for model in answer.get("answerModels") or ():
            if model.get("upstreamModel"):
                return str(model["upstreamModel"])
    return None


def run_check(
    *,
    api_url: str,
    project_id: str | None,
    golden_json: Mapping[str, Any],
    corpus_value: Mapping[str, Any],
    question_ids: Sequence[str] | None,
    model_alias: str | None,
    agent_revision_id: str | None,
    turn_timeout_seconds: float,
    write_observations: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    started_at = _now_iso()
    golden_set = validate_golden(golden_json, require_human=True)
    corpus_digest = golden_json.get("corpus", {}).get("digest")
    if corpus_value.get("manifestDigest") != corpus_digest:
        raise ValueError(
            "corpus.json manifestDigest does not match golden corpus.digest"
        )
    selected = [
        question
        for question in golden_set.questions
        if not question_ids or question.id in question_ids
    ]
    unknown = sorted(set(question_ids or ()) - {question.id for question in selected})
    if unknown:
        raise ValueError(f"unknown golden question ids {unknown!r}")

    api = TapperApi(api_url, project_id or runtime_project_id(api_url))
    alias = model_alias or api.default_model_alias()
    published = api.published_sources()
    # Resolve every question's frozen sources before any model call (fail closed).
    revisions = {
        question.id: revision_ids_for(question, corpus_value, published)
        for question in selected
    }
    graph_version = api.graph_version()

    def observations(answers: list[dict[str, Any]], complete: bool) -> dict[str, Any]:
        return assemble_observations(
            golden_json=golden_json,
            corpus_value=corpus_value,
            answers=answers,
            graph_version=graph_version,
            model_alias=alias,
            actual_model=_first_upstream(answers),
            agent_revision_id=agent_revision_id,
            started_at=started_at,
            finished_at=_now_iso() if complete else None,
            complete=complete,
        )

    progress: list[dict[str, Any]] = []

    def on_answer(entry: dict[str, Any]) -> None:
        progress.append(entry)
        write_observations(observations(list(progress), False))

    answers = ask_questions(
        api,
        selected,
        revisions,
        model_alias=alias,
        agent_revision_id=agent_revision_id,
        turn_timeout_seconds=turn_timeout_seconds,
        on_answer=on_answer,
    )
    final = observations(answers, True)
    write_observations(final)
    return final


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline-observations", type=Path, default=None)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--model-alias", default=None)
    parser.add_argument(
        "--agent-revision-id",
        default="",
        help="opt-in Agent revision (e.g. validation-knowledge-agent-v3); the default "
        "sends no Agent, like the web app before a user picks one",
    )
    parser.add_argument(
        "--questions",
        default="",
        help="comma-separated golden question ids (default: every golden question)",
    )
    parser.add_argument("--turn-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--allow-alias-only-model-check",
        action="store_true",
        default=os.environ.get("TAP_CHAT_RELATIONS_ALLOW_ALIAS_ONLY") == "1",
        help="accept alias-only model identity when an answer call recorded no "
        "upstream model (default: such a baseline-gated run is incomparable, exit 2); "
        "env TAP_CHAT_RELATIONS_ALLOW_ALIAS_ONLY=1",
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="re-score an existing --observations file without calling the API",
    )
    arguments = parser.parse_args()
    question_ids = tuple(
        item.strip() for item in arguments.questions.split(",") if item.strip()
    )

    try:
        golden_json = _load_json(arguments.golden)
        if arguments.score_only:
            observations = _load_json(arguments.observations)
        else:
            observations = run_check(
                api_url=arguments.api,
                project_id=arguments.project_id,
                golden_json=golden_json,
                corpus_value=_load_json(arguments.corpus),
                question_ids=question_ids or None,
                model_alias=arguments.model_alias,
                agent_revision_id=arguments.agent_revision_id or None,
                turn_timeout_seconds=arguments.turn_timeout_seconds,
                write_observations=lambda value: _write_json(
                    arguments.observations, value
                ),
            )
        baseline = (
            _load_json(arguments.baseline_observations)
            if arguments.baseline_observations is not None
            else None
        )
        report = build_report(
            golden_json,
            observations,
            question_ids=question_ids or None,
            baseline=baseline,
            allow_alias_only=arguments.allow_alias_only_model_check,
        )
    except (ValueError, OSError, KeyError, urllib.error.URLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    _write_json(arguments.report, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report["gate"]["mode"] == "incomparable":
        print(
            "error: baseline is not comparable: "
            + "; ".join(report["gate"]["reasons"]),
            file=sys.stderr,
        )
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
