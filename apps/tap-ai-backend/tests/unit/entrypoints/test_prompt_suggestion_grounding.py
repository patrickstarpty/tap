"""AnswerGroundingCheck always queries with the requested sources in SCOPE
mode, and CompositeReadyProjection fans a READY event out to every wired
projection (graph extraction, prompt suggestion refresh, ...)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from tap.entrypoints.prompt_suggestion_knowledge import (
    AnswerGroundingCheck,
    CompositeReadyProjection,
)
from tap.modules.knowledge.domain.models import AnswerRequest, ResourceMode, SourceFamily
from tap.modules.knowledge.ports.errors import AnswerUnavailable


@dataclass
class _FakeAnswerResponse:
    abstained: bool


class _FakeAnswerService:
    def __init__(self, *, abstained: bool) -> None:
        self.abstained = abstained
        self.requests: list[AnswerRequest] = []

    async def answer(self, request: AnswerRequest) -> _FakeAnswerResponse:
        self.requests.append(request)
        return _FakeAnswerResponse(abstained=self.abstained)


@pytest.mark.asyncio
async def test_grounding_uses_scope_resources_and_abstention():
    answers = _FakeAnswerService(abstained=True)
    check = AnswerGroundingCheck(answers)

    grounded = await check.is_grounded("actor-1", "What is the refund policy?", ("src-1", "src-2"))

    assert grounded is False
    assert len(answers.requests) == 1
    request = answers.requests[0]
    assert request.query == "What is the refund policy?"
    assert [ref.source_id for ref in request.resource_refs] == ["src-1", "src-2"]
    assert all(ref.family is SourceFamily.DOC for ref in request.resource_refs)
    assert all(ref.mode is ResourceMode.SCOPE for ref in request.resource_refs)

    answers.abstained = False
    assert await check.is_grounded("actor-1", "Another question?", ("src-1",)) is True


class _FailingAnswerService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def answer(self, request: AnswerRequest) -> _FakeAnswerResponse:
        del request
        raise self.error


@pytest.mark.asyncio
async def test_invalid_grounded_answer_is_not_grounded():
    check = AnswerGroundingCheck(
        _FailingAnswerService(
            AnswerUnavailable("claim text is not one unique complete answer statement")
        )
    )

    assert await check.is_grounded("actor-1", "What is the refund policy?", ("src-1",)) is False


@pytest.mark.asyncio
async def test_unavailable_answer_model_fails_the_grounding_check():
    check = AnswerGroundingCheck(_FailingAnswerService(AnswerUnavailable("model-unavailable")))

    with pytest.raises(AnswerUnavailable):
        await check.is_grounded("actor-1", "What is the refund policy?", ("src-1",))


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
