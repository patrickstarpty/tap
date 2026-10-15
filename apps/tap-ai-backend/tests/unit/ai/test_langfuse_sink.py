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
    def __init__(self, *, delete_error: BaseException | None = None) -> None:
        self.deleted: list[str] = []
        self._delete_error = delete_error

    def delete(self, *, score_id):  # type: ignore[no-untyped-def]
        if self._delete_error is not None:
            raise self._delete_error
        self.deleted.append(score_id)


class _FakeLegacy:
    def __init__(self, score_v1: _FakeScoreV1) -> None:
        self.score_v1 = score_v1


class _FakeClient:
    def __init__(
        self, *, errors: list[object] | None = None, delete_error: BaseException | None = None
    ) -> None:
        self.ingestion = _FakeIngestionClient(errors=errors)
        self.legacy = _FakeLegacy(_FakeScoreV1(delete_error=delete_error))


def _sink(
    monkeypatch,  # type: ignore[no-untyped-def]
    *,
    errors: list[object] | None = None,
    delete_error: BaseException | None = None,
    environment: str = "tapper-demo",
):
    from tap.modules.ai.adapters import langfuse_sink as module

    client = _FakeClient(errors=errors, delete_error=delete_error)
    monkeypatch.setattr(module, "LangfuseAPI", lambda **_kwargs: client)
    sink = LangfuseSdkSink(
        host="http://127.0.0.1:23000",
        public_key="pk",
        secret_key="sk",
        environment=environment,
    )
    return sink, client


def _span(
    *,
    span_id: str = "span-1",
    parent_id: str | None = None,
    start_time: datetime = _START,
    end_time: datetime = _END,
) -> LangfuseObservation:
    return LangfuseObservation(
        id=span_id,
        parent_id=parent_id,
        kind="span",
        name="chat.turn",
        start_time=start_time,
        end_time=end_time,
        level="DEFAULT",
        status_message=None,
        input="hello",
        output="hi",
        model=None,
        usage_details={},
        cost_details={},
        metadata={},
    )


def _generation(
    *,
    gen_id: str = "gen-1",
    parent_id: str | None = "span-1",
    start_time: datetime = _START,
    end_time: datetime = _END,
) -> LangfuseObservation:
    return LangfuseObservation(
        id=gen_id,
        parent_id=parent_id,
        kind="generation",
        name="chat.completion",
        start_time=start_time,
        end_time=end_time,
        level="DEFAULT",
        status_message=None,
        input="hello",
        output="hi",
        model="qwen-plus",
        usage_details={"input": 10, "output": 20},
        cost_details={"total": 0.0012},
        metadata={"foo": "bar"},
    )


def _trace(*, observations: tuple[LangfuseObservation, ...] | None = None) -> LangfuseTrace:
    return LangfuseTrace(
        id="trace-1",
        name="chat.turn",
        session_id="conv-1",
        user_id="actor-1",
        environment="local",
        tags=("tapper",),
        metadata={"turn_id": "turn-1"},
        observations=observations if observations is not None else (_span(), _generation()),
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
async def test_sdk_sink_upsert_score_normalizes_a_naive_timestamp_to_utc(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """MySQL `created_at` comes back naive (no offset) but is UTC; Langfuse
    rejects an offset-less timestamp outright, so a naive value must still be
    sent as an explicit UTC offset, not stripped or defaulted to local time."""

    sink, client = _sink(monkeypatch)
    naive_timestamp = datetime(2026, 10, 10, 8, 30, 0)
    score = LangfuseScore(
        id="score-1",
        trace_id="trace-1",
        name="user_feedback",
        value=1.0,
        comment=None,
        timestamp=naive_timestamp,
    )

    await sink.upsert_score(score)

    (event,) = client.ingestion.calls[0]
    assert event.timestamp == "2026-10-10T08:30:00+00:00"


@pytest.mark.asyncio
async def test_sdk_sink_trace_timestamp_is_the_earliest_observation_start_time(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """The trace envelope/body timestamp must be a historical, stable value —
    not "now" — because Langfuse v3 keys trace overwrite by day; re-pushing
    the same trace with "now" would move it to a different day's bucket."""

    sink, client = _sink(monkeypatch)
    earlier = _START - timedelta(minutes=5)
    trace = _trace(observations=(_span(start_time=earlier), _generation()))

    await sink.push_trace(trace)

    trace_event = client.ingestion.calls[0][0]
    assert trace_event.body.timestamp == earlier
    assert trace_event.timestamp == earlier.isoformat()


@pytest.mark.asyncio
async def test_sdk_sink_trace_timestamp_is_stable_across_re_pushes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sink, client = _sink(monkeypatch)
    trace = _trace()

    await sink.push_trace(trace)
    await sink.push_trace(trace)

    first_event, second_event = (calls[0] for calls in client.ingestion.calls)
    assert first_event.timestamp == second_event.timestamp
    assert first_event.body.timestamp == second_event.body.timestamp


@pytest.mark.asyncio
async def test_sdk_sink_environment_reaches_observations_and_scores(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sink, client = _sink(monkeypatch, environment="tapper-demo")

    await sink.push_trace(_trace())
    score = LangfuseScore(
        id="score-1",
        trace_id="trace-1",
        name="user_feedback",
        value=1.0,
        comment=None,
        timestamp=_START,
    )
    await sink.upsert_score(score)

    trace_event, span_event, generation_event = client.ingestion.calls[0]
    # The trace keeps the trace's own recorded environment, not the sink's.
    assert trace_event.body.environment == "local"
    assert span_event.body.environment == "tapper-demo"
    assert generation_event.body.environment == "tapper-demo"
    (score_event,) = client.ingestion.calls[1]
    assert score_event.body.environment == "tapper-demo"


@pytest.mark.asyncio
async def test_sdk_sink_trace_wire_form_has_only_expected_camelcase_keys(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """A fake with `extra="allow"` fields would silently accept a misspelled
    kwarg; asserting the exact serialized key set (what the SDK actually puts
    on the wire) catches that a plain `getattr` assertion would miss."""

    sink, client = _sink(monkeypatch)

    await sink.push_trace(_trace())

    trace_event, span_event, generation_event = client.ingestion.calls[0]
    assert set(trace_event.body.dict().keys()) == {
        "id",
        "timestamp",
        "name",
        "userId",
        "sessionId",
        "metadata",
        "tags",
        "environment",
    }
    assert set(span_event.body.dict().keys()) == {
        "id",
        "traceId",
        "parentObservationId",
        "name",
        "startTime",
        "endTime",
        "level",
        "statusMessage",
        "input",
        "output",
        "metadata",
        "environment",
    }
    assert set(generation_event.body.dict().keys()) == {
        "id",
        "traceId",
        "parentObservationId",
        "name",
        "startTime",
        "endTime",
        "level",
        "statusMessage",
        "input",
        "output",
        "metadata",
        "environment",
        "model",
        "usageDetails",
        "costDetails",
    }


@pytest.mark.asyncio
async def test_sdk_sink_score_wire_form_has_only_expected_camelcase_keys(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    sink, client = _sink(monkeypatch)
    score = LangfuseScore(
        id="score-1",
        trace_id="trace-1",
        name="user_feedback",
        value=1.0,
        comment="great",
        timestamp=_START,
    )

    await sink.upsert_score(score)

    (event,) = client.ingestion.calls[0]
    assert set(event.body.dict().keys()) == {
        "id",
        "traceId",
        "name",
        "environment",
        "value",
        "comment",
    }


@pytest.mark.asyncio
async def test_sdk_sink_delete_score_treats_404_as_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from langfuse.api import NotFoundError

    sink, _client = _sink(monkeypatch, delete_error=NotFoundError(body=None))

    await sink.delete_score("score-1")


@pytest.mark.asyncio
async def test_sdk_sink_delete_score_treats_generic_404_api_error_as_success(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Not every 404 the SDK surfaces is the typed `NotFoundError` subclass;
    a bare `ApiError(status_code=404, ...)` (e.g. from a non-JSON body on
    that response) must be treated the same way."""

    from langfuse.api.core.api_error import ApiError

    sink, _client = _sink(monkeypatch, delete_error=ApiError(status_code=404, body="not json"))

    await sink.delete_score("score-1")


@pytest.mark.asyncio
async def test_sdk_sink_delete_score_reraises_a_non_404_api_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from langfuse.api.core.api_error import ApiError

    sink, _client = _sink(monkeypatch, delete_error=ApiError(status_code=500, body="boom"))

    with pytest.raises(ApiError):
        await sink.delete_score("score-1")


@pytest.mark.asyncio
async def test_sdk_sink_delete_score_passes_through_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sink, client = _sink(monkeypatch)

    await sink.delete_score("score-1")

    assert client.legacy.score_v1.deleted == ["score-1"]
