from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from test_milvus_filter import _execution_with_resources, _resource, doc_execution
from test_milvus_mapping import valid_doc_row
from test_milvus_search_strict import RecordingAuditSink, RecordingReader, config

from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
from tap.modules.knowledge.adapters.milvus.transport import MilvusHybridRequest
from tap.modules.knowledge.domain.models import FilterableSubtree, ResourceMode, SourceFamily
from tap.modules.knowledge.ports.errors import SearchUnavailable
from tap.modules.knowledge.ports.models import SearchExecution, SearchHit

_CLASSIFICATION_RANK = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


@dataclass(frozen=True, slots=True)
class ConformanceResult:
    channels: tuple[str, ...]
    outbound_filters: tuple[str, ...]
    expected_filter: str
    provider_rows: tuple[Mapping[str, object], ...]
    hits: tuple[SearchHit, ...]


class SearchProviderConformanceHarness(Protocol):
    provider_name: Literal["milvus"]

    async def run_case(self, case_id: str) -> ConformanceResult: ...


@dataclass(frozen=True, slots=True)
class ProviderDocument:
    row: Mapping[str, object]
    tenant_id: str
    project_id: str
    allowed_group_ids: frozenset[str]
    classification_rank: int
    environment: str
    corpus_version: str
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class ConformanceCase:
    execution: SearchExecution
    documents: tuple[ProviderDocument, ...]
    mismatch: str | None
    unavailable: bool = False


def _allowed_document() -> ProviderDocument:
    return ProviderDocument(
        row=valid_doc_row(),
        tenant_id="tenant-a",
        project_id="project-a",
        allowed_group_ids=frozenset({"group-one"}),
        classification_rank=2,
        environment="production",
        corpus_version="corpus-fixture-v1",
    )


def conformance_case(case_id: str) -> ConformanceCase:
    base = _allowed_document()
    if case_id == "allowed":
        return ConformanceCase(doc_execution(), (base,), None)
    if case_id == "denied-group":
        return ConformanceCase(
            doc_execution(),
            (replace(base, allowed_group_ids=frozenset({"group-denied"})),),
            "groups",
        )
    if case_id == "wrong-tenant":
        return ConformanceCase(
            doc_execution(),
            (replace(base, tenant_id="tenant-other"),),
            "tenant",
        )
    if case_id == "wrong-project":
        return ConformanceCase(
            doc_execution(),
            (replace(base, project_id="project-other"),),
            "project",
        )
    if case_id == "over-classification":
        return ConformanceCase(
            doc_execution(),
            (replace(base, classification_rank=3),),
            "classification",
        )
    if case_id == "wrong-environment":
        return ConformanceCase(
            doc_execution(),
            (replace(base, environment="staging"),),
            "environment",
        )
    if case_id == "wrong-corpus":
        return ConformanceCase(
            doc_execution(),
            (replace(base, corpus_version="corpus-other-v1"),),
            "corpus",
        )
    if case_id == "resource-scope":
        execution = _execution_with_resources(
            (
                _resource(
                    "blob:handbook/payment-policy",
                    subtree=FilterableSubtree(root_ids=("h_" + "3" * 64,)),
                ),
            )
        )
        outside_subtree = {
            **valid_doc_row(),
            "source_id": "blob:handbook/payment-policy",
            "source_revision": "blob-version-v1",
            "source_content_hash": "sha256:" + "f" * 64,
            "root_id": "h_" + "6" * 64,
        }
        return ConformanceCase(
            execution,
            (replace(base, row=outside_subtree),),
            "scope",
        )
    if case_id == "unavailable":
        return ConformanceCase(doc_execution(), (base,), None, unavailable=True)
    raise ValueError("unknown conformance case")


class _MilvusReader(RecordingReader):
    def __init__(self, case: ConformanceCase) -> None:
        super().__init__(rows=())
        self.case = case
        self.returned_rows: tuple[Mapping[str, object], ...] = ()

    async def hybrid_search(
        self,
        request: MilvusHybridRequest,
    ) -> tuple[Mapping[str, object], ...]:
        self.requests.append(request)
        if self.case.unavailable:
            raise SearchUnavailable("controlled provider outage")
        self.returned_rows = tuple(
            document.row
            for document in self.case.documents
            if milvus_filter_allows(
                request.channels[0].filter_expression,
                document,
                self.case.execution,
            )
        )
        return self.returned_rows


class MilvusConformanceHarness:
    provider_name: Literal["milvus"] = "milvus"

    async def run_case(self, case_id: str) -> ConformanceResult:
        case = conformance_case(case_id)
        reader = _MilvusReader(case)
        adapter = MilvusSearchAdapter(config(), reader, RecordingAuditSink())
        hits = await adapter.search(case.execution)
        request = reader.requests[0]
        return ConformanceResult(
            channels=tuple(channel.kind for channel in request.channels),
            outbound_filters=tuple(channel.filter_expression for channel in request.channels),
            expected_filter=expected_milvus_filter(case.execution),
            provider_rows=reader.returned_rows,
            hits=hits,
        )


def expected_milvus_filter(execution: SearchExecution) -> str:
    clauses = [
        _milvus_tenant_clause(execution),
        _milvus_project_clause(execution),
        _milvus_group_clause(execution),
        _milvus_classification_clause(execution),
        _milvus_environment_clause(execution),
        _milvus_corpus_clause(execution),
        "deleted == false",
    ]
    scope = _milvus_scope_clause(execution)
    if scope is not None:
        clauses.append(scope)
    return " and ".join(clauses)


def milvus_filter_allows(
    expression: str,
    document: ProviderDocument,
    execution: SearchExecution,
) -> bool:
    allowed_ranks = {
        _CLASSIFICATION_RANK[item.value] for item in execution.policy.allowed_classifications
    }
    environments = {"global"}
    if execution.plan.effective_environment is not None:
        environments.add(execution.plan.effective_environment)
    checks = (
        (_milvus_tenant_clause(execution), document.tenant_id == execution.policy.tenant_id),
        (_milvus_project_clause(execution), document.project_id == execution.policy.project_id),
        (
            _milvus_group_clause(execution),
            bool(document.allowed_group_ids & execution.policy.actor.allowed_group_ids),
        ),
        (
            _milvus_classification_clause(execution),
            document.classification_rank in allowed_ranks,
        ),
        (_milvus_environment_clause(execution), document.environment in environments),
        (
            _milvus_corpus_clause(execution),
            document.corpus_version == execution.plan.corpus_version,
        ),
        ("deleted == false", document.deleted is False),
    )
    if any(clause in expression and not matches for clause, matches in checks):
        return False
    scope = _milvus_scope_clause(execution)
    return scope is None or scope not in expression or _document_matches_scope(document, execution)


def conformance_guard_clause(provider: str, case: ConformanceCase) -> str:
    mismatch = case.mismatch
    execution = case.execution
    clauses = {
        "milvus": {
            "groups": _milvus_group_clause(execution),
            "tenant": _milvus_tenant_clause(execution),
            "project": _milvus_project_clause(execution),
            "classification": _milvus_classification_clause(execution),
            "environment": _milvus_environment_clause(execution),
            "corpus": _milvus_corpus_clause(execution),
            "scope": _milvus_scope_clause(execution),
        },
    }
    if provider not in clauses or mismatch is None:
        raise ValueError("conformance case has no provider guard")
    clause = clauses[provider].get(mismatch)
    if not isinstance(clause, str):
        raise ValueError("conformance case has no provider guard")
    return clause


def _document_matches_scope(
    document: ProviderDocument,
    execution: SearchExecution,
) -> bool:
    row = document.row
    for resource in execution.plan.resources:
        if resource.family is not SourceFamily.DOC or resource.mode is not ResourceMode.SCOPE:
            continue
        if (
            row.get("source_id") != resource.source_id
            or row.get("source_revision") != resource.revision
            or row.get("source_content_hash") != resource.source_content_hash
        ):
            continue
        subtree = resource.subtree
        if subtree is None:
            return True
        if (
            row.get("root_id") in subtree.root_ids
            or row.get("parent_id") in subtree.parent_ids
            or row.get("logical_chunk_id") in subtree.logical_chunk_ids
        ):
            return True
    return False


def _compact_json(value: object) -> str:
    if isinstance(value, tuple):
        return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def _milvus_tenant_clause(execution: SearchExecution) -> str:
    return f"tenant_id == {_compact_json(execution.policy.tenant_id)}"


def _milvus_project_clause(execution: SearchExecution) -> str:
    return f"project_id == {_compact_json(execution.policy.project_id)}"


def _milvus_group_clause(execution: SearchExecution) -> str:
    groups = tuple(sorted(execution.policy.actor.allowed_group_ids))
    return f"ARRAY_CONTAINS_ANY(allowed_group_ids, {_compact_json(groups)})"


def _milvus_classification_clause(execution: SearchExecution) -> str:
    ranks = tuple(
        rank
        for name, rank in _CLASSIFICATION_RANK.items()
        if any(item.value == name for item in execution.policy.allowed_classifications)
    )
    return f"classification_rank in {_compact_json(ranks)}"


def _milvus_environment_clause(execution: SearchExecution) -> str:
    values = (
        ("global",)
        if execution.plan.effective_environment is None
        or execution.plan.effective_environment == "global"
        else (execution.plan.effective_environment, "global")
    )
    return f"environment in {_compact_json(values)}"


def _milvus_corpus_clause(execution: SearchExecution) -> str:
    return f"corpus_version == {_compact_json(execution.plan.corpus_version)}"


def _milvus_scope_clause(execution: SearchExecution) -> str | None:
    resource_clauses = []
    for resource in execution.plan.resources:
        if resource.family is not SourceFamily.DOC or resource.mode is not ResourceMode.SCOPE:
            continue
        parts = [
            f"source_id == {_compact_json(resource.source_id)}",
            f"source_revision == {_compact_json(resource.revision)}",
            f"source_content_hash == {_compact_json(resource.source_content_hash)}",
        ]
        if resource.subtree is not None:
            locators = []
            if resource.subtree.root_ids:
                locators.append(f"root_id in {_compact_json(resource.subtree.root_ids)}")
            if resource.subtree.parent_ids:
                locators.append(f"parent_id in {_compact_json(resource.subtree.parent_ids)}")
            if resource.subtree.logical_chunk_ids:
                locators.append(
                    f"logical_chunk_id in {_compact_json(resource.subtree.logical_chunk_ids)}"
                )
            parts.append("(" + " or ".join(locators) + ")")
        resource_clauses.append("(" + " and ".join(parts) + ")")
    if not resource_clauses:
        return None
    return "(" + " or ".join(resource_clauses) + ")"


def milvus_harness() -> SearchProviderConformanceHarness:
    return MilvusConformanceHarness()
