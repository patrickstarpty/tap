"""Failure classification and retry backoff for the Tapper chat turn worker.

`classify_turn_failure` sorts every exception the chat turn graph can raise into
one of three buckets so the worker knows whether retrying, surfacing an owner-
visible error, or returning a deterministic failure to the caller is correct:

- ``transient``: the underlying dependency is temporarily unavailable; retrying
  the same input, after a backoff, can succeed.
- ``deterministic``: the same input will fail again; retrying cannot help.
- ``ownership``: the failure is about who may act on this turn/conversation
  (a lease, authorization, or conflicting write), not about the dependency's
  health; the caller must re-establish ownership rather than blindly retry.

`GenerationWorker` wires `classify_turn_failure` into its node-level `retryable`
predicates and its own top-level `except Exception` handling; the `ownership`
bucket is handled by exiting silently, writing nothing, so the real owner's
own write is never clobbered.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Literal

from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from tap.interfaces.http.knowledge_service import AnswerBudgetExhausted
from tap.modules.access.domain.policy import AuthorizationDenied, PolicyUnavailable
from tap.modules.ai.domain.graph_runs import (
    GraphCheckpointRetryable,
    GraphCheckpointUnavailable,
    GraphLeaseLost,
)
from tap.modules.ai.domain.models import (
    ModelGatewayRejected,
    ModelGatewayUnavailable,
    ModelOutputTruncated,
)
from tap.modules.ai.ports.insights import (
    InsightsAuthorizationChanged,
    InsightsBudgetExceeded,
    InsightsQueryUnavailable,
)
from tap.modules.chat.application.conversations import ConversationConflict, ConversationNotFound
from tap.modules.knowledge.application.evidence_bundle import EvidenceBundleTooLarge
from tap.modules.knowledge.ports.answers import AnswerSnapshotUnavailable, DocumentStateChanged
from tap.modules.knowledge.ports.errors import (
    AnswerRejected,
    ArtifactUnavailable,
    IndexUnavailable,
    KnowledgeRuntimeUnavailable,
    ModelRejected,
    ModelUnavailable,
    SearchUnavailable,
)

TurnFailureClass = Literal["transient", "deterministic", "ownership"]

_RETRY_DELAY_MIN_SECONDS = 0.5
_RETRY_DELAY_MAX_SECONDS = 1.0
_RETRY_DELAY_CAP_SECONDS = 30.0


def classify_turn_failure(error: BaseException) -> TurnFailureClass:
    """Classify `error` as `transient`, `deterministic`, or `ownership`.

    Matching follows the fixed order below; the first matching bucket wins.
    Anything not listed is `deterministic` -- the safe default when a new
    exception type is introduced and this classifier is not updated for it.

    `AuthorizationDenied` is special-cased first: `authorize_completed_answer`
    wraps every exception from its post-graph reauthorization recheck
    (`PolicyUnavailable`, `OperationalError`, ...) as `AuthorizationDenied`
    via `raise ... from error`, which would otherwise always read as a plain
    (deterministic) denial. One level of `__cause__` is unwrapped: if the
    cause itself classifies as transient, the denial is transient too (retry
    re-runs the recheck, so it stays fail-closed); a plain denial with no
    cause, or a cause that is not transient, stays deterministic.
    """
    if isinstance(error, AuthorizationDenied):
        cause = error.__cause__
        if cause is not None and classify_turn_failure(cause) == "transient":
            return "transient"
        return "deterministic"
    if isinstance(
        error,
        (
            InsightsAuthorizationChanged,
            ModelGatewayRejected,
            ModelOutputTruncated,
            ModelRejected,
            AnswerRejected,
            EvidenceBundleTooLarge,
            AnswerBudgetExhausted,
            InsightsBudgetExceeded,
            InsightsQueryUnavailable,
            DocumentStateChanged,
        ),
    ):
        return "deterministic"
    if isinstance(
        error,
        (
            PermissionError,
            ConversationConflict,
            ConversationNotFound,
            GraphLeaseLost,
        ),
    ):
        return "ownership"
    if isinstance(
        error,
        (
            GraphCheckpointRetryable,
            ModelGatewayUnavailable,
            ModelUnavailable,
            SearchUnavailable,
            KnowledgeRuntimeUnavailable,
            IndexUnavailable,
            ArtifactUnavailable,
            AnswerSnapshotUnavailable,
            PolicyUnavailable,
            TimeoutError,
            ConnectionError,
            SAOperationalError,
            SATimeoutError,
        ),
    ):
        return "transient"
    if isinstance(error, GraphCheckpointUnavailable):
        return "deterministic"
    return "deterministic"


def retry_delay(attempt: int, *, rng: random.Random | None = None) -> timedelta:
    """Return a jittered, exponentially increasing backoff for `attempt` (>= 1).

    The uncapped exponential ceiling doubles each attempt (2s, 4s, 8s, 16s, ...)
    up to a `30s` cap; the actual delay is drawn uniformly from half that
    ceiling to the full ceiling, so the result always lands in
    `[ceiling / 2, ceiling]`.
    """
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    source = rng or random
    ceiling = min(2.0**attempt, _RETRY_DELAY_CAP_SECONDS)
    multiplier = source.uniform(_RETRY_DELAY_MIN_SECONDS, _RETRY_DELAY_MAX_SECONDS)
    return timedelta(seconds=ceiling * multiplier)
