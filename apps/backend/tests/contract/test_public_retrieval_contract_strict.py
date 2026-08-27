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

SOURCE_HASH = "sha256:" + "a" * 64
CHUNK_HASH = "sha256:" + "b" * 64


def public_source_revision_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sourceId": "repo:tap:authorization.py",
        "sourceType": "code",
        "revisionKind": "git_commit",
        "revision": "a" * 40,
        "sourceContentHash": SOURCE_HASH,
        "anchor": {
            "type": "code",
            "repo": "tap",
            "path": "authorization.py",
            "symbol": "authorize",
            "lineStart": 1,
            "lineEnd": 10,
        },
    }
    payload.update(changes)
    return payload


def public_hit_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "indexFamily": "code",
        "chunkId": "h_" + "1" * 64,
        "logicalChunkId": "h_" + "2" * 64,
        "title": "authorize",
        "content": "Authorization uses current policy.",
        "source": public_source_revision_payload(),
        "chunkContentHash": CHUNK_HASH,
        "contentRole": "source",
        "citationId": "citation-17",
        "evidenceLabel": "S1",
        "scores": {"rrf": 1 / 61},
        "aclDecisionId": "decision-17",
        "schemaVersion": "search-schema-v1",
        "embeddingModelVersion": "tap-embed-fixed-v1",
    }
    payload.update(changes)
    return payload


def public_citation_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "citationId": "citation-17",
        "evidenceLabel": "S1",
        "chunkId": "h_" + "1" * 64,
        "logicalChunkId": "h_" + "2" * 64,
        "source": public_source_revision_payload(),
        "chunkContentHash": CHUNK_HASH,
        "contentRole": "source",
    }
    payload.update(changes)
    return payload


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
    (RetrievalClaim, {"claimId", "text", "answerStart", "answerEnd", "citationIds"}),
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


@pytest.mark.parametrize("field", ["exact", "bm25", "vector", "rrf", "rerank"])
@pytest.mark.parametrize(
    "value",
    [True, False, "0.5", float("nan"), float("inf"), float("-inf")],
    ids=("true", "false", "numeric-string", "nan", "positive-inf", "negative-inf"),
)
def test_every_public_score_is_a_strict_finite_number(
    field: str,
    value: object,
) -> None:
    """Coercion or non-finite serialization must fail for every public score slot."""
    with pytest.raises(ValidationError):
        RetrievalScores.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "A" * 40),
        ("revision", "g" * 40),
        ("revision", "a" * 39),
        ("sourceContentHash", "sha256:" + "A" * 64),
        ("sourceContentHash", "sha256:" + "g" * 64),
        ("sourceContentHash", "sha256:" + "a" * 63),
    ],
    ids=(
        "git-uppercase",
        "git-non-hex",
        "git-wrong-length",
        "source-hash-uppercase",
        "source-hash-non-hex",
        "source-hash-wrong-length",
    ),
)
def test_public_source_revision_rejects_noncanonical_git_and_hash_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        RetrievalSourceRevision.model_validate(public_source_revision_payload(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sourceId", "x" * 1_025),
        ("sourceType", "x" * 129),
        ("revision", "x" * 513),
        ("sourceId", 17),
        ("sourceType", 17),
        ("revision", 17),
    ],
    ids=(
        "source-id-length",
        "source-type-length",
        "revision-length",
        "source-id-type",
        "source-type-type",
        "revision-type",
    ),
)
def test_public_source_provenance_strings_are_strict_and_bounded(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        RetrievalSourceRevision.model_validate(public_source_revision_payload(**{field: value}))


@pytest.mark.parametrize(
    ("source_type", "revision_kind", "revision", "anchor"),
    [
        (
            "document",
            "blob_version",
            "etag:opaque-blob-version-17",
            {"type": "document", "headingPath": ["Authorization"], "page": 1},
        ),
        (
            "failure",
            "mysql_version",
            "mysql-bin.000017:42",
            {"type": "failure", "incidentId": "incident-17"},
        ),
    ],
)
def test_public_source_revision_preserves_bounded_opaque_non_git_formats(
    source_type: str,
    revision_kind: str,
    revision: str,
    anchor: dict[str, object],
) -> None:
    result = RetrievalSourceRevision.model_validate(
        public_source_revision_payload(
            sourceType=source_type,
            revisionKind=revision_kind,
            revision=revision,
            anchor=anchor,
        )
    )

    assert result.revision == revision


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (RetrievalHit, public_hit_payload(chunkContentHash="sha256:" + "A" * 64)),
        (RetrievalHit, public_hit_payload(chunkContentHash="sha256:" + "g" * 64)),
        (RetrievalHit, public_hit_payload(chunkContentHash="sha256:" + "b" * 63)),
        (RetrievalCitation, public_citation_payload(chunkContentHash="sha256:" + "A" * 64)),
        (RetrievalCitation, public_citation_payload(chunkContentHash="sha256:" + "g" * 64)),
        (RetrievalCitation, public_citation_payload(chunkContentHash="sha256:" + "b" * 63)),
    ],
    ids=(
        "hit-uppercase",
        "hit-non-hex",
        "hit-wrong-length",
        "citation-uppercase",
        "citation-non-hex",
        "citation-wrong-length",
    ),
)
def test_every_public_chunk_hash_is_canonical(
    model: type[Any],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("revision_kind", "revision", "anchor"),
    [
        (
            "blob_version",
            "etag:blob-version-17",
            {
                "type": "code",
                "repo": "tap",
                "path": "authorization.py",
                "lineStart": 1,
                "lineEnd": 10,
            },
        ),
        (
            "mysql_version",
            "mysql-bin.000017:42",
            {
                "type": "code",
                "repo": "tap",
                "path": "authorization.py",
                "lineStart": 1,
                "lineEnd": 10,
            },
        ),
        (
            "blob_version",
            "etag:blob-version-17",
            {"type": "bdd", "featureId": "authorization"},
        ),
        (
            "mysql_version",
            "mysql-bin.000017:42",
            {"type": "bdd", "featureId": "authorization"},
        ),
        (
            "git_commit",
            "a" * 40,
            {"type": "document", "headingPath": ["Authorization"], "page": 1},
        ),
        (
            "mysql_version",
            "mysql-bin.000017:42",
            {"type": "document", "headingPath": ["Authorization"], "page": 1},
        ),
        (
            "git_commit",
            "a" * 40,
            {
                "type": "openapi",
                "method": "GET",
                "path": "/authorization",
                "jsonPointer": "/paths/~1authorization/get",
            },
        ),
        (
            "mysql_version",
            "mysql-bin.000017:42",
            {
                "type": "openapi",
                "method": "GET",
                "path": "/authorization",
                "jsonPointer": "/paths/~1authorization/get",
            },
        ),
        (
            "git_commit",
            "a" * 40,
            {"type": "failure", "incidentId": "incident-17"},
        ),
        (
            "blob_version",
            "etag:blob-version-17",
            {"type": "failure", "incidentId": "incident-17"},
        ),
    ],
    ids=(
        "code-blob",
        "code-mysql",
        "bdd-blob",
        "bdd-mysql",
        "document-git",
        "document-mysql",
        "openapi-git",
        "openapi-mysql",
        "failure-git",
        "failure-blob",
    ),
)
def test_public_source_revision_rejects_every_revision_anchor_mismatch(
    revision_kind: str,
    revision: str,
    anchor: dict[str, object],
) -> None:
    """Removing any anchor/kind branch must authorize one incompatible source shape."""
    with pytest.raises(ValidationError):
        RetrievalSourceRevision.model_validate(
            public_source_revision_payload(
                sourceType="route_specific_subtype",
                revisionKind=revision_kind,
                revision=revision,
                anchor=anchor,
            )
        )


@pytest.mark.parametrize(
    ("source_type", "revision_kind", "revision", "anchor"),
    [
        (
            "bdd",
            "git_commit",
            "a" * 40,
            {
                "type": "code",
                "repo": "tap",
                "path": "authorization.py",
                "lineStart": 1,
                "lineEnd": 10,
            },
        ),
        (
            "failure",
            "git_commit",
            "a" * 40,
            {"type": "bdd", "featureId": "authorization"},
        ),
        (
            "code",
            "blob_version",
            "etag:blob-version-17",
            {"type": "document", "headingPath": ["Authorization"], "page": 1},
        ),
        (
            "document",
            "mysql_version",
            "mysql-bin.000017:42",
            {"type": "failure", "incidentId": "incident-17"},
        ),
    ],
    ids=("code-as-bdd", "bdd-as-failure", "doc-as-code", "failure-as-document"),
)
def test_public_source_revision_rejects_known_source_type_contradictions(
    source_type: str,
    revision_kind: str,
    revision: str,
    anchor: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RetrievalSourceRevision.model_validate(
            public_source_revision_payload(
                sourceType=source_type,
                revisionKind=revision_kind,
                revision=revision,
                anchor=anchor,
            )
        )


@pytest.mark.parametrize(
    ("index_family", "source"),
    [
        (
            "code",
            public_source_revision_payload(
                sourceType="document",
                revisionKind="blob_version",
                revision="etag:blob-version-17",
                anchor={
                    "type": "document",
                    "headingPath": ["Authorization"],
                    "page": 1,
                },
            ),
        ),
        ("doc", public_source_revision_payload()),
        (
            "failure",
            public_source_revision_payload(
                sourceType="bdd",
                revisionKind="git_commit",
                revision="b" * 40,
                anchor={"type": "bdd", "featureId": "authorization"},
            ),
        ),
        (
            "bdd",
            public_source_revision_payload(
                sourceType="failure",
                revisionKind="mysql_version",
                revision="mysql-bin.000017:42",
                anchor={"type": "failure", "incidentId": "incident-17"},
            ),
        ),
    ],
    ids=("code-with-document", "doc-with-code", "failure-with-bdd", "bdd-with-failure"),
)
def test_public_hit_index_family_must_match_source_provenance(
    index_family: str,
    source: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RetrievalHit.model_validate(public_hit_payload(indexFamily=index_family, source=source))


def test_public_unknown_doc_subtype_remains_valid_when_family_and_provenance_agree() -> None:
    source = public_source_revision_payload(
        sourceType="policy_manual_v2",
        revisionKind="blob_version",
        revision="etag:blob-version-17",
        anchor={"type": "document", "headingPath": ["Authorization"], "page": 1},
    )

    validated_source = RetrievalSourceRevision.model_validate(source)
    validated_hit = RetrievalHit.model_validate(
        public_hit_payload(indexFamily="doc", source=source)
    )

    assert validated_source.source_type == "policy_manual_v2"
    assert validated_hit.index_family.value == "doc"


def test_public_citation_revalidates_nested_source_compatibility() -> None:
    with pytest.raises(ValidationError):
        RetrievalCitation.model_validate(
            public_citation_payload(
                source=public_source_revision_payload(
                    sourceType="code",
                    revisionKind="blob_version",
                    revision="etag:blob-version-17",
                    anchor={
                        "type": "document",
                        "headingPath": ["Authorization"],
                        "page": 1,
                    },
                )
            )
        )


def _assert_objects_closed(value: object) -> None:
    if isinstance(value, dict):
        if "properties" in value:
            assert value.get("additionalProperties") is False
        for nested in value.values():
            _assert_objects_closed(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_objects_closed(nested)
