"""Compile trusted executions into bounded, closed Milvus ACL expressions."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from tap.modules.access.domain.policy import Classification
from tap.modules.knowledge.domain.models import (
    FilterableSubtree,
    ResolvedResourceRef,
    ResourceMode,
    SourceFamily,
)
from tap.modules.knowledge.ports.errors import SearchBoundsExceeded
from tap.modules.knowledge.ports.models import SearchExecution

_MAX_FILTER_BYTES = 32_768
_MAX_FILTER_LITERAL_CHARS = 256
_MAX_GROUPS = 128
_MAX_RESOURCES = 20
_MAX_LOCATORS = 32
_FILTER_FIELDS = frozenset(
    {
        "tenant_id",
        "project_id",
        "allowed_group_ids",
        "classification_rank",
        "environment",
        "corpus_version",
        "deleted",
        "source_id",
        "source_revision",
        "source_content_hash",
        "root_id",
        "parent_id",
        "logical_chunk_id",
    }
)
_CLASSIFICATION_RANK = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}


class _ExpressionBuilder:
    @staticmethod
    def equal(field: str, value: str) -> str:
        return f"{_field(field)} == {_literal(value)}"

    @staticmethod
    def equal_bool(field: str, value: bool) -> str:
        return f"{_field(field)} == {'true' if value else 'false'}"

    @staticmethod
    def string_membership(field: str, values: Sequence[str]) -> str:
        return f"{_field(field)} in {_string_array(values)}"

    @staticmethod
    def integer_membership(field: str, values: Sequence[int]) -> str:
        if not values or any(type(value) is not int or not 0 <= value <= 3 for value in values):
            raise SearchBoundsExceeded("classification ranks are outside the closed set")
        return f"{_field(field)} in [{', '.join(str(value) for value in values)}]"

    @staticmethod
    def array_contains_any(field: str, values: Sequence[str]) -> str:
        return f"ARRAY_CONTAINS_ANY({_field(field)}, {_string_array(values)})"

    @staticmethod
    def all_of(clauses: Iterable[str]) -> str:
        bounded = tuple(clauses)
        if not bounded:
            raise SearchBoundsExceeded("filter conjunction must not be empty")
        return " and ".join(bounded)

    @staticmethod
    def any_of(clauses: Iterable[str]) -> str:
        bounded = tuple(clauses)
        if not bounded:
            raise SearchBoundsExceeded("filter disjunction must not be empty")
        return " or ".join(bounded)


def compile_milvus_filter(
    execution: SearchExecution,
    family: SourceFamily,
    *,
    max_bytes: int,
) -> str:
    """Compile only trusted execution facts into a bounded Milvus expression."""
    if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_FILTER_BYTES:
        raise SearchBoundsExceeded("filter byte bound is outside the closed limit")
    if not isinstance(execution, SearchExecution):
        raise SearchBoundsExceeded("filter input must be a trusted search execution")
    if family is not SourceFamily.DOC:
        raise SearchBoundsExceeded("Milvus filter supports only the doc source family")

    policy = execution.policy
    plan = execution.plan
    group_ids = tuple(sorted(policy.actor.allowed_group_ids))
    if not group_ids or len(group_ids) > _MAX_GROUPS:
        raise SearchBoundsExceeded("filter ACL groups are empty or exceed the bound")
    allowed_classifications = policy.allowed_classifications
    if not allowed_classifications or any(
        not isinstance(classification, Classification) for classification in allowed_classifications
    ):
        raise SearchBoundsExceeded("filter ACL classifications are empty or malformed")
    ranks = tuple(
        rank
        for classification, rank in _CLASSIFICATION_RANK.items()
        if classification in allowed_classifications
    )
    resources = plan.resources
    if not isinstance(resources, tuple) or len(resources) > _MAX_RESOURCES:
        raise SearchBoundsExceeded("filter resources exceed the bound")
    if not all(isinstance(resource, ResolvedResourceRef) for resource in resources):
        raise SearchBoundsExceeded("filter resources are not trusted resolved values")

    environments = (
        ("global",)
        if plan.effective_environment is None or plan.effective_environment == "global"
        else (plan.effective_environment, "global")
    )
    clauses = [
        _ExpressionBuilder.equal("tenant_id", policy.tenant_id),
        _ExpressionBuilder.equal("project_id", policy.project_id),
        _ExpressionBuilder.array_contains_any("allowed_group_ids", group_ids),
        _ExpressionBuilder.integer_membership("classification_rank", ranks),
        _ExpressionBuilder.string_membership("environment", environments),
        _ExpressionBuilder.equal("corpus_version", plan.corpus_version),
        _ExpressionBuilder.equal_bool("deleted", False),
    ]

    scoped = tuple(
        resource
        for resource in resources
        if resource.family is family and resource.mode is ResourceMode.SCOPE
    )
    if scoped:
        locator_count = sum(_locator_count(resource.subtree) for resource in scoped)
        if locator_count > _MAX_LOCATORS:
            raise SearchBoundsExceeded("filter subtree locators exceed the bound")
        resource_clauses = tuple(_resource_clause(resource) for resource in scoped)
        clauses.append(f"({_ExpressionBuilder.any_of(resource_clauses)})")

    expression = _ExpressionBuilder.all_of(clauses)
    if len(expression.encode("utf-8")) > max_bytes:
        raise SearchBoundsExceeded("filter expression exceeds the UTF-8 byte bound")
    return expression


def _resource_clause(resource: ResolvedResourceRef) -> str:
    parts = [
        _ExpressionBuilder.equal("source_id", resource.source_id),
        _ExpressionBuilder.equal("source_revision", resource.revision),
        _ExpressionBuilder.equal("source_content_hash", resource.source_content_hash),
    ]
    if resource.subtree is not None:
        locators = []
        if resource.subtree.root_ids:
            locators.append(
                _ExpressionBuilder.string_membership("root_id", resource.subtree.root_ids)
            )
        if resource.subtree.parent_ids:
            locators.append(
                _ExpressionBuilder.string_membership("parent_id", resource.subtree.parent_ids)
            )
        if resource.subtree.logical_chunk_ids:
            locators.append(
                _ExpressionBuilder.string_membership(
                    "logical_chunk_id",
                    resource.subtree.logical_chunk_ids,
                )
            )
        parts.append(f"({_ExpressionBuilder.any_of(locators)})")
    return f"({_ExpressionBuilder.all_of(parts)})"


def _locator_count(subtree: FilterableSubtree | None) -> int:
    if subtree is None:
        return 0
    return len(subtree.root_ids) + len(subtree.parent_ids) + len(subtree.logical_chunk_ids)


def _field(value: str) -> str:
    if value not in _FILTER_FIELDS:
        raise SearchBoundsExceeded("filter field is outside the closed schema")
    return value


def _string_array(values: Sequence[str]) -> str:
    if not values:
        raise SearchBoundsExceeded("filter literal array must not be empty")
    return "[" + ", ".join(_literal(value) for value in values) + "]"


def _literal(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise SearchBoundsExceeded("filter literal must be a non-empty string")
    if len(value) > _MAX_FILTER_LITERAL_CHARS:
        raise SearchBoundsExceeded("filter literal exceeds 256 characters")
    if any(ord(character) < 0x20 for character in value):
        raise SearchBoundsExceeded("filter literal contains a control character")
    return json.dumps(value, ensure_ascii=False)
