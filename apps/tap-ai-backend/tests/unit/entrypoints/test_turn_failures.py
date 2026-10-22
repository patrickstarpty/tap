"""Failure classification and retry backoff for the Tapper chat turn worker."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from tap.entrypoints.tapper_turn_failures import classify_turn_failure, retry_delay
from tap.interfaces.http.knowledge_service import AnswerBudgetExhausted, KnowledgeHttpService
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
    AnswerUnavailable,
    ArtifactUnavailable,
    IndexUnavailable,
    KnowledgeRuntimeUnavailable,
    ModelRejected,
    ModelUnavailable,
    SearchUnavailable,
)
from tap.modules.knowledge.ports.redaction import RedactionUnavailable
from tap.platform.budget import CallBudgetExhausted, TurnBudgetExhausted

_CLASSIFICATION_TABLE: tuple[tuple[BaseException, str], ...] = (
    (TurnBudgetExhausted("wall-clock"), "deterministic"),
    (CallBudgetExhausted(), "deterministic"),
    (InsightsAuthorizationChanged("x"), "deterministic"),
    (AuthorizationDenied("x"), "deterministic"),
    (ModelGatewayRejected(), "deterministic"),
    (ModelOutputTruncated(), "deterministic"),
    (ModelRejected("x"), "deterministic"),
    (AnswerRejected("x"), "deterministic"),
    (EvidenceBundleTooLarge("x"), "deterministic"),
    (AnswerBudgetExhausted("x"), "deterministic"),
    (InsightsBudgetExceeded("x"), "deterministic"),
    (InsightsQueryUnavailable("x"), "deterministic"),
    (DocumentStateChanged("x"), "deterministic"),
    (PermissionError("x"), "ownership"),
    (ConversationConflict("x"), "ownership"),
    (ConversationNotFound("x"), "ownership"),
    (GraphLeaseLost("x"), "ownership"),
    (GraphCheckpointRetryable("x"), "transient"),
    (ModelGatewayUnavailable(), "transient"),
    (ModelUnavailable("x"), "transient"),
    (AnswerUnavailable("x"), "transient"),
    (SearchUnavailable("x"), "transient"),
    (KnowledgeRuntimeUnavailable("x"), "transient"),
    (IndexUnavailable("x"), "transient"),
    (ArtifactUnavailable("x"), "transient"),
    (AnswerSnapshotUnavailable("x"), "transient"),
    (PolicyUnavailable("x"), "transient"),
    (TimeoutError(), "transient"),
    (ConnectionError(), "transient"),
    (SAOperationalError("stmt", {}, Exception("orig")), "transient"),
    (SATimeoutError(), "transient"),
    (GraphCheckpointUnavailable("x"), "deterministic"),
    (ValueError("x"), "deterministic"),
    (RuntimeError("x"), "deterministic"),
    (RedactionUnavailable("x"), "deterministic"),
)


@pytest.mark.parametrize(
    "error, expected",
    _CLASSIFICATION_TABLE,
    ids=[type(error).__qualname__ for error, _ in _CLASSIFICATION_TABLE],
)
def test_classification_table(error: BaseException, expected: str) -> None:
    assert classify_turn_failure(error) == expected


def test_insights_authorization_change_is_deterministic_not_ownership() -> None:
    assert classify_turn_failure(InsightsAuthorizationChanged("x")) == "deterministic"


def test_plan_budget_timeout_is_deterministic_but_plain_timeout_is_transient() -> None:
    assert (
        classify_turn_failure(AnswerBudgetExhausted("persisted answer budget exhausted"))
        == "deterministic"
    )
    assert classify_turn_failure(TimeoutError()) == "transient"
    assert classify_turn_failure(GraphCheckpointRetryable()) == "transient"
    assert classify_turn_failure(GraphCheckpointUnavailable()) == "deterministic"


@pytest.mark.parametrize(
    "cause, expected",
    [
        (None, "deterministic"),
        (ValueError("x"), "deterministic"),
        (PolicyUnavailable("x"), "transient"),
        (SAOperationalError("stmt", {}, Exception("orig")), "transient"),
        (TimeoutError(), "transient"),
    ],
    ids=["no-cause", "deterministic-cause", "policy-unavailable", "operational-error", "timeout"],
)
def test_authorization_denied_classification_follows_its_cause(cause, expected) -> None:
    error = AuthorizationDenied("x")
    if cause is not None:
        try:
            raise error from cause
        except AuthorizationDenied as raised:
            assert classify_turn_failure(raised) == expected
            return
    assert classify_turn_failure(error) == expected


def test_retry_delay_is_exponential_capped_and_jittered() -> None:
    rng = random.Random(0)
    expected_bounds = (
        (1.0, 2.0),
        (2.0, 4.0),
        (4.0, 8.0),
        (8.0, 16.0),
        (15.0, 30.0),
        (15.0, 30.0),
    )
    for attempt, (lower, upper) in enumerate(expected_bounds, start=1):
        delay = retry_delay(attempt, rng=rng)
        assert isinstance(delay, timedelta)
        seconds = delay.total_seconds()
        assert lower <= seconds <= upper, (attempt, seconds)
    with pytest.raises(ValueError):
        retry_delay(0)


@pytest.mark.asyncio
async def test_gateway_rejection_is_deterministic_through_adapter() -> None:
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway

    async def redact(text: str) -> str:
        return text

    class RejectingGateway:
        async def chat(self, request):
            raise ModelGatewayRejected()

    model = KnowledgeModelGateway(
        RejectingGateway(),
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="embed",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=5,
    )
    with pytest.raises(AnswerRejected) as excinfo:
        await model.chat("hi", model_alias="qwen-plus", answer_plan_id="plan-a")
    assert classify_turn_failure(excinfo.value) == "deterministic"

    class UnavailableGateway:
        async def chat(self, request):
            raise ModelGatewayUnavailable()

    model = KnowledgeModelGateway(
        UnavailableGateway(),
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="embed",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=5,
    )
    with pytest.raises(AnswerUnavailable) as excinfo:
        await model.chat("hi", model_alias="qwen-plus", answer_plan_id="plan-a")
    assert classify_turn_failure(excinfo.value) == "transient"


@dataclass(frozen=True, slots=True)
class _FakePlan:
    deadline_at: float


@pytest.mark.asyncio
async def test_expired_plan_deadline_raises_answer_budget_exhausted() -> None:
    plan = _FakePlan(deadline_at=time.time() + 60)
    expired_plan = replace(plan, deadline_at=time.time() - 1)
    entered = False
    with pytest.raises(AnswerBudgetExhausted) as excinfo:
        async with KnowledgeHttpService._plan_deadline(expired_plan):
            entered = True
    assert not entered
    assert isinstance(excinfo.value, TimeoutError)
