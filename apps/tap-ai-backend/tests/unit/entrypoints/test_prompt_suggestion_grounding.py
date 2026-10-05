"""ConversationGroundingCheck runs a candidate through the chat answer pipeline with
the requested sources in SCOPE mode, and CompositeReadyProjection fans a READY event
out to every wired projection (graph extraction, prompt suggestion refresh, ...)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tap.contracts.http import ResourceMode, SourceFamily
from tap.entrypoints.prompt_suggestion_knowledge import (
    CompositeReadyProjection,
    ConversationGroundingCheck,
)
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.ports.errors import AnswerUnavailable

_HASH = "sha256:" + "a" * 64
_SOURCE = "src_" + "1" * 32
_REVISION = "rev-1"


class _ReadySources:
    async def list_sources(self):
        return SimpleNamespace(items=[SimpleNamespace(source_id=_SOURCE, revision_id=_REVISION)])


class _Planner:
    def __init__(self) -> None:
        self.inputs: list[object] = []

    async def plan(self, value):
        self.inputs.append(value)
        return "plan-1"


class _Knowledge:
    def __init__(self, *, answer=None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.answer_planner = _Planner()
        self.authorized: list[object] = []
        self.calls: list[tuple[object, object, object]] = []

    async def resolve_conversation_selection(self, revision_ids):
        assert revision_ids == (_REVISION,)
        return (
            [
                SimpleNamespace(
                    source_id=_SOURCE,
                    document_id="doc-1",
                    revision_id=_REVISION,
                    source_content_hash=_HASH,
                    source_name="Policy terms.pdf",
                )
            ],
            SimpleNamespace(
                acl_digest=_HASH,
                decision_id="decision-1",
                policy_version="policy-1",
                active_corpus_version="corpus-1",
            ),
        )

    async def authorize_planning(self, snapshot) -> None:
        self.authorized.append(snapshot)

    async def answer_conversation(self, request, frozen_input, *, answer_plan=None, authorize=None):
        self.calls.append((request, frozen_input, answer_plan))
        if self.error is not None:
            raise self.error
        return self.answer


def _check(knowledge: _Knowledge) -> ConversationGroundingCheck:
    return ConversationGroundingCheck(
        knowledge, ready_sources=_ReadySources(), scope=VALIDATION_SCOPE, model_alias="qwen-plus"
    )


@pytest.mark.asyncio
async def test_grounding_runs_the_chat_planner_and_answer_pipeline():
    knowledge = _Knowledge(answer=SimpleNamespace(abstained=False, citations=[object()]))

    grounded = await _check(knowledge).is_grounded("actor-1", "保单红利如何派发？", (_SOURCE,))

    assert grounded is True
    snapshot = knowledge.authorized[0]
    assert snapshot.value.message == "保单红利如何派发？"
    assert snapshot.value.source_revision_ids == (_REVISION,)
    assert [item.revision_id for item in snapshot.value.resolved_resources] == [_REVISION]
    assert len(knowledge.answer_planner.inputs) == 1
    request, frozen_input, plan = knowledge.calls[0]
    assert plan == "plan-1"
    assert frozen_input == snapshot.value
    assert [ref.source_id for ref in request.resource_refs] == [_SOURCE]
    assert all(ref.family is SourceFamily.DOC for ref in request.resource_refs)
    assert all(ref.mode is ResourceMode.SCOPE for ref in request.resource_refs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        SimpleNamespace(abstained=True, citations=[]),
        SimpleNamespace(abstained=False, citations=[]),
    ],
    ids=["abstained", "uncited"],
)
async def test_abstained_or_uncited_answers_are_not_grounded(answer):
    assert await _check(_Knowledge(answer=answer)).is_grounded("a", "Q?", (_SOURCE,)) is False


@pytest.mark.asyncio
async def test_sources_without_a_current_revision_are_not_grounded():
    knowledge = _Knowledge(answer=SimpleNamespace(abstained=False, citations=[object()]))

    assert await _check(knowledge).is_grounded("a", "Q?", ("src_" + "9" * 32,)) is False
    assert knowledge.calls == []


@pytest.mark.asyncio
async def test_invalid_grounded_answer_is_not_grounded():
    knowledge = _Knowledge(
        error=AnswerUnavailable("claim text is not one unique complete answer statement")
    )

    assert await _check(knowledge).is_grounded("a", "Q?", (_SOURCE,)) is False


@pytest.mark.asyncio
async def test_unavailable_answer_model_fails_the_grounding_check():
    knowledge = _Knowledge(error=AnswerUnavailable("model-unavailable"))

    with pytest.raises(AnswerUnavailable):
        await _check(knowledge).is_grounded("a", "Q?", (_SOURCE,))


class _RecordingProjection:
    def __init__(self, name: str, calls: list[str]) -> None:
        self._name = name
        self._calls = calls

    async def after_ready(self, session, scope, revision, *, now, ingestion_job_id):
        del session, scope, revision, now, ingestion_job_id
        self._calls.append(self._name)


@pytest.mark.asyncio
async def test_composite_projection_calls_every_projection():
    calls: list[str] = []
    composite = CompositeReadyProjection(
        _RecordingProjection("graph", calls), _RecordingProjection("suggestions", calls)
    )

    await composite.after_ready(
        object(),
        object(),
        {"revision_id": "rev-1"},
        now=datetime(2026, 9, 30, tzinfo=timezone.utc),
        ingestion_job_id="job-1",
    )

    assert calls == ["graph", "suggestions"]
