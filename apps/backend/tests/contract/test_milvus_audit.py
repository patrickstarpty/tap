from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from test_milvus_filter import doc_execution
from test_milvus_search_strict import RecordingAuditSink, RecordingReader, config

from tap.modules.knowledge.adapters.milvus.audit import MilvusSearchAuditEvent
from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
from tap.modules.knowledge.ports.errors import SearchUnavailable

EVENT_KEYS = {
    "outcome",
    "provider",
    "query_plan_id",
    "acl_digest",
    "alias",
    "physical_collection",
    "schema_version",
    "corpus_version",
    "embedding_model_version",
    "provider_row_count",
    "rejected_row_count",
    "elapsed_milliseconds",
    "provider_request_ids",
    "error_code",
}


@pytest.mark.asyncio
async def test_success_emits_exactly_one_fixed_shape_event() -> None:
    """Adding arbitrary payload fields or duplicate emission would widen audit data."""
    audit = RecordingAuditSink()

    await MilvusSearchAdapter(config(), RecordingReader(), audit).search(doc_execution())

    assert len(audit.events) == 1
    event = audit.events[0]
    assert isinstance(event, MilvusSearchAuditEvent)
    assert set(asdict(event)) == EVENT_KEYS
    assert event.outcome == "success"
    assert event.provider == "milvus"
    assert event.physical_collection == "kb_doc_v1_corpus_fixture_v1"
    assert event.provider_row_count == 1
    assert event.rejected_row_count == 0
    assert event.provider_request_ids == ("milvus-request-v1",)
    assert event.error_code is None
    assert event.elapsed_milliseconds >= 0


@pytest.mark.asyncio
async def test_failure_emits_one_sanitized_event_without_sensitive_inputs() -> None:
    """Copying exception or request context into audit would expose restricted values."""
    execution = doc_execution()
    secret = "reader-password-do-not-log"
    group_id = next(iter(execution.policy.actor.allowed_group_ids))
    compiled_filter = 'tenant_id == "tenant-a" and allowed_group_ids == "group-one"'
    query_text = execution.plan.sanitized_query
    vector_fragment = str(execution.query_vector[:3])
    reader = RecordingReader(
        failure=RuntimeError(
            " | ".join((secret, group_id, compiled_filter, query_text, vector_fragment))
        )
    )
    audit = RecordingAuditSink()

    with pytest.raises(SearchUnavailable):
        await MilvusSearchAdapter(config(), reader, audit).search(execution)

    assert len(audit.events) == 1
    payload = json.dumps(asdict(audit.events[0]), sort_keys=True)
    assert set(asdict(audit.events[0])) == EVENT_KEYS
    assert audit.events[0].outcome == "failure"
    assert audit.events[0].error_code == "unavailable"
    for forbidden in (secret, group_id, compiled_filter, query_text, vector_fragment):
        assert forbidden not in payload


@pytest.mark.asyncio
async def test_audit_failure_after_valid_results_fails_closed_with_generic_detail() -> None:
    """Returning hits when required audit persistence fails would create an unaudited success."""
    audit = RecordingAuditSink(RuntimeError("audit backend leaked detail"))

    with pytest.raises(SearchUnavailable, match="search audit is unavailable") as raised:
        await MilvusSearchAdapter(config(), RecordingReader(), audit).search(doc_execution())

    assert len(audit.events) == 1
    assert str(raised.value) == "search audit is unavailable"
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
async def test_audit_failure_cannot_mask_the_original_provider_error() -> None:
    """Replacing the provider error with an audit error would hide the primary failure."""
    provider_error = SearchUnavailable("primary provider failure")
    reader = RecordingReader(failure=provider_error)
    audit = RecordingAuditSink(RuntimeError("secondary audit failure"))

    with pytest.raises(SearchUnavailable) as raised:
        await MilvusSearchAdapter(config(), reader, audit).search(doc_execution())

    assert raised.value is provider_error
    assert len(audit.events) == 1
