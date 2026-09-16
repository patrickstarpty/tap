"""Operator authorization precedes effects; receipts preserve observed completion."""

from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE, ValidationAuthorizationPolicy
from tap.modules.access.domain.authorization import ActorPrincipal
from tap.modules.access.domain.policy import AuthorizationDenied


class Registry:
    enabled = True

    async def get_principal(self, enterprise_id, project_id, actor_id):
        return ActorPrincipal(
            enterprise_id=enterprise_id,
            actor_id=actor_id,
            principal_type="VALIDATION",
            enabled=self.enabled,
        )


def test_operator_request_is_closed_and_bounded():
    from tap.modules.knowledge.domain.operations import OperationRequest

    for command, limit, key in [
        ("delete-all", 1, "key"),
        ("recover-uploads", -1, "key"),
        ("recover-uploads", True, "key"),
        ("recover-uploads", 501, "key"),
        ("recover-uploads", 1, ""),
    ]:
        with pytest.raises((ValueError, TypeError)):
            OperationRequest(
                command=command, limit=limit, idempotency_key=key, correlation_id="request"
            )


@pytest.mark.asyncio
async def test_operator_policy_explicit_operate_and_rechecks_disable():
    from tap.modules.access.domain.authorization import ResourceRef

    registry = Registry()
    policy = ValidationAuthorizationPolicy(registry)
    resource = ResourceRef(enterprise_id="local", project_id="tapper-demo", kind="knowledge")
    assert (await policy.authorize(VALIDATION_SCOPE, "knowledge.operate", resource)).allowed
    registry.enabled = False
    assert not (await policy.authorize(VALIDATION_SCOPE, "knowledge.operate", resource)).allowed


@pytest.mark.asyncio
async def test_operator_denial_or_mismatched_bindings_precedes_receipt_and_effects():
    from tap.modules.knowledge.application.operations import KnowledgeOperator
    from tap.modules.knowledge.domain.operations import OperationRequest

    class Forbidden:
        scope = VALIDATION_SCOPE

        async def claim(self, *args, **kwargs):
            raise AssertionError("unauthorized receipt")

        async def execute(self, *args, **kwargs):
            raise AssertionError("unauthorized effects")

    registry = Registry()
    registry.enabled = False
    operator = KnowledgeOperator(
        scope=VALIDATION_SCOPE,
        policy=ValidationAuthorizationPolicy(registry),
        repository=Forbidden(),
        effects=Forbidden(),
    )
    with pytest.raises(AuthorizationDenied):
        await operator.run(
            OperationRequest(
                command="recover-uploads", limit=1, idempotency_key="key", correlation_id="request"
            )
        )
    for changed in [
        replace(VALIDATION_SCOPE, project_id="other"),
        replace(VALIDATION_SCOPE, enterprise_id="other"),
    ]:
        with pytest.raises(ValueError, match="scope"):
            KnowledgeOperator(
                scope=changed,
                policy=ValidationAuthorizationPolicy(registry),
                repository=Forbidden(),
                effects=Forbidden(),
            )


def test_operator_cli_requires_explicit_project_and_valid_arguments_before_runtime():
    from tap.entrypoints.knowledge_operator import parse_arguments

    for arguments in [
        ["recover-uploads"],
        ["recover-uploads", "--project", ""],
        ["recover-uploads", "--project", "other"],
        ["recover-uploads", "--project", "tapper-demo", "--limit", "-1"],
        ["unknown", "--project", "tapper-demo"],
    ]:
        with pytest.raises((ValueError, SystemExit)):
            parse_arguments(arguments)
    first = parse_arguments(["recover-uploads", "--project", "tapper-demo", "--key", "retry"])
    assert first.idempotency_key == "retry"


def test_operator_completed_event_has_closed_command_outcome_and_version():
    from datetime import datetime, timezone

    from tap.contracts.events import ProjectEventEnvelope

    values = dict(
        event_id="event",
        event_type="knowledge.operator.completed",
        schema_version=1,
        occurred_at=datetime.now(timezone.utc),
        scope_kind="PROJECT",
        enterprise_id="local",
        project_id="tapper-demo",
        actor_id="tapper-local-user",
        identity_mode="validation",
        aggregate_type="KnowledgeOperation",
        aggregate_id="operation",
        aggregate_version=1,
        correlation_id="request",
        causation_id=None,
        idempotency_key="operation",
        payload={
            "operationId": "operation",
            "command": "recover-uploads",
            "outcome": "completed",
            "resultDigest": "sha256:" + "a" * 64,
        },
    )
    assert ProjectEventEnvelope(**values).aggregate_version == 1
    for override in [
        {"aggregate_version": 2},
        {"payload": {**values["payload"], "command": "delete-all"}},
        {"payload": {**values["payload"], "outcome": "running"}},
        {"payload": {**values["payload"], "resultDigest": "provider-private-error"}},
    ]:
        with pytest.raises(ValueError):
            ProjectEventEnvelope(**{**values, **override})


@pytest.mark.parametrize(
    "name,value",
    [
        (
            "TAP_DATABASE_URL",
            "mysql+asyncmy://user:private@production.example:3306/tap?charset=utf8mb4",
        ),
        ("TAP_REDIS_URL", "redis://production.example:6379/0"),
    ],
)
def test_operator_cli_rejects_nonlocal_configuration_before_runtime(
    monkeypatch, capsys, name, value
):
    from tap.entrypoints import knowledge_operator

    calls = []

    async def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("configuration must be rejected before runtime")

    monkeypatch.setattr(knowledge_operator, "run", forbidden)
    assert (
        knowledge_operator.cli(["recover-uploads", "--project", "tapper-demo"], {name: value}) == 1
    )
    output = capsys.readouterr()
    assert "private" not in output.err
    assert "production.example" not in output.err
    assert output.out == ""
    assert calls == []


@pytest.mark.asyncio
async def test_operator_takeover_and_partial_failure_report_only_observed_counts():
    from datetime import timedelta

    from tap.modules.knowledge.application.operations import KnowledgeOperator
    from tap.modules.knowledge.domain.operations import OperationClaim, OperationRequest

    class Repository:
        scope = VALIDATION_SCOPE

        async def claim(self, request, *, lease_duration):
            return OperationClaim(
                scope=self.scope,
                operation_id="operation",
                command=request.command,
                limit=request.limit,
                correlation_id="original",
                fence=2,
                claim_token="claim",
            )

        async def renew(self, claim, *, lease_duration):
            pass

        async def complete(self, claim, result):
            return replace(claim, result=result)

    class Effects:
        scope = VALIDATION_SCOPE

        async def execute(self, command, *, limit):
            if command == "scavenge-staging":
                raise RuntimeError("private provider details")
            return {"processed_count": 1}

    operator = KnowledgeOperator(
        scope=VALIDATION_SCOPE,
        policy=ValidationAuthorizationPolicy(Registry()),
        repository=Repository(),
        effects=Effects(),
        lease_duration=timedelta(seconds=3),
    )
    result = await operator.run(
        OperationRequest(
            command="reconcile-all", limit=1, idempotency_key="key", correlation_id="retry"
        )
    )
    assert result.correlation_id == "original"
    assert result.result.outcome == "partial"
    assert dict(result.result.counts) == {"processed_count": 3, "failed_count": 1}
