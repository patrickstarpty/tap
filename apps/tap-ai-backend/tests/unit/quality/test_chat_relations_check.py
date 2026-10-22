"""Pure parts of `scripts/run-chat-relations-check.py`.

The script drives the real Tapper chat path (Conversation/Turn over HTTP, the
LangGraph `model <-> tools` loop) for each relation question in the Graph golden
set. These tests replay recorded SSE event sequences -- no API, no model.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tap.quality.evidence import canonical_digest

ROOT = Path(__file__).resolve().parents[5]


def _load() -> ModuleType:
    path = ROOT / "scripts" / "run-chat-relations-check.py"
    spec = importlib.util.spec_from_file_location("run_chat_relations_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load()

TURN = "turn-1"
GRAPH_VERSION = "7"
DIGEST = "sha256:" + "a" * 64


def _envelope(sequence: int, event_type: str, payload: dict[str, Any], turn: str = TURN):
    return {
        "eventId": f"e{sequence}",
        "sequence": sequence,
        "chatId": "c1",
        "turnId": turn,
        "occurredAt": "2026-10-22T00:00:00+00:00",
        "schemaVersion": 1,
        "event": {"type": event_type, "payload": payload},
    }


def _sse(*envelopes: dict[str, Any]) -> str:
    return "".join(
        f"id: {item['sequence']}\nevent: {item['event']['type']}\ndata: {json.dumps(item)}\n\n"
        for item in envelopes
    )


def _edge(label: str, subject: str, relation: str, obj: str) -> dict[str, Any]:
    return {
        "citationId": f"c-{label}",
        "evidenceLabel": label,
        "chunkId": f"chunk-{label}",
        "logicalChunkId": f"logical-{label}",
        "kind": "edge",
        "edge": {
            "edgeId": f"edge-{label}",
            "graphVersion": GRAPH_VERSION,
            "subject": {"nodeId": "n1", "label": subject},
            "object": {"nodeId": "n2", "label": obj},
            "relationType": relation,
            "relationLabel": relation.lower(),
        },
    }


def _chunk(label: str) -> dict[str, Any]:
    return {
        "citationId": f"c-{label}",
        "evidenceLabel": label,
        "chunkId": f"chunk-{label}",
        "logicalChunkId": f"logical-{label}",
        "kind": "chunk",
    }


def _answer(citations: list[dict[str, Any]], *, abstained: bool = False) -> dict[str, Any]:
    return {
        "answer": "Claims require a form [R1][S1].",
        "abstained": abstained,
        "abstentionReason": "insufficient_evidence" if abstained else None,
        "answerKind": "answer",
        "claims": [{"text": "Claims require a form", "citationIds": ["c-R1"]}],
        "citations": citations,
        "graphContextStatus": "APPLIED",
    }


def _step(sequence: int, status: str, tool: str = "graph.relations") -> dict[str, Any]:
    return _envelope(
        sequence,
        "task.step",
        {
            "stepId": f"{TURN}:r1:c0",
            "tool": tool,
            "status": status,
            "summary": "",
            "durationMs": 12,
            "toolRound": 1,
        },
    )


RELATION_TURN = (
    _envelope(1, "turn.started", {"state": "running"}),
    _step(2, "running"),
    _step(3, "completed"),
    _envelope(
        4,
        "turn.completed",
        {"answer": _answer([_edge("R1", "Claim", "REQUIRES", "Claim form"), _chunk("S1")])},
    ),
)


def _golden(*question_ids: str) -> dict[str, Any]:
    return {
        "schemaVersion": "graph-relation-golden-v1",
        "labeledBy": "reviewer",
        "labeledAt": "2026-10-06",
        "corpus": {"manifest": "manifest.json", "digest": DIGEST},
        "questions": [
            {
                "id": question_id,
                "question": f"What does a claim require ({question_id})?",
                "locale": "en",
                "expectedEntities": [{"label": "Claim"}, {"label": "Claim form"}],
                "expectedEdges": [
                    {"subject": "Claim", "relationType": "REQUIRES", "object": "Claim form"}
                ],
                "expectedSources": ["policy-01"],
            }
            for question_id in question_ids
        ],
    }


# --- SSE parsing -----------------------------------------------------------------------


def test_parse_sse_reads_every_complete_frame_and_keeps_the_partial_tail() -> None:
    text = _sse(*RELATION_TURN[:2]) + "id: 3\ndata: {"
    envelopes, rest = script.parse_sse(text)
    assert [item["sequence"] for item in envelopes] == [1, 2]
    assert rest == "id: 3\ndata: {"


def test_parse_sse_ignores_comments_and_malformed_data() -> None:
    text = ": keep-alive\n\ndata: not-json\n\n" + _sse(RELATION_TURN[0])
    envelopes, rest = script.parse_sse(text.replace("\n", "\r\n"))
    assert [item["sequence"] for item in envelopes] == [1]
    assert rest == ""


# --- turn reduction --------------------------------------------------------------------


def test_reduce_turn_upserts_steps_and_captures_the_terminal_answer() -> None:
    outcome = script.reduce_turn(RELATION_TURN, TURN)
    assert outcome.terminal == "turn.completed"
    assert outcome.last_sequence == 4
    assert [(step["tool"], step["status"]) for step in outcome.steps] == [
        ("graph.relations", "completed")
    ]
    assert outcome.answer is not None and outcome.answer["graphContextStatus"] == "APPLIED"


def test_reduce_turn_ignores_other_turns_and_draft_frames() -> None:
    events = (
        _envelope(1, "turn.started", {"state": "running"}, turn="other"),
        _envelope(2, "answer.draft", {"attempt": 1, "seq": 1, "text": "x"}),
        _envelope(3, "turn.completed", {"answer": _answer([])}, turn="other"),
    )
    outcome = script.reduce_turn(events, TURN)
    assert outcome.terminal is None
    assert outcome.steps == ()


def test_reduce_turn_records_a_failure_problem_title() -> None:
    events = (_envelope(1, "turn.failed", {"problem": {"title": "Model unavailable"}}),)
    outcome = script.reduce_turn(events, TURN)
    assert outcome.terminal == "turn.failed"
    assert outcome.problem == "Model unavailable"
    assert outcome.answer is None


def test_conversation_turn_completed_alone_takes_its_payload_outcome() -> None:
    events = (
        _envelope(
            1,
            "conversation.turn.completed",
            {"turnId": TURN, "outcome": "canceled"},
            turn=TURN,
        ),
    )
    outcome = script.reduce_turn(events, TURN)
    assert outcome.terminal == "conversation.turn.completed"
    assert outcome.state == "canceled"
    assert outcome.final is False


def test_a_later_turn_event_supplies_the_answer_after_conversation_completion() -> None:
    events = (
        _envelope(1, "conversation.turn.completed", {"turnId": TURN, "outcome": "completed"}),
        _envelope(2, "turn.completed", {"answer": _answer([])}),
    )
    outcome = script.reduce_turn(events, TURN)
    assert outcome.terminal == "turn.completed"
    assert outcome.state == "completed"
    assert outcome.final is True
    assert outcome.answer is not None


def test_legacy_turn_completed_without_an_answer_is_completed_but_abstained() -> None:
    outcome = script.reduce_turn((_envelope(1, "turn.completed", {"state": "completed"}),), TURN)
    assert outcome.state == "completed"
    entry = script.answer_entry(question_id="q-01", locale="en", outcome=outcome, latency_ms=1)
    assert entry["outcome"] == "completed"
    assert entry["abstained"] is True


# --- observation building --------------------------------------------------------------


def test_answer_entry_flattens_edge_citations_and_records_tool_use() -> None:
    entry = script.answer_entry(
        question_id="q-01",
        locale="en",
        outcome=script.reduce_turn(RELATION_TURN, TURN),
        latency_ms=1234,
    )
    assert entry["questionId"] == "q-01"
    assert entry["group"] == "golden"
    assert entry["abstained"] is False
    assert entry["answerKind"] == "answer"
    assert entry["graphRelationsCalled"] is True
    assert entry["relationCitationLabels"] == ["R1"]
    assert entry["latencyMs"] == 1234
    edge = entry["citations"][0]
    assert edge["subject"] == {"nodeId": "n1", "label": "Claim"}
    assert edge["relationType"] == "REQUIRES"
    assert "edge" not in edge


def test_a_running_or_rejected_graph_relations_step_is_not_a_completed_call() -> None:
    events = (
        _step(1, "running"),
        _envelope(2, "task.step", {**_step(2, "rejected")["event"]["payload"]}),
        _envelope(3, "turn.abstained", {"answer": _answer([], abstained=True)}),
    )
    entry = script.answer_entry(
        question_id="q-01", locale="en", outcome=script.reduce_turn(events, TURN), latency_ms=1
    )
    assert entry["graphRelationsCalled"] is False
    assert entry["abstained"] is True
    assert entry["abstentionReason"] == "insufficient_evidence"
    assert entry["toolSteps"] == [
        {"tool": "graph.relations", "status": "rejected", "toolRound": 1, "durationMs": 12}
    ]


def test_a_turn_without_an_answer_is_recorded_as_abstained() -> None:
    entry = script.answer_entry(
        question_id="q-01",
        locale="en",
        outcome=script.reduce_turn(
            (_envelope(1, "turn.failed", {"problem": {"title": "boom"}}),), TURN
        ),
        latency_ms=5,
    )
    assert entry["abstained"] is True
    assert entry["citations"] == []
    assert entry["outcome"] == "failed"
    assert entry["terminalEvent"] == "turn.failed"
    assert entry["error"] == "boom"


def test_revision_ids_map_manifest_ids_through_corpus_and_published_sources() -> None:
    question = script.golden_questions(_golden("q-01"))[0]
    corpus = {"sources": [{"id": "policy-01", "sourceId": "src_1", "revisionId": "old"}]}
    published = [{"sourceId": "src_1", "revisionId": "rev-9"}]
    assert script.revision_ids_for(question, corpus, published) == ("rev-9",)


def test_revision_ids_fail_closed_on_an_unpublished_source() -> None:
    question = script.golden_questions(_golden("q-01"))[0]
    corpus = {"sources": [{"id": "policy-01", "sourceId": "src_1", "revisionId": "r"}]}
    with pytest.raises(ValueError, match="not published"):
        script.revision_ids_for(question, corpus, [])
    with pytest.raises(ValueError, match="corpus.json"):
        script.revision_ids_for(question, {"sources": []}, [])


# --- report and gate -------------------------------------------------------------------


def _observations(golden: dict[str, Any], answers: list[dict[str, Any]], **overrides: Any):
    value = script.assemble_observations(
        golden_json=golden,
        corpus_value={"manifestDigest": DIGEST},
        answers=answers,
        graph_version=GRAPH_VERSION,
        model_alias="chat-default",
        actual_model="provider/model-x",
        agent_revision_id="validation-knowledge-agent-v3",
        started_at="s",
        finished_at="f",
    )
    value.update(overrides)
    return value


def _entry(question_id: str, *, called: bool, citations: list[dict[str, Any]]):
    events: list[dict[str, Any]] = [_step(1, "completed")] if called else []
    events.append(_envelope(2, "turn.completed", {"answer": _answer(citations)}))
    entry = script.answer_entry(
        question_id=question_id,
        locale="en",
        outcome=script.reduce_turn(events, TURN),
        latency_ms=100,
    )
    entry["answerModels"] = [
        {"alias": "chat-default", "upstreamModel": "openai/gpt-x", "provider": "openai"}
    ]
    return entry


GOOD = [_edge("R1", "Claim", "REQUIRES", "Claim form")]
WRONG = [_edge("R1", "Claim", "CONFLICTS_WITH", "Refund")]


def test_observations_use_the_shared_relation_observation_schema() -> None:
    golden = _golden("q-01")
    value = _observations(golden, [])
    assert value["schemaVersion"] == "graph-relation-observations-v1"
    assert value["answerPath"] == "chat-agent-loop"
    assert value["executionMode"] == "real"
    assert value["goldenDigest"] == canonical_digest(golden)
    assert value["corpusDigest"] == DIGEST
    assert value["model"] == {"alias": "chat-default", "actual": "provider/model-x"}


def test_report_only_scores_with_the_shared_evaluator_and_tool_call_rate() -> None:
    golden = _golden("q-01", "q-02")
    observations = _observations(
        golden,
        [
            _entry("q-01", called=True, citations=GOOD),
            _entry("q-02", called=False, citations=[_chunk("S1")]),
        ],
    )
    report = script.build_report(golden, observations)
    assert report["summary"]["passedCount"] == 1
    assert report["summary"]["total"] == 2
    assert report["graphRelationsToolCallRate"]["actual"] == "1/2"
    assert report["graphRelationsToolCallRate"]["passed"] is False
    assert report["groundedRate"] == {"grounded": 2, "total": 2}
    statuses = {
        row["id"]: (row["status"], row["graphRelationsCalled"]) for row in report["questions"]
    }
    assert statuses == {"q-01": ("pass", True), "q-02": ("miss", False)}
    assert report["gate"]["mode"] == "report-only"
    assert report["passed"] is True


def test_question_filter_limits_scoring_to_the_selected_questions() -> None:
    golden = _golden("q-01", "q-02")
    observations = _observations(golden, [_entry("q-01", called=True, citations=GOOD)])
    report = script.build_report(golden, observations, question_ids=("q-01",))
    assert report["summary"]["total"] == 1
    assert report["summary"]["passedCount"] == 1
    with pytest.raises(ValueError, match="unknown"):
        script.build_report(golden, observations, question_ids=("q-99",))


def _baseline(golden: dict[str, Any], answers: list[dict[str, Any]], **overrides: Any):
    value = {
        "schemaVersion": "graph-relation-observations-v1",
        "executionMode": "real",
        "extractionMode": "model",
        "model": {"alias": "chat-default", "actual": "openai/gpt-x"},
        "goldenDigest": canonical_digest(golden),
        "corpusDigest": DIGEST,
        "graphVersion": GRAPH_VERSION,
        "graphReasoning": True,
        "answers": answers,
    }
    value.update(overrides)
    return value


def _single_shot(question_id: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "questionId": question_id,
        "group": "golden",
        "abstained": False,
        "citations": [script.flatten_citation(item) for item in citations],
    }


def test_gate_passes_when_chat_matches_the_single_shot_baseline() -> None:
    golden = _golden("q-01", "q-02")
    chat = _observations(
        golden,
        [
            _entry("q-01", called=True, citations=GOOD),
            _entry("q-02", called=True, citations=[_chunk("S1")]),
        ],
    )
    baseline = _baseline(golden, [_single_shot("q-01", GOOD), _single_shot("q-02", GOOD)])
    report = script.build_report(golden, chat, baseline=baseline)
    assert report["gate"]["mode"] == "baseline"
    assert report["baseline"]["summary"]["passedCount"] == 2
    checks = {check["id"]: check["passed"] for check in report["gate"]["checks"]}
    # 1/2 chat vs 2/2 single shot: relation pass rate regressed.
    assert checks == {
        "relationPassRateNotBelowBaseline": False,
        "wrongEdgeQuestionsNotAboveBaseline": True,
        "graphRelationsToolCallRate": True,
    }
    assert report["passed"] is False

    baseline = _baseline(golden, [_single_shot("q-01", GOOD), _single_shot("q-02", [])])
    assert script.build_report(golden, chat, baseline=baseline)["passed"] is True


def test_gate_fails_on_more_wrong_edge_questions_than_the_baseline() -> None:
    golden = _golden("q-01", "q-02")
    chat = _observations(
        golden,
        [
            _entry("q-01", called=True, citations=GOOD),
            _entry("q-02", called=True, citations=WRONG),
        ],
    )
    baseline = _baseline(golden, [_single_shot("q-01", GOOD), _single_shot("q-02", [])])
    report = script.build_report(golden, chat, baseline=baseline)
    checks = {check["id"]: check["passed"] for check in report["gate"]["checks"]}
    assert checks["relationPassRateNotBelowBaseline"] is True
    assert checks["wrongEdgeQuestionsNotAboveBaseline"] is False
    assert report["passed"] is False


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"goldenDigest": "sha256:" + "b" * 64}, "goldenDigest"),
        ({"corpusDigest": "sha256:" + "b" * 64}, "corpusDigest"),
        ({"graphVersion": "8"}, "graphVersion"),
        ({"graphReasoning": False}, "graphReasoning"),
        ({"executionMode": "fake"}, "executionMode"),
        ({"model": {"alias": "chat-default", "actual": "openai/gpt-y"}}, "q-01"),
        ({"model": {"alias": "other-alias", "actual": "openai/gpt-x"}}, "alias"),
    ],
)
def test_an_incomparable_baseline_is_reported_and_not_gated(
    override: dict[str, Any], message: str
) -> None:
    golden = _golden("q-01")
    entry = _entry("q-01", called=True, citations=GOOD)
    entry["answerModels"] = [
        {"alias": "chat-default", "upstreamModel": "openai/gpt-x", "provider": "openai"}
    ]
    chat = _observations(golden, [entry])
    baseline = _baseline(golden, [_single_shot("q-01", GOOD)])
    baseline["model"] = {"alias": "chat-default", "actual": "openai/gpt-x"}
    baseline.update(override)
    report = script.build_report(golden, chat, baseline=baseline)
    assert report["gate"]["mode"] == "incomparable"
    assert report["passed"] is False
    assert any(message in reason for reason in report["gate"]["reasons"])
    assert script.exit_code(report) == 2


# --- answer model identity -------------------------------------------------------------


REALISTIC_TRACE = {
    "traceId": "t",
    "summary": {"upstreamModels": ["openai/text-embedding-3-small", "openai/gpt-x"]},
    "spans": [
        {"spanId": "s1", "name": "tapper.chat.turn", "attributes": {"tap.agent.tools": ""}},
        {
            "spanId": "s2",
            "name": "tapper.chat.ingress",
            "attributes": {"tap.agent.tools": "knowledge.search,graph.relations"},
        },
    ],
    "modelCalls": [
        {
            "callId": "m1",
            "operation": "embed",
            "modelName": "embedding-default",
            "upstreamModel": "openai/text-embedding-3-small",
            "provider": "openai",
            "status": "ok",
        },
        {
            "callId": "m2",
            "operation": "chat",
            "modelName": "chat-default",
            "upstreamModel": None,
            "provider": "unknown",
            "status": "ok",
        },
        {
            "callId": "m3",
            "operation": "chat",
            "modelName": "chat-default",
            "upstreamModel": "openai/gpt-z",
            "provider": "openai",
            "status": "error",
        },
        {
            "callId": "m4",
            "operation": "structured",
            "modelName": "chat-default",
            "upstreamModel": "openai/gpt-x",
            "provider": "openai",
            "status": "ok",
        },
    ],
}


def test_answer_models_skip_embedding_and_failed_calls() -> None:
    assert script.answer_models(REALISTIC_TRACE) == [
        {"alias": "chat-default", "upstreamModel": None, "provider": "unknown"},
        {"alias": "chat-default", "upstreamModel": "openai/gpt-x", "provider": "openai"},
    ]


def test_offered_tools_come_from_the_agent_tools_span_attribute() -> None:
    assert script.offered_tools(REALISTIC_TRACE) == ["knowledge.search", "graph.relations"]
    assert script.offered_tools({"spans": []}) is None


def _chat_with_models(golden: dict[str, Any], models: list[dict[str, Any]]):
    entry = _entry("q-01", called=True, citations=GOOD)
    entry["answerModels"] = models
    return _observations(golden, [entry])


def test_model_check_compares_alias_and_normalized_provider_model_id() -> None:
    golden = _golden("q-01")
    chat = _chat_with_models(golden, script.answer_models(REALISTIC_TRACE))
    baseline = _baseline(golden, [_single_shot("q-01", GOOD)])
    baseline["model"] = {"alias": "chat-default", "actual": "OpenAI/GPT-X "}
    report = script.build_report(golden, chat, baseline=baseline)
    assert report["gate"]["mode"] == "baseline"
    check = report["modelCheck"]
    assert check["mismatches"] == []
    # The streamed chat call had no resolved upstream model: listed, not hidden.
    assert check["unresolved"] == ["q-01"]


def test_model_check_rejects_an_answer_without_a_recorded_model() -> None:
    golden = _golden("q-01")
    chat = _chat_with_models(golden, [])
    baseline = _baseline(golden, [_single_shot("q-01", GOOD)])
    report = script.build_report(golden, chat, baseline=baseline)
    assert report["gate"]["mode"] == "incomparable"
    assert report["modelCheck"]["mismatches"][0]["questionId"] == "q-01"


def test_score_only_refuses_observations_from_another_golden_set() -> None:
    golden = _golden("q-01")
    chat = _observations(golden, [], goldenDigest="sha256:" + "c" * 64)
    with pytest.raises(ValueError, match="goldenDigest"):
        script.build_report(golden, chat)
    chat = _observations(golden, [], corpusDigest="sha256:" + "c" * 64)
    with pytest.raises(ValueError, match="corpusDigest"):
        script.build_report(golden, chat)


def test_tool_call_threshold_is_the_shared_relation_required_percent() -> None:
    from tap.quality.graph_relations import REQUIRED_PERCENT

    assert script.TOOL_CALL_REQUIRED_PERCENT == REQUIRED_PERCENT == 80


# --- HTTP layer ------------------------------------------------------------------------


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def test_create_conversation_posts_the_frontend_shape_with_the_web_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAPPER_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("TAPPER_WEB_PORT", "15173")
    seen: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float = 0) -> Any:
        seen.append(request)
        body = {"conversationId": "c1", "turnId": "t1", "state": "queued"}
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    api = script.TapperApi("http://127.0.0.1:8000", "p1")
    accepted = api.create_conversation(
        message="Q?",
        model_alias="chat-default",
        source_revision_ids=("rev-1",),
        agent_revision_id="validation-knowledge-agent-v3",
    )

    assert accepted == {"conversationId": "c1", "turnId": "t1", "state": "queued"}
    request = seen[0]
    assert request.get_method() == "POST"
    assert request.full_url == "http://127.0.0.1:8000/api/v1/projects/p1/conversations"
    assert request.get_header("Origin") == "http://127.0.0.1:15173"
    assert request.get_header("Idempotency-key")
    assert request.get_header("Content-type") == "application/json"
    assert request.data is not None
    assert json.loads(request.data) == {
        "message": "Q?",
        "modelAlias": "chat-default",
        "sourceRevisionIds": ["rev-1"],
        "documentRevisionIds": [],
        "agentRevisionId": "validation-knowledge-agent-v3",
        "skillRevisionIds": [],
    }


def test_follow_turn_resumes_from_the_last_sequence_after_a_dropped_stream() -> None:
    calls: list[int] = []
    chunks = [_sse(*RELATION_TURN[:2]), _sse(*RELATION_TURN[2:])]

    def open_stream(conversation_id: str, last_sequence: int):
        calls.append(last_sequence)
        return iter([chunks[len(calls) - 1].encode()])

    outcome = script.follow_turn(open_stream, "c1", TURN, deadline_seconds=30)
    assert calls == [0, 2]
    assert outcome.terminal == "turn.completed"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "error", Message(), None)


def test_follow_turn_fails_fast_on_a_client_error() -> None:
    calls: list[int] = []

    def open_stream(conversation_id: str, last_sequence: int):
        calls.append(last_sequence)
        raise _http_error(404)

    with pytest.raises(urllib.error.HTTPError):
        script.follow_turn(open_stream, "c1", TURN, deadline_seconds=30)
    assert calls == [0]


def test_follow_turn_retries_a_server_error() -> None:
    calls: list[int] = []

    def open_stream(conversation_id: str, last_sequence: int):
        calls.append(last_sequence)
        if len(calls) == 1:
            raise _http_error(503)
        return iter([_sse(*RELATION_TURN).encode()])

    outcome = script.follow_turn(
        open_stream, "c1", TURN, deadline_seconds=30, reconnect_delay_seconds=0
    )
    assert calls == [0, 0]
    assert outcome.state == "completed"


def test_follow_turn_timeout_keeps_the_partial_outcome() -> None:
    def open_stream(conversation_id: str, last_sequence: int):
        return iter([_sse(*RELATION_TURN[:3]).encode()])

    with pytest.raises(script.TurnTimeout) as raised:
        script.follow_turn(open_stream, "c1", TURN, deadline_seconds=0)
    assert raised.value.outcome.steps[0]["status"] == "completed"


class _FakeApi:
    def __init__(self) -> None:
        self.created: list[str] = []

    def create_conversation(self, *, message: str, **_: Any) -> dict[str, Any]:
        self.created.append(message)
        if "q-01" in message:
            raise _http_error(422)
        return {"conversationId": f"c{len(self.created)}", "turnId": TURN, "state": "queued"}

    def open_stream(self, conversation_id: str, last_sequence: int):
        if conversation_id == "c2":
            return iter([_sse(RELATION_TURN[0]).encode()])
        return iter([_sse(*RELATION_TURN).encode()])

    def turn_trace(self, conversation_id: str, turn_id: str) -> dict[str, Any]:
        return REALISTIC_TRACE


def test_ask_questions_records_failures_and_continues() -> None:
    questions = script.golden_questions(_golden("q-01", "q-02", "q-03"))
    revisions = {question.id: ("rev-1",) for question in questions}
    seen: list[str] = []
    answers = script.ask_questions(
        _FakeApi(),
        questions,
        revisions,
        model_alias="chat-default",
        agent_revision_id=None,
        turn_timeout_seconds=0,
        on_answer=lambda entry: seen.append(entry["questionId"]),
    )
    assert seen == ["q-01", "q-02", "q-03"]
    assert [entry["outcome"] for entry in answers] == ["error", "timeout", "completed"]
    assert answers[0]["abstained"] is True and "422" in answers[0]["error"]
    assert answers[2]["graphRelationsOffered"] is True
    assert answers[2]["answerModels"][1]["upstreamModel"] == "openai/gpt-x"

    golden = _golden("q-01", "q-02", "q-03")
    report = script.build_report(golden, _observations(golden, answers))
    statuses = [row["status"] for row in report["questions"]]
    assert statuses == ["miss", "miss", "pass"]


def test_web_origin_helper_is_shared() -> None:
    from tap.quality.web_origin import web_origin

    assert script.web_origin is web_origin
