"""Mutation-sensitive public Retrieval HTTP DTO and schema contracts."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tap.contracts.http import (
    BddAnchor,
    ChatTurnRequest,
    CodeAnchor,
    DocumentAnchor,
    FailureAnchor,
    OpenApiAnchor,
    ResourceRef,
    RetrievalAnswerRequest,
    RetrievalAnswerResponse,
    RetrievalCitation,
    RetrievalClaim,
    RetrievalHit,
    RetrievalScores,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
    RetrievalSourceRevision,
    StructuralAnchor,
)

PUBLIC_FIELDS: tuple[tuple[type[Any], set[str]], ...] = (
    (
        ChatTurnRequest,
        {
            "clientRequestId",
            "message",
            "answerMode",
            "sourceScope",
            "resourceRefs",
            "requestedEnvironment",
            "requestedCorpusVersion",
        },
    ),
    (
        RetrievalSearchRequest,
        {
            "query",
            "answerMode",
            "sources",
            "resourceRefs",
            "requestedEnvironment",
            "requestedCorpusVersion",
            "topK",
        },
    ),
    (
        RetrievalAnswerRequest,
        {
            "query",
            "answerMode",
            "sources",
            "resourceRefs",
            "requestedEnvironment",
            "requestedCorpusVersion",
            "topK",
        },
    ),
    (
        ResourceRef,
        {"family", "sourceId", "mode", "requestedRevision", "anchor"},
    ),
    (
        RetrievalSourceRevision,
        {
            "sourceId",
            "sourceType",
            "revisionKind",
            "revision",
            "sourceContentHash",
            "anchor",
        },
    ),
    (
        RetrievalScores,
        {"exact", "bm25", "vector", "rrf", "rerank"},
    ),
    (
        RetrievalHit,
        {
            "indexFamily",
            "chunkId",
            "logicalChunkId",
            "title",
            "content",
            "source",
            "chunkContentHash",
            "contentRole",
            "citationId",
            "evidenceLabel",
            "scores",
            "aclDecisionId",
            "schemaVersion",
            "embeddingModelVersion",
        },
    ),
    (
        RetrievalCitation,
        {
            "citationId",
            "evidenceLabel",
            "chunkId",
            "logicalChunkId",
            "source",
            "chunkContentHash",
            "contentRole",
            "derivedFromChunkIds",
        },
    ),
    (RetrievalClaim, {"claimId", "text", "citationIds"}),
    (
        RetrievalSearchResponse,
        {
            "traceId",
            "queryPlanId",
            "contextSnapshotId",
            "corpusVersion",
            "retrievalProfileId",
            "degradedMode",
            "degradationReasons",
            "hits",
        },
    ),
    (
        RetrievalAnswerResponse,
        {
            "traceId",
            "queryPlanId",
            "contextSnapshotId",
            "corpusVersion",
            "retrievalProfileId",
            "degradedMode",
            "degradationReasons",
            "answer",
            "abstained",
            "abstentionReason",
            "claims",
            "citations",
        },
    ),
)

ANCHOR_FIELDS: tuple[tuple[type[Any], set[str]], ...] = (
    (
        DocumentAnchor,
        {"type", "headingPath", "page", "bbox", "startOffset", "endOffset"},
    ),
    (CodeAnchor, {"type", "repo", "path", "symbol", "lineStart", "lineEnd"}),
    (BddAnchor, {"type", "featureId", "scenarioId", "stepId"}),
    (OpenApiAnchor, {"type", "method", "path", "jsonPointer"}),
    (
        FailureAnchor,
        {"type", "incidentId", "runId", "timeStart", "timeEnd"},
    ),
)


@pytest.mark.parametrize(("model", "expected"), PUBLIC_FIELDS + ANCHOR_FIELDS)
def test_public_models_have_an_exact_literal_field_set(
    model: type[Any], expected: set[str]
) -> None:
    """Field-by-field allowlists catch public policy/provider leakage and accidental widening."""
    schema = model.model_json_schema(by_alias=True)
    assert set(schema["properties"]) == expected


def test_every_public_object_schema_is_recursively_closed() -> None:
    """Nested anchors and response values must not acquire undeclared JSON properties."""
    for model, _expected in PUBLIC_FIELDS + ANCHOR_FIELDS:
        _assert_objects_closed(model.model_json_schema(by_alias=True))
    _assert_objects_closed(StructuralAnchor.model_json_schema(by_alias=True))


@pytest.mark.parametrize("top_k", [True, False, 1.0, 2.5])
def test_public_top_k_is_a_strict_json_integer(top_k: object) -> None:
    with pytest.raises(ValidationError):
        RetrievalSearchRequest.model_validate({"query": "authorization", "topK": top_k})


@pytest.mark.parametrize(
    ("model", "payload", "field", "value"),
    [
        (DocumentAnchor, {"type": "document"}, "page", True),
        (DocumentAnchor, {"type": "document"}, "page", 1.5),
        (DocumentAnchor, {"type": "document"}, "startOffset", False),
        (DocumentAnchor, {"type": "document"}, "startOffset", 2.5),
        (DocumentAnchor, {"type": "document"}, "endOffset", True),
        (DocumentAnchor, {"type": "document"}, "endOffset", 2.5),
        (
            CodeAnchor,
            {"type": "code", "repo": "tap", "path": "a.py", "lineStart": 1, "lineEnd": 2},
            "lineStart",
            True,
        ),
        (
            CodeAnchor,
            {"type": "code", "repo": "tap", "path": "a.py", "lineStart": 1, "lineEnd": 2},
            "lineStart",
            1.5,
        ),
        (
            CodeAnchor,
            {"type": "code", "repo": "tap", "path": "a.py", "lineStart": 1, "lineEnd": 2},
            "lineEnd",
            False,
        ),
        (
            CodeAnchor,
            {"type": "code", "repo": "tap", "path": "a.py", "lineStart": 1, "lineEnd": 2},
            "lineEnd",
            2.5,
        ),
    ],
)
def test_every_public_anchor_integer_is_strict(
    model: type[Any], payload: dict[str, object], field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**payload, field: value})


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            DocumentAnchor,
            {"type": "document", "startOffset": 20, "endOffset": 10},
        ),
        (
            CodeAnchor,
            {
                "type": "code",
                "repo": "tap",
                "path": "a.py",
                "lineStart": 20,
                "lineEnd": 10,
            },
        ),
    ],
)
def test_public_anchor_ranges_are_ordered(model: type[Any], payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ChatTurnRequest, {"clientRequestId": "x" * 257, "message": "hello"}),
        (ChatTurnRequest, {"clientRequestId": "request-1", "message": "x" * 8_001}),
        (
            ChatTurnRequest,
            {"clientRequestId": "request-1", "message": "hello", "sourceScope": ["code"] * 5},
        ),
        (
            ChatTurnRequest,
            {
                "clientRequestId": "request-1",
                "message": "hello",
                "resourceRefs": [
                    {"family": "code", "sourceId": f"repo:tap:{index}"} for index in range(21)
                ],
            },
        ),
        (
            ChatTurnRequest,
            {"clientRequestId": "request-1", "message": "hello", "requestedEnvironment": "x" * 129},
        ),
        (
            ChatTurnRequest,
            {
                "clientRequestId": "request-1",
                "message": "hello",
                "requestedCorpusVersion": "x" * 129,
            },
        ),
        (ResourceRef, {"family": "code", "sourceId": "x" * 1_025}),
        (
            ResourceRef,
            {"family": "code", "sourceId": "repo:tap:a.py", "requestedRevision": "x" * 513},
        ),
        (DocumentAnchor, {"type": "document", "headingPath": ["x"] * 33}),
        (DocumentAnchor, {"type": "document", "headingPath": ["x" * 257]}),
        (DocumentAnchor, {"type": "document", "bbox": [0.0] * 5}),
        (
            CodeAnchor,
            {"type": "code", "repo": "x" * 257, "path": "a.py", "lineStart": 1, "lineEnd": 1},
        ),
        (
            CodeAnchor,
            {"type": "code", "repo": "tap", "path": "x" * 2_049, "lineStart": 1, "lineEnd": 1},
        ),
        (
            CodeAnchor,
            {
                "type": "code",
                "repo": "tap",
                "path": "a.py",
                "symbol": "x" * 513,
                "lineStart": 1,
                "lineEnd": 1,
            },
        ),
        (BddAnchor, {"type": "bdd", "featureId": "x" * 257}),
        (BddAnchor, {"type": "bdd", "featureId": "feature", "scenarioId": "x" * 257}),
        (BddAnchor, {"type": "bdd", "featureId": "feature", "stepId": "x" * 257}),
        (OpenApiAnchor, {"type": "openapi", "method": "x" * 17, "path": "/a", "jsonPointer": "/x"}),
        (
            OpenApiAnchor,
            {"type": "openapi", "method": "GET", "path": "x" * 2_049, "jsonPointer": "/x"},
        ),
        (
            OpenApiAnchor,
            {"type": "openapi", "method": "GET", "path": "/a", "jsonPointer": "x" * 4_097},
        ),
        (FailureAnchor, {"type": "failure", "incidentId": "x" * 257}),
        (FailureAnchor, {"type": "failure", "incidentId": "incident", "runId": "x" * 257}),
        (FailureAnchor, {"type": "failure", "incidentId": "incident", "timeStart": "x" * 129}),
        (FailureAnchor, {"type": "failure", "incidentId": "incident", "timeEnd": "x" * 129}),
    ],
)
def test_every_browser_controlled_string_list_and_anchor_is_bounded(
    model: type[Any], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query", 17),
        ("answerMode", {}),
        ("sources", "code"),
        ("resourceRefs", {}),
        ("requestedEnvironment", 17),
        ("requestedCorpusVersion", 17),
    ],
)
def test_retrieval_request_fields_reject_wrong_json_types(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RetrievalAnswerRequest.model_validate({"query": "authorization", field: value})


def _assert_objects_closed(value: object) -> None:
    if isinstance(value, dict):
        if "properties" in value:
            assert value.get("additionalProperties") is False
        for nested in value.values():
            _assert_objects_closed(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_objects_closed(nested)
