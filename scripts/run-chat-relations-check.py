#!/usr/bin/env python3
"""Opt-in, real-model check of relation-answer quality through the Tapper CHAT path.

Since LangGraph PR 5, chat relation analysis runs only when the model calls the
`graph.relations` tool inside the `model <-> tools` agent loop. The single-shot
gate (`make quality-graph-relations-real`, `run-graph-relations-candidate.py --mode
real`) never enters that loop, so this script asks each relation question in the
Graph golden set through the real HTTP API exactly as the web app does -- a new
durable Conversation/Turn per question (`POST /conversations`, the exact web
`Origin`, frozen published source revisions, the built-in Agent revision) -- then
follows the conversation SSE stream to the Turn's terminal event and records:

- whether a `task.step` for `graph.relations` reached `completed`,
- the answer's R-labelled edge citations, answer kind and abstention,
- end-to-end latency (POST accepted -> terminal event).

Answers are written as `graph-relation-observations-v1` (plus chat-only fields)
and scored with `scripts/evaluate-graph-relations.py`'s own `evaluate()`, so the
`summary`/`questions` metrics mean exactly what they mean in the single-shot
report. `graphRelationsToolCallRate` is added on top.

Gate rule (exit code):

- without `--baseline-observations`: report-only, exit 0 once the report is written;
- with a single-shot real baseline (the `TAPPER_GRAPH_REASONING=1`
  `run-graph-relations-candidate.py --mode real` observations), the baseline must be
  comparable (same goldenDigest, corpusDigest, graphVersion, real execution,
  graphReasoning True, and the same actual model) or the run exits 2; then, scored
  over the same selected questions, the gate passes (exit 0) only when
  1. `relationPassRateNotBelowBaseline`: chat pass rate >= single-shot pass rate,
  2. `wrongEdgeQuestionsNotAboveBaseline`: chat `wrong` questions <= single-shot's,
  3. `graphRelationsToolCallRate`: the model called `graph.relations` on >= 80% of
     the questions (the same required percent the relation summary uses);
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from tap.modules.knowledge.ports.answers import RELATION_CITATION_LABEL_PATTERN
from tap.quality.evidence import canonical_digest
from tap.quality.graph_relations import (
    GoldenQuestion,
    grounded_rate,
    validate_golden,
)

OBSERVATIONS_SCHEMA_VERSION = "graph-relation-observations-v1"
REPORT_SCHEMA_VERSION = "chat-relation-report-v1"
ANSWER_PATH = "chat-agent-loop"
GRAPH_RELATIONS_TOOL = "graph.relations"
DEFAULT_AGENT_REVISION_ID = "validation-knowledge-agent-v3"
TOOL_CALL_REQUIRED_PERCENT = 80
_TURN_TERMINAL_EVENTS = frozenset(
    {"turn.completed", "turn.abstained", "turn.failed", "turn.canceled"}
)
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
    answer: Mapping[str, Any] | None
    steps: tuple[Mapping[str, Any], ...]
    last_sequence: int
    problem: str | None


def reduce_turn(envelopes: Iterable[Mapping[str, Any]], turn_id: str) -> TurnOutcome:
    """Reduce one Turn's stream envelopes, mirroring the web stream reducer:
    `task.step` upserts by `stepId`; `answer.draft` frames are ignored and never
    advance the sequence; the first terminal event wins."""
    terminal: str | None = None
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
        if envelope.get("turnId") != turn_id or terminal is not None:
            continue
        if event_type == "task.step":
            step_id = payload.get("stepId")
            if (
                isinstance(step_id, str)
                and isinstance(payload.get("tool"), str)
                and payload.get("status") in _STEP_STATUSES
            ):
                steps[step_id] = dict(payload)
        elif event_type in _TURN_TERMINAL_EVENTS:
            terminal = str(event_type)
            raw_answer = payload.get("answer")
            if isinstance(raw_answer, Mapping):
                answer = raw_answer
            raw_problem = payload.get("problem")
            if isinstance(raw_problem, Mapping) and isinstance(
                raw_problem.get("title"), str
            ):
                problem = raw_problem["title"]
        elif event_type == _CONVERSATION_TERMINAL_EVENT:
            terminal = _CONVERSATION_TERMINAL_EVENT
    return TurnOutcome(
        terminal=terminal,
        answer=answer,
        steps=tuple(steps.values()),
        last_sequence=last_sequence,
        problem=problem,
    )


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
        "outcome": outcome.terminal,
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
    finished_at: str,
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


def _require_comparable(
    observations: Mapping[str, Any], baseline: Mapping[str, Any]
) -> None:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(f"baseline is not comparable: {message}")

    require(baseline.get("executionMode") == "real", "baseline executionMode 'real'")
    require(
        baseline.get("graphReasoning") is True,
        "baseline graphReasoning must be True (the single-shot relation run, not "
        "the TAPPER_GRAPH_REASONING=0 regression baseline)",
    )
    for field in ("goldenDigest", "corpusDigest", "graphVersion"):
        require(
            baseline.get(field) is not None
            and baseline.get(field) == observations.get(field),
            f"{field} differs from the chat observations",
        )
    baseline_model = baseline.get("model")
    chat_model = observations.get("model")
    baseline_actual = (
        baseline_model.get("actual") if isinstance(baseline_model, Mapping) else None
    )
    chat_actual = chat_model.get("actual") if isinstance(chat_model, Mapping) else None
    require(
        isinstance(baseline_actual, str)
        and bool(baseline_actual)
        and baseline_actual == chat_actual,
        f"model.actual {baseline_actual!r} differs from chat {chat_actual!r}",
    )


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
) -> dict[str, Any]:
    """Score chat observations (and optionally a single-shot baseline) with the
    shared evaluator over the selected golden questions; apply the gate rule."""
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
    if baseline is None:
        gate: dict[str, Any] = {"mode": "report-only", "checks": [], "passed": True}
    else:
        _require_comparable(observations, baseline)
        baseline_report = _EVALUATOR.evaluate(golden_subset, baseline)
        baseline_summary = baseline_report["summary"]
        baseline_wrong = _wrong_count(baseline_report)
        checks = [
            {
                "id": "relationPassRateNotBelowBaseline",
                "actual": summary["actual"],
                "baseline": baseline_summary["actual"],
                "passed": int(summary["passedCount"]) * int(baseline_summary["total"])
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
            "passed": all(check["passed"] for check in checks),
        }
        baseline_section = {
            "observationsDigest": canonical_digest(dict(baseline)),
            "summary": baseline_summary,
            "wrongCount": baseline_wrong,
            "questions": baseline_report["questions"],
        }

    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "answerPath": ANSWER_PATH,
        "goldenDigest": canonical_digest(dict(golden_json)),
        "observationsDigest": canonical_digest(dict(observations)),
        "graphVersion": observations.get("graphVersion"),
        "model": observations.get("model"),
        "agentRevisionId": observations.get("agentRevisionId"),
        "questionIds": sorted(selected_ids),
        "questions": questions,
        "summary": summary,
        "wrongCount": wrong,
        "graphRelationsToolCallRate": tool_rate,
        "groundedRate": {"grounded": grounded, "total": grounded_total},
        "latencyMs": {
            "p50": int(statistics.median(latencies)) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "baseline": baseline_section,
        "gate": gate,
        "passed": bool(gate["passed"]),
    }


# --- HTTP layer -------------------------------------------------------------------


def _web_origin() -> str:
    """The web app's exact loopback Origin; the API rejects mutations without it."""
    host = os.environ.get("TAPPER_WEB_HOST", "127.0.0.1")
    port = os.environ.get("TAPPER_WEB_PORT", "5173")
    return f"http://{host}:{port}"


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

    def turn_upstream_models(self, conversation_id: str, turn_id: str) -> list[str]:
        trace = self._get(
            f"/conversations/{urllib.parse.quote(conversation_id)}"
            f"/turns/{urllib.parse.quote(turn_id)}/trace"
        )
        models = trace.get("summary", {}).get("upstreamModels", [])
        return [str(model) for model in models]

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
                "Origin": _web_origin(),
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


def follow_turn(
    open_stream: StreamOpener,
    conversation_id: str,
    turn_id: str,
    *,
    deadline_seconds: float,
    reconnect_delay_seconds: float = 1.0,
) -> TurnOutcome:
    """Follow the conversation SSE stream until `turn_id` is terminal, resuming
    from the last authoritative sequence (`Last-Event-ID`) when the stream drops."""
    deadline = time.monotonic() + deadline_seconds
    envelopes: list[dict[str, Any]] = []
    outcome = reduce_turn(envelopes, turn_id)
    while True:
        before = outcome.last_sequence
        pending = ""
        try:
            for chunk in open_stream(conversation_id, outcome.last_sequence):
                parsed, pending = parse_sse(pending + chunk.decode("utf-8"))
                envelopes.extend(parsed)
                if parsed:
                    outcome = reduce_turn(envelopes, turn_id)
                    if outcome.terminal is not None:
                        return outcome
                if time.monotonic() > deadline:
                    break
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
            pass
        outcome = reduce_turn(envelopes, turn_id)
        if outcome.terminal is not None:
            return outcome
        if time.monotonic() > deadline:
            raise TimeoutError(f"turn {turn_id} did not finish before the deadline")
        if outcome.last_sequence == before:
            time.sleep(reconnect_delay_seconds)


# --- Real run ---------------------------------------------------------------------


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

    answers: list[dict[str, Any]] = []
    actual_model: str | None = None
    for question in selected:
        started = time.monotonic()
        accepted = api.create_conversation(
            message=question.question,
            model_alias=alias,
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
        latency_ms = int((time.monotonic() - started) * 1000)
        entry = answer_entry(
            question_id=question.id,
            locale=question.locale,
            outcome=outcome,
            latency_ms=latency_ms,
        )
        entry["conversationId"] = conversation_id
        entry["turnId"] = turn_id
        try:
            entry["upstreamModels"] = api.turn_upstream_models(conversation_id, turn_id)
        except (urllib.error.URLError, ValueError, KeyError):
            entry["upstreamModels"] = []
        if actual_model is None and entry["upstreamModels"]:
            actual_model = entry["upstreamModels"][0]
        answers.append(entry)
        print(
            f"{question.id}: {entry['outcome']} graphRelationsCalled="
            f"{entry['graphRelationsCalled']} R={entry['relationCitationLabels']} "
            f"{latency_ms}ms",
            file=sys.stderr,
        )

    return assemble_observations(
        golden_json=golden_json,
        corpus_value=corpus_value,
        answers=answers,
        graph_version=graph_version,
        model_alias=alias,
        actual_model=actual_model,
        agent_revision_id=agent_revision_id,
        started_at=started_at,
        finished_at=_now_iso(),
    )


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
        default=DEFAULT_AGENT_REVISION_ID,
        help="Agent revision to run the Turn with; pass '' for the default chat path",
    )
    parser.add_argument(
        "--questions",
        default="",
        help="comma-separated golden question ids (default: every golden question)",
    )
    parser.add_argument("--turn-timeout-seconds", type=float, default=300.0)
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
            )
            _write_json(arguments.observations, observations)
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
        )
    except (ValueError, OSError, KeyError, urllib.error.URLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    _write_json(arguments.report, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
