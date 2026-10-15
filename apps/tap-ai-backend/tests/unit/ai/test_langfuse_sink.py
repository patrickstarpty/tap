"""`LangfuseSdkSink`: maps domain carriers onto the SDK's low-level ingestion
batch calls (spec §8). A fake Langfuse client stands in for the real SDK —
no network is involved."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tap.modules.ai.adapters.langfuse_sink import LangfuseSdkSink, LangfuseSinkError
from tap.modules.ai.domain.langfuse_sync import LangfuseObservation, LangfuseScore, LangfuseTrace

_START = datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc)
_END = _START + timedelta(seconds=2)


class _FakeIngestionClient:
    def __init__(self, *, errors: list[object] | None = None) -> None:
        self.calls: list[list[object]] = []
        self._errors = errors or []

    def batch(self, *, batch):  # type: ignore[no-untyped-def]
        self.calls.append(list(batch))
        return SimpleNamespace(errors=self._errors, successes=[])


class _FakeScoreV1:
    def __init__(self, *, not_found: bool = False) -> None:
        self.deleted: list[str] = []
        self._not_found = not_found

    def delete(self, *, score_id):  # type: ignore[no-untyped-def]
        if self._not_found:
            from langfuse.api import NotFoundError

            raise NotFoundError(body=None)
        self.deleted.append(score_id)


class _FakeLegacy:
    def __init__(self, score_v1: _FakeScoreV1) -> None:
        self.score_v1 = score_v1


class _FakeClient:
    def __init__(self, *, errors: list[object] | None = None, not_found: bool = False) -> None:
        self.ingestion = _FakeIngestionClient(errors=errors)
        self.legacy = _FakeLegacy(_FakeScoreV1(not_found=not_found))


def _sink(monkeypatch, *, errors: list[object] | None = None, not_found: bool = False):  # type: ignore[no-untyped-def]
    from tap.modules.ai.adapters import langfuse_sink as module

    client = _FakeClient(errors=errors, not_found=not_found)
    monkeypatch.setattr(module, "LangfuseAPI", lambda **_kwargs: client)
    sink = LangfuseSdkSink(host="http://127.0.0.1:23000", public_key="pk", secret_key="sk")
    return sink, client


def _trace() -> LangfuseTrace:
    span = LangfuseObservation(
        id="span-1",
        parent_id=None,
        kind="span",
        name="chat.turn",
        start_time=_START,
        end_time=_END,
        level="DEFAULT",
        status_message=None,
        input="hello",
        output="hi",
        model=None,
        usage_details={},
        cost_details={},
        metadata={},
    )
    generation = LangfuseObservation(
        id="gen-1",
        parent_id="span-1",
        kind="generation",
        name="chat.completion",
        start_time=_START,
        end_time=_END,
        level="DEFAULT",
        status_message=None,
        input="hello",
        output="hi",
        model="qwen-plus",
        usage_details={"input": 10, "output": 20},
        cost_details={"total": 0.0012},
        metadata={"foo": "bar"},
    )
    return LangfuseTrace(
        id="trace-1",
        name="chat.turn",
        session_id="conv-1",
        user_id="actor-1",
        environment="local",
        tags=("tapper",),
        metadata={"turn_id": "turn-1"},
        observations=(span, generation),
    )


@pytest.mark.asyncio
async def test_sdk_sink_maps_trace_to_sdk_calls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sink, client = _sink(monkeypatch)

    await sink.push_trace(_trace())

    assert len(client.ingestion.calls) == 1
    events = client.ingestion.calls[0]
    assert [event.type for event in events] == ["trace-create", "span-create", "generation-create"]

    trace_event = events[0]
    assert trace_event.body.id == "trace-1"
    assert trace_event.body.user_id == "actor-1"
    assert trace_event.body.session_id == "conv-1"
    assert trace_event.body.environment == "local"
    assert trace_event.body.tags == ["tapper"]

    span_event = events[1]
    assert span_event.body.id == "span-1"
    assert span_event.body.trace_id == "trace-1"
    assert span_event.body.parent_observation_id is None
    assert span_event.body.start_time == _START
    assert span_event.body.end_time == _END

    generation_event = events[2]
    assert generation_event.body.id == "gen-1"
    assert generation_event.body.parent_observation_id == "span-1"
    assert generation_event.body.model == "qwen-plus"
    assert generation_event.body.usage_details == {"input": 10, "output": 20}
    assert generation_event.body.cost_details == {"total": 0.0012}


@pytest.mark.asyncio
async def test_sdk_sink_raises_when_ingestion_batch_reports_errors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sink, _client = _sink(
        monkeypatch, errors=[SimpleNamespace(id="trace-1", status=400, message="bad request")]
    )

    with pytest.raises(LangfuseSinkError):
        await sink.push_trace(_trace())


@pytest.mark.asyncio
async def test_sdk_sink_upsert_score_uses_score_timestamp_as_envelope_timestamp(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    sink, client = _sink(monkeypatch)
    timestamp = datetime(2026, 10, 10, 8, 30, 0, tzinfo=timezone.utc)
    score = LangfuseScore(
        id="score-1",
        trace_id="trace-1",
        name="user_feedback",
        value=1.0,
        comment="great",
        timestamp=timestamp,
    )

    await sink.upsert_score(score)

    assert len(client.ingestion.calls) == 1
    (event,) = client.ingestion.calls[0]
    assert event.type == "score-create"
    assert event.timestamp == timestamp.isoformat()
    assert event.body.id == "score-1"
    assert event.body.trace_id == "trace-1"
    assert event.body.value == 1.0
    assert event.body.comment == "great"


@pytest.mark.asyncio
async def test_sdk_sink_delete_score_treats_404_as_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sink, _client = _sink(monkeypatch, not_found=True)

    await sink.delete_score("score-1")


@pytest.mark.asyncio
async def test_sdk_sink_delete_score_passes_through_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sink, client = _sink(monkeypatch)

    await sink.delete_score("score-1")

    assert client.legacy.score_v1.deleted == ["score-1"]
