"""Private domain events validate a closed, immutable versioned registry."""

from datetime import datetime, timezone

import pytest

from tap.contracts.events import ProjectEventEnvelope, event_content_digest


def domain_event(**changes: object) -> ProjectEventEnvelope:
    values = dict(
        event_id="event-1",
        event_type="knowledge.graph-snapshot.requested",
        schema_version=1,
        occurred_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        scope_kind="PROJECT",
        enterprise_id="enterprise-a",
        project_id="project-a",
        actor_id="actor-a",
        identity_mode="validation",
        aggregate_type="GraphSnapshot",
        aggregate_id="snapshot-1",
        aggregate_version=1,
        correlation_id="correlation-1",
        causation_id=None,
        idempotency_key="snapshot-1",
        payload={
            "snapshotId": "snapshot-1",
            "sourceRevisionIds": ["opaque-revision"],
            "extractionProfileDigest": "sha256:profile",
        },
    )
    values.update(changes)
    return ProjectEventEnvelope(**values)


def test_domain_event_roundtrip_deep_freezes_input_and_output_arrays() -> None:
    revisions = ["opaque-revision"]
    event = domain_event(
        payload={
            "snapshotId": "snapshot-1",
            "sourceRevisionIds": revisions,
            "extractionProfileDigest": "sha256:profile",
        }
    )
    revisions.append("other")
    assert event.payload["sourceRevisionIds"] == ("opaque-revision",)
    encoded = event.to_dict()
    encoded["payload"]["sourceRevisionIds"].append("external")
    assert ProjectEventEnvelope.from_dict(event.to_dict()) == event
    assert event.payload["sourceRevisionIds"] == ("opaque-revision",)


@pytest.mark.parametrize("field", list(ProjectEventEnvelope.__dataclass_fields__))
def test_missing_envelope_field_is_rejected(field: str) -> None:
    encoded = domain_event().to_dict()
    del encoded[field]
    with pytest.raises(ValueError):
        ProjectEventEnvelope.from_dict(encoded)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"scope_kind": "PLATFORM"},
        {"event_type": "provider.raw"},
        {"aggregate_type": "ExecutionRun"},
        {"aggregate_version": 0},
        {"aggregate_version": True},
        {
            "payload": {
                "snapshotId": "other",
                "sourceRevisionIds": ["revision"],
                "extractionProfileDigest": "digest",
            }
        },
        {
            "payload": {
                "snapshotId": "snapshot-1",
                "sourceRevisionIds": "revision",
                "extractionProfileDigest": "digest",
            }
        },
        {
            "payload": {
                "snapshotId": "snapshot-1",
                "sourceRevisionIds": ["revision"],
                "extractionProfileDigest": "digest",
                "secret": "provider-token",
            }
        },
    ],
)
def test_unknown_major_malformed_or_aggregate_contradiction_rejected(changes: dict) -> None:
    with pytest.raises(ValueError):
        domain_event(**changes)


def test_shape_validation_does_not_guess_opaque_resource_project_membership() -> None:
    event = domain_event()
    assert event.payload["sourceRevisionIds"] == ("opaque-revision",)
    # Membership must be verified by the owning scoped application transaction.


def test_canonical_event_digest_excludes_delivery_metadata_but_includes_content() -> None:
    event = domain_event()
    replay = domain_event(event_id="event-2", correlation_id="another", causation_id="parent")
    changed = domain_event(aggregate_version=2)
    assert event_content_digest(replay) == event_content_digest(event)
    assert event_content_digest(changed) != event_content_digest(event)


@pytest.mark.parametrize(
    "event_type,aggregate,identity,payload",
    [
        (
            "knowledge.document-revision.accepted",
            "DocumentRevision",
            "revisionId",
            {"sourceId": "s", "documentId": "d", "revisionId": "a", "contentHash": "hash"},
        ),
        (
            "knowledge.document-revision.ready",
            "DocumentRevision",
            "revisionId",
            {"revisionId": "a", "chunkManifestDigest": "digest", "projectionDigest": "digest"},
        ),
        (
            "knowledge.graph-snapshot.ready",
            "GraphSnapshot",
            "snapshotId",
            {"snapshotId": "a", "graphDigest": "digest", "evidenceDigest": "digest"},
        ),
        (
            "conversation.turn.requested",
            "Turn",
            "turnId",
            {"conversationId": "c", "turnId": "a", "inputSnapshotDigest": "digest"},
        ),
        (
            "conversation.turn.completed",
            "Turn",
            "turnId",
            {
                "turnId": "a",
                "answerEvidenceSnapshotId": "s",
                "answerEvidenceSnapshotDigest": "digest",
                "outcome": "completed",
            },
        ),
        (
            "test-plan.generation.requested",
            "TestPlanRevision",
            "revisionId",
            {
                "revisionId": "a",
                "inputSnapshotDigest": "digest",
                "answerEvidenceSnapshotDigest": "digest",
                "requestDigest": "digest",
            },
        ),
        (
            "test-plan.revision.published",
            "TestPlanRevision",
            "revisionId",
            {"revisionId": "a", "contentDigest": "digest", "validationDigest": "digest"},
        ),
        (
            "automation.generation.requested",
            "AutomationRevision",
            "revisionId",
            {
                "revisionId": "a",
                "inputSnapshotDigest": "digest",
                "answerEvidenceSnapshotDigest": "digest",
                "requestDigest": "digest",
            },
        ),
        (
            "automation.revision.published",
            "AutomationRevision",
            "revisionId",
            {"revisionId": "a", "testIrDigest": "digest", "bundleManifestDigest": "digest"},
        ),
        (
            "automation.debug-execution.requested",
            "DebugExecution",
            "debugExecutionId",
            {"debugExecutionId": "a", "draftDigest": "digest", "environmentRevisionId": "e"},
        ),
        (
            "automation.debug-execution.status-changed",
            "DebugExecution",
            "debugExecutionId",
            {"debugExecutionId": "a", "from": "QUEUED", "to": "RUNNING", "sequence": 1},
        ),
        (
            "automation.debug-execution.completed",
            "DebugExecution",
            "debugExecutionId",
            {"debugExecutionId": "a", "outcome": "PASSED", "evidenceManifestDigest": "digest"},
        ),
        (
            "recorder.session.requested",
            "RecorderSession",
            "sessionId",
            {"sessionId": "a", "environmentRevisionId": "e", "policyDigest": "digest"},
        ),
        (
            "recorder.session.completed",
            "RecorderSession",
            "sessionId",
            {"sessionId": "a", "eventManifestDigest": "digest", "outcome": "SUCCEEDED"},
        ),
        (
            "execution.run.requested",
            "ExecutionRun",
            "runId",
            {"runId": "a", "submissionKey": "a:1", "configurationManifestDigest": "digest"},
        ),
        (
            "execution.run.status-changed",
            "ExecutionRun",
            "runId",
            {"runId": "a", "from": "QUEUED", "to": "RUNNING", "sequence": 1, "observationId": "o"},
        ),
        (
            "execution.run.completed",
            "ExecutionRun",
            "runId",
            {
                "runId": "a",
                "outcome": "PASSED",
                "evidenceStatus": "INCOMPLETE",
                "resultManifestDigest": "digest",
            },
        ),
    ],
)
def test_normative_domain_payload_roundtrips_and_rejects_missing_fields(
    event_type: str,
    aggregate: str,
    identity: str,
    payload: dict,
) -> None:
    event = domain_event(
        event_type=event_type, aggregate_type=aggregate, aggregate_id="a", payload=payload
    )
    assert ProjectEventEnvelope.from_dict(event.to_dict()).payload[identity] == "a"
    for field in payload:
        invalid = dict(payload)
        del invalid[field]
        with pytest.raises(ValueError):
            domain_event(
                event_type=event_type, aggregate_type=aggregate, aggregate_id="a", payload=invalid
            )
    if "sequence" in payload:
        with pytest.raises(ValueError, match="version differs"):
            domain_event(
                event_type=event_type,
                aggregate_type=aggregate,
                aggregate_id="a",
                payload=payload,
                aggregate_version=2,
            )
    if "outcome" in payload:
        with pytest.raises(ValueError, match="registered event value"):
            domain_event(
                event_type=event_type,
                aggregate_type=aggregate,
                aggregate_id="a",
                payload={**payload, "outcome": "SUCCESS"},
            )


def test_private_schema_uses_registered_payloads_and_bounds() -> None:
    from tap.contracts.events import project_event_schema

    schema = project_event_schema()
    variants = {item["properties"]["event_type"]["const"]: item for item in schema["oneOf"]}
    assert len(variants) == 23
    requested = variants["conversation.turn.requested"]
    assert set(requested["properties"]["payload"]["required"]) == {
        "conversationId",
        "turnId",
        "inputSnapshotDigest",
    }
    assert requested["additionalProperties"] is False
    assert requested["properties"]["payload"]["additionalProperties"] is False
    assert requested["properties"]["aggregate_version"]["minimum"] == 1
    compatibility = variants["chat.event_appended"]
    assert compatibility["properties"]["aggregate_version"]["minimum"] == 0
    assert {"type": "null"} in compatibility["properties"]["payload"]["properties"]["sequence"][
        "anyOf"
    ]


def test_event_aggregate_id_fits_existing_outbox_storage_before_sql() -> None:
    with pytest.raises(ValueError, match="aggregate_id"):
        domain_event(
            aggregate_id="a" * 65,
            payload={
                "snapshotId": "a" * 65,
                "sourceRevisionIds": ["r"],
                "extractionProfileDigest": "digest",
            },
        )


@pytest.mark.parametrize("timestamp", ["2026-09-05X00:00:00+00:00", "20260905T000000Z"])
def test_persisted_timestamp_requires_rfc3339_format(timestamp: str) -> None:
    encoded = domain_event().to_dict()
    encoded["occurred_at"] = timestamp
    with pytest.raises(ValueError, match="timestamp"):
        ProjectEventEnvelope.from_dict(encoded)


@pytest.mark.parametrize(
    "timestamp",
    [
        "9999-12-31T23:59:59-23:59",
        "0001-01-01T00:00:00+23:59",
    ],
)
def test_event_rejects_instants_that_cannot_be_represented_in_canonical_utc(timestamp: str) -> None:
    with pytest.raises(ValueError, match="UTC"):
        domain_event(occurred_at=datetime.fromisoformat(timestamp))
    encoded = domain_event().to_dict()
    encoded["occurred_at"] = timestamp
    with pytest.raises(ValueError, match="UTC"):
        ProjectEventEnvelope.from_dict(encoded)


def test_document_revision_event_preserves_real_68_character_identity():
    from tap.contracts.events import project_event_schema

    revision = "rev_" + "a" * 64
    event = domain_event(
        event_type="knowledge.document-revision.accepted",
        aggregate_type="DocumentRevision",
        aggregate_id=revision,
        idempotency_key=revision + ":ingest",
        payload={
            "sourceId": "src_1",
            "documentId": "doc_1",
            "revisionId": revision,
            "contentHash": "sha256:" + "a" * 64,
        },
    )
    branch = next(
        branch
        for branch in project_event_schema()["oneOf"]
        if branch["properties"]["event_type"]["const"] == event.event_type
    )
    assert branch["properties"]["aggregate_id"]["maxLength"] == 128
    assert branch["properties"]["payload"]["properties"]["revisionId"]["maxLength"] == 128
    with pytest.raises(ValueError):
        domain_event(aggregate_id="x" * 65)
    with pytest.raises(ValueError):
        domain_event(
            event_type="knowledge.document-revision.ready",
            aggregate_type="DocumentRevision",
            aggregate_id="x" * 129,
        )
