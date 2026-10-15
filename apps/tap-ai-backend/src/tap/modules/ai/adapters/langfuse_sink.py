"""`LangfuseSdkSink`: the SDK-backed `LangfuseSink` implementation.

Per spec §8 (SDK source read, Langfuse 3.225.11 self-hosted): trace, span,
generation and score creation/overwrite all go through the SDK's low-level
synchronous `langfuse.api.ingestion.batch` endpoint — not the high-level OTel
tracer API and not a direct OTLP connection. `langfuse.api.scores.create` is
never used (it cannot carry the day-granularity timestamp the overwrite key
needs); a score is instead sent as an `IngestionEvent_ScoreCreate` whose
envelope `timestamp` is the feedback's `created_at`. Deletion uses the only
delete path the SDK exposes, `langfuse.api.legacy.score_v1.delete`.

`ingestion.batch` is marked `@typing_extensions.deprecated` in the installed
SDK (self-hosted deployments on v3 are unaffected; see spec §8), so the
warning is suppressed locally around that one call site only.

Every timestamp this sink hands to the SDK goes through `_as_utc`/`_iso`
first: Langfuse rejects an offset-less ("naive") timestamp, but the MySQL
columns this data is read from (`created_at`, `started_at`) come back naive
(the columns carry no offset, even though the values are UTC).
"""

from __future__ import annotations

import asyncio
import uuid
import warnings
from datetime import UTC, datetime
from typing import Any

from langfuse.api import LangfuseAPI
from langfuse.api.core.api_error import ApiError
from langfuse.api.ingestion.types.create_generation_body import CreateGenerationBody
from langfuse.api.ingestion.types.create_span_body import CreateSpanBody
from langfuse.api.ingestion.types.ingestion_event import (
    IngestionEvent,
    IngestionEvent_GenerationCreate,
    IngestionEvent_ScoreCreate,
    IngestionEvent_SpanCreate,
    IngestionEvent_TraceCreate,
)
from langfuse.api.ingestion.types.score_body import ScoreBody
from langfuse.api.ingestion.types.trace_body import TraceBody

from tap.modules.ai.domain.langfuse_sync import LangfuseObservation, LangfuseScore, LangfuseTrace
from tap.modules.ai.ports.langfuse_sync import LangfuseSink

_NOT_FOUND_STATUS = 404


class LangfuseSinkError(RuntimeError):
    """One or more events in an `ingestion.batch` call were rejected server-side."""


class LangfuseSdkSink(LangfuseSink):
    def __init__(self, *, host: str, public_key: str, secret_key: str, environment: str) -> None:
        # Langfuse's REST API authenticates with HTTP basic auth: the project's
        # public key as the username, the secret key as the password.
        self._client: Any = LangfuseAPI(base_url=host, username=public_key, password=secret_key)
        self._environment = environment

    async def push_trace(self, trace: LangfuseTrace) -> None:
        batch: list[IngestionEvent] = [_trace_event(trace)]
        batch.extend(
            _observation_event(trace.id, observation, environment=self._environment)
            for observation in trace.observations
        )
        await asyncio.to_thread(self._send_batch, batch)

    async def upsert_score(self, score: LangfuseScore) -> None:
        await asyncio.to_thread(
            self._send_batch, [_score_event(score, environment=self._environment)]
        )

    async def delete_score(self, score_id: str) -> None:
        await asyncio.to_thread(self._delete_score, score_id)

    def _send_batch(self, batch: list[IngestionEvent]) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            response = self._client.ingestion.batch(batch=batch)
        if response.errors:
            details = [(error.id, error.status, error.message) for error in response.errors]
            raise LangfuseSinkError(
                f"Langfuse ingestion batch reported {len(response.errors)} error(s): {details}"
            )

    def _delete_score(self, score_id: str) -> None:
        try:
            self._client.legacy.score_v1.delete(score_id=score_id)
        except ApiError as error:
            if error.status_code == _NOT_FOUND_STATUS:
                # Already gone is the goal state of a delete. `NotFoundError`
                # (the SDK's typed 404) is a subclass of `ApiError` and is
                # caught here too; a generic `ApiError` that still carries a
                # 404 status (e.g. a non-JSON body on that response) is
                # treated the same way.
                return
            raise


def _event_id() -> str:
    return uuid.uuid4().hex


def _as_utc(value: datetime) -> datetime:
    """Treat a naive datetime as already UTC; convert an aware one to UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _trace_timestamp(trace: LangfuseTrace) -> datetime:
    """A trace's historical timestamp: the earliest observation's start time.

    Stable across re-pushes of the same trace (unlike "now"), which matters
    because Langfuse keys overwrite-on-same-id by day (spec §8). Only a trace
    with no observations yet falls back to the current time.
    """

    if trace.observations:
        earliest = min(observation.start_time for observation in trace.observations)
        return _as_utc(earliest)
    return datetime.now(UTC)


def _trace_event(trace: LangfuseTrace) -> IngestionEvent_TraceCreate:
    timestamp = _trace_timestamp(trace)
    body = TraceBody(
        id=trace.id,
        timestamp=timestamp,
        name=trace.name,
        user_id=trace.user_id,
        session_id=trace.session_id,
        metadata=trace.metadata,
        tags=list(trace.tags),
        environment=trace.environment,
    )
    return IngestionEvent_TraceCreate(id=_event_id(), timestamp=timestamp.isoformat(), body=body)


def _observation_event(
    trace_id: str, observation: LangfuseObservation, *, environment: str
) -> IngestionEvent_SpanCreate | IngestionEvent_GenerationCreate:
    start_time = _as_utc(observation.start_time)
    common: dict[str, Any] = {
        "id": observation.id,
        "trace_id": trace_id,
        "parent_observation_id": observation.parent_id,
        "name": observation.name,
        "start_time": start_time,
        "end_time": _as_utc(observation.end_time),
        "level": observation.level,
        "status_message": observation.status_message,
        "input": observation.input,
        "output": observation.output,
        "metadata": observation.metadata,
        "environment": environment,
    }
    if observation.kind == "generation":
        generation_body = CreateGenerationBody(
            **common,
            model=observation.model,
            usage_details=observation.usage_details,
            cost_details=observation.cost_details,
        )
        return IngestionEvent_GenerationCreate(
            id=_event_id(), timestamp=start_time.isoformat(), body=generation_body
        )
    span_body = CreateSpanBody(**common)
    return IngestionEvent_SpanCreate(
        id=_event_id(), timestamp=start_time.isoformat(), body=span_body
    )


def _score_event(score: LangfuseScore, *, environment: str) -> IngestionEvent_ScoreCreate:
    body = ScoreBody(
        id=score.id,
        trace_id=score.trace_id,
        name=score.name,
        value=score.value,
        comment=score.comment,
        environment=environment,
    )
    # The envelope timestamp (not anything on ScoreBody, which carries none) is
    # the day-granularity key Langfuse uses, together with id and name, to
    # decide whether this overwrites an existing score (spec §8); it must stay
    # `score.timestamp` (the feedback row's `created_at`), never "now".
    return IngestionEvent_ScoreCreate(id=_event_id(), timestamp=_iso(score.timestamp), body=body)


__all__ = ["LangfuseSdkSink", "LangfuseSinkError"]
