"""Immutable, server-authorized answer plans stored in the interaction checkpoint."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any

from tap.modules.chat.domain.conversations import content_digest

INTENTS = frozenset(
    {
        "general_chat",
        "transform_text",
        "factual_lookup",
        "explanation",
        "comparison",
        "procedural",
        "clarification",
    }
)
ROUTES = frozenset({"direct", "retrieve", "clarify", "insights", "file_analysis"})
MISSING_FIELDS = frozenset(
    {"sources", "object-or-version", "object", "version", "time-range", "comparison-conditions"}
)


def protected_constraints(text: str) -> tuple[str, ...]:
    """Keep exact codes, numbers, versions, dates and negations byte-for-byte."""
    return tuple(
        dict.fromkeys(
            re.findall(
                r"[A-Za-z][A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*|\d+(?:[-./:]\d+)*|不得|不能|不允许|不要|禁止|没有|未|不|\b(?:not|never|without|except)\b",
                text,
                re.IGNORECASE,
            )
        )
    )


@dataclass(frozen=True, slots=True)
class PlanningInput:
    project_id: str
    turn_id: str
    input_digest: str
    acl_digest: str
    policy_digest: str
    original_question: str
    model_alias: str
    source_ids: tuple[str, ...]
    authorized_referents: tuple[str, ...] = ()
    remaining_seconds: float = 30.0
    candidate_limit: int = 20
    evidence_limit: int = 10
    output_requirements: str = ""
    context_digests: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedQuery:
    id: str
    text: str
    depends_on: tuple[str, ...]
    evidence_goal: str
    source_ids: tuple[str, ...]

    def __post_init__(self):
        for values in (self.depends_on, self.source_ids):
            if not isinstance(values, tuple) or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                raise TypeError("query collections must be immutable tuples of identities")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.id, self.text, self.evidence_goal)
        ):
            raise ValueError("query identity, text and evidence goal are required")
        if len(self.text) > 20000 or not self.source_ids:
            raise ValueError("query text and scope must be bounded")


@dataclass(frozen=True, slots=True)
class AnswerPlan:
    plan_id: str
    project_id: str
    turn_id: str
    input_digest: str
    acl_digest: str
    policy_digest: str
    model_alias: str
    original_question: str
    intent: str
    route: str
    confidence: float
    standalone_query: str
    constraints: tuple[str, ...]
    missing: tuple[str, ...]
    queries: tuple[PlannedQuery, ...]
    source_ids: tuple[str, ...]
    template_id: str
    template_version: str
    template_digest: str
    deadline_at: float
    candidate_limit: int = 20
    evidence_limit: int = 10
    remaining_seconds: float = 30.0
    output_requirements: str = ""
    graph_version: str = "fast-chat-v1"
    planner_version: str = "answer-planner-v1"
    previous_plan_id: str | None = None
    degradation_reason: str | None = None
    context_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "plan_id",
            "project_id",
            "turn_id",
            "model_alias",
            "original_question",
            "standalone_query",
            "template_id",
            "template_version",
            "graph_version",
            "planner_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("plan identities and versioned content must be nonblank")
        for name in ("constraints", "missing", "queries", "source_ids", "context_digests"):
            if not isinstance(getattr(self, name), tuple):
                raise TypeError("plan collections must be immutable")
        if self.intent not in INTENTS or self.route not in ROUTES:
            raise ValueError("unknown answer intent or route")
        if not set(self.missing) <= MISSING_FIELDS:
            raise ValueError("unknown missing information field")
        if self.route == "clarify" and (self.intent != "clarification" or not self.missing):
            raise ValueError("clarification requires a missing field and clarification intent")
        if not math.isfinite(self.deadline_at) or self.deadline_at <= 0:
            raise ValueError("invalid persisted answer deadline")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("invalid classification confidence")
        if not 1 <= self.candidate_limit <= 50 or not 1 <= self.evidence_limit <= 20:
            raise ValueError("invalid shared retrieval budget")
        if self.evidence_limit > self.candidate_limit or not 0 < self.remaining_seconds <= 300:
            raise ValueError("invalid answer budget")
        if len(self.queries) > 3 or (self.route == "retrieve") != bool(self.queries):
            raise ValueError("only retrieval plans have one to three queries")
        if self.route == "direct" and self.intent not in {"general_chat", "transform_text"}:
            raise ValueError("enterprise facts require evidence")
        if self.constraints != protected_constraints(self.original_question):
            raise ValueError("plan constraints differ from original question")
        if not set(self.constraints) <= set(protected_constraints(self.standalone_query)):
            raise ValueError("standalone query lost a protected constraint")
        if self.queries and not set(self.constraints) <= set(
            protected_constraints(" ".join(query.text for query in self.queries))
        ):
            raise ValueError("queries lost a protected constraint")
        if len({query.id for query in self.queries}) != len(self.queries):
            raise ValueError("duplicate query identity")
        pending = {query.id: set(query.depends_on) for query in self.queries}
        if any(set(query.source_ids) - set(self.source_ids) for query in self.queries):
            raise ValueError("query expanded authorized source scope")
        completed: set[str] = set()
        while pending:
            ready = {key for key, dependencies in pending.items() if dependencies <= completed}
            if not ready:
                raise ValueError("unknown or cyclic query dependency")
            completed.update(ready)
            pending = {
                key: dependencies for key, dependencies in pending.items() if key not in ready
            }
        for digest in (
            self.input_digest,
            self.acl_digest,
            self.policy_digest,
            self.template_digest,
        ):
            if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                raise ValueError("plan digest must be canonical")

    @property
    def digest(self) -> str:
        return content_digest(asdict(self))

    def validate_binding(self, current: PlanningInput) -> None:
        for name in (
            "project_id",
            "turn_id",
            "input_digest",
            "acl_digest",
            "policy_digest",
            "model_alias",
            "original_question",
            "source_ids",
        ):
            if getattr(self, name) != getattr(current, name):
                raise ValueError("answer plan input or authorization binding changed")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "digest": self.digest}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AnswerPlan:
        value = dict(raw)
        digest = value.pop("digest")
        value["queries"] = tuple(
            PlannedQuery(
                **{
                    **query,
                    "depends_on": tuple(query["depends_on"]),
                    "source_ids": tuple(query["source_ids"]),
                }
            )
            for query in value["queries"]
        )
        for name in ("constraints", "missing", "source_ids", "context_digests"):
            value[name] = tuple(value[name])
        result = cls(**value)
        if result.digest != digest:
            raise ValueError("answer plan checkpoint digest changed")
        return result
