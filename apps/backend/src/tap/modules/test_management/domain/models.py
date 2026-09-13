"""Immutable Test Plan revision content and generation identities."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{2,127}\Z")


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a stable identifier")


def _text(name: str, value: str, maximum: int = 4096) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{name} must be bounded nonblank text")


class RevisionStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


class IdentityOrigin(StrEnum):
    VALIDATION = "VALIDATION"
    PRODUCT = "PRODUCT"


class BddKeyword(StrEnum):
    GIVEN = "Given"
    WHEN = "When"
    THEN = "Then"
    AND = "And"
    BUT = "But"


class CitationOrigin(StrEnum):
    SOURCE = "SOURCE"
    GRAPH_EXTRACTED = "GRAPH_EXTRACTED"
    GRAPH_INFERRED = "GRAPH_INFERRED"


class GapSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class GenerationJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DRAFT_READY = "DRAFT_READY"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class TestPlanStep:
    step_id: str
    ordinal: int
    keyword: BddKeyword
    text: str
    expected_result: str | None = None
    critical: bool = False

    def __post_init__(self) -> None:
        _identifier("step_id", self.step_id)
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 1:
            raise ValueError("step ordinal must be positive")
        if not isinstance(self.keyword, BddKeyword):
            raise TypeError("keyword must be a BddKeyword")
        _text("step text", self.text)
        if self.expected_result is not None:
            _text("expected result", self.expected_result)
        if not isinstance(self.critical, bool):
            raise TypeError("critical must be boolean")


@dataclass(frozen=True, slots=True)
class TestScenario:
    scenario_id: str
    ordinal: int
    title: str
    steps: tuple[TestPlanStep, ...]

    def __post_init__(self) -> None:
        _identifier("scenario_id", self.scenario_id)
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 1:
            raise ValueError("scenario ordinal must be positive")
        _text("scenario title", self.title, 512)


@dataclass(frozen=True, slots=True)
class TestCase:
    case_id: str
    ordinal: int
    title: str
    objective: str
    critical: bool
    scenarios: tuple[TestScenario, ...]

    def __post_init__(self) -> None:
        _identifier("case_id", self.case_id)
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 1:
            raise ValueError("case ordinal must be positive")
        _text("case title", self.title, 512)
        _text("case objective", self.objective)
        if not isinstance(self.critical, bool):
            raise TypeError("critical must be boolean")


@dataclass(frozen=True, slots=True)
class TestPlanCitation:
    citation_id: str
    source_revision_id: str
    document_revision_id: str
    chunk_id: str
    content_digest: str
    claim_text: str
    origin: CitationOrigin

    def __post_init__(self) -> None:
        for name, value in (
            ("citation_id", self.citation_id),
            ("source_revision_id", self.source_revision_id),
            ("document_revision_id", self.document_revision_id),
            ("chunk_id", self.chunk_id),
        ):
            _identifier(name, value)
        if _DIGEST.fullmatch(self.content_digest) is None:
            raise ValueError("citation content digest must be canonical")
        _text("citation claim", self.claim_text)
        if not isinstance(self.origin, CitationOrigin):
            raise TypeError("citation origin must be explicit")


@dataclass(frozen=True, slots=True)
class TestPlanAssumption:
    assumption_id: str
    text: str
    graph_edge_id: str | None = None

    def __post_init__(self) -> None:
        _identifier("assumption_id", self.assumption_id)
        _text("assumption", self.text)
        if self.graph_edge_id is not None:
            _identifier("graph_edge_id", self.graph_edge_id)


@dataclass(frozen=True, slots=True)
class TestPlanUnknown:
    unknown_id: str
    text: str

    def __post_init__(self) -> None:
        _identifier("unknown_id", self.unknown_id)
        _text("unknown", self.text)


@dataclass(frozen=True, slots=True)
class TestPlanCoverageGap:
    gap_id: str
    requirement_ref: str
    reason: str
    severity: GapSeverity

    def __post_init__(self) -> None:
        _identifier("gap_id", self.gap_id)
        _text("coverage requirement", self.requirement_ref, 512)
        _text("coverage gap reason", self.reason)
        if not isinstance(self.severity, GapSeverity):
            raise TypeError("coverage gap severity must be explicit")


@dataclass(frozen=True, slots=True)
class TestPlanGenerationRequest:
    job_id: str
    test_plan_id: str
    revision_id: str
    project_id: str
    conversation_id: str
    turn_id: str
    input_snapshot_digest: str
    answer_evidence_snapshot_digest: str
    model_alias: str
    agent_revision_id: str
    skill_revision_ids: tuple[str, ...]
    objective: str
    idempotency_key: str
    request_digest: str

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        conversation_id: str,
        turn_id: str,
        input_snapshot_digest: str,
        answer_evidence_snapshot_digest: str,
        model_alias: str,
        agent_revision_id: str,
        skill_revision_ids: tuple[str, ...],
        objective: str,
        idempotency_key: str,
    ) -> TestPlanGenerationRequest:
        for name, value in (
            ("project_id", project_id),
            ("conversation_id", conversation_id),
            ("turn_id", turn_id),
            ("model_alias", model_alias),
            ("agent_revision_id", agent_revision_id),
            ("idempotency_key", idempotency_key),
        ):
            _identifier(name, value)
        if any(
            _DIGEST.fullmatch(value) is None
            for value in (
                input_snapshot_digest,
                answer_evidence_snapshot_digest,
            )
        ):
            raise ValueError("generation snapshot digest must be canonical")
        if not skill_revision_ids or len(skill_revision_ids) != len(set(skill_revision_ids)):
            raise ValueError("generation skill revisions must be nonempty and unique")
        for value in skill_revision_ids:
            _identifier("skill_revision_id", value)
        _text("generation objective", objective)
        material = json.dumps(
            {
                "projectId": project_id,
                "conversationId": conversation_id,
                "turnId": turn_id,
                "inputSnapshotDigest": input_snapshot_digest,
                "answerEvidenceSnapshotDigest": answer_evidence_snapshot_digest,
                "modelAlias": model_alias,
                "agentRevisionId": agent_revision_id,
                "skillRevisionIds": list(skill_revision_ids),
                "objective": objective,
                "idempotencyKey": idempotency_key,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = "sha256:" + hashlib.sha256(material.encode()).hexdigest()
        suffix = digest.removeprefix("sha256:")[:32]
        return cls(
            f"tpj_{suffix}",
            f"tp_{suffix}",
            f"tpr_{suffix}",
            project_id,
            conversation_id,
            turn_id,
            input_snapshot_digest,
            answer_evidence_snapshot_digest,
            model_alias,
            agent_revision_id,
            skill_revision_ids,
            objective,
            idempotency_key,
            digest,
        )


@dataclass(frozen=True, slots=True)
class TestPlanGenerationJob:
    request: TestPlanGenerationRequest
    status: GenerationJobStatus
    created_at: datetime
    updated_at: datetime
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, GenerationJobStatus):
            raise TypeError("generation status must be explicit")
        if self.attempt_count < 0:
            raise ValueError("generation attempt count cannot be negative")


@dataclass(frozen=True, slots=True)
class TestPlanRevision:
    test_plan_id: str
    revision_id: str
    version: int
    title: str
    objective: str
    scope_items: tuple[str, ...]
    prerequisites: tuple[str, ...]
    risks: tuple[str, ...]
    cases: tuple[TestCase, ...]
    citations: tuple[TestPlanCitation, ...]
    assumptions: tuple[TestPlanAssumption, ...]
    unknowns: tuple[TestPlanUnknown, ...]
    coverage_gaps: tuple[TestPlanCoverageGap, ...]
    status: RevisionStatus
    origin: IdentityOrigin
    adopted_from_revision_id: str | None
    content_digest: str
    row_version: int = 1
    validation_digest: str | None = None
    created_at: datetime | None = None
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        _identifier("test_plan_id", self.test_plan_id)
        _identifier("revision_id", self.revision_id)
        if self.adopted_from_revision_id is not None:
            _identifier("adopted_from_revision_id", self.adopted_from_revision_id)
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("revision version must be positive")
        if (
            not isinstance(self.row_version, int)
            or isinstance(self.row_version, bool)
            or self.row_version < 1
        ):
            raise ValueError("row version must be positive")
        _text("plan title", self.title, 512)
        _text("plan objective", self.objective)
        if not isinstance(self.status, RevisionStatus) or not isinstance(
            self.origin, IdentityOrigin
        ):
            raise TypeError("revision status and origin must be explicit")
        if _DIGEST.fullmatch(self.content_digest) is None:
            raise ValueError("content digest must be canonical")
        if self.validation_digest is not None and _DIGEST.fullmatch(self.validation_digest) is None:
            raise ValueError("validation digest must be canonical")

    @classmethod
    def create(
        cls,
        *,
        test_plan_id: str,
        revision_id: str,
        version: int,
        title: str,
        objective: str,
        scope_items: tuple[str, ...],
        prerequisites: tuple[str, ...],
        risks: tuple[str, ...],
        cases: tuple[TestCase, ...],
        citations: tuple[TestPlanCitation, ...],
        assumptions: tuple[TestPlanAssumption, ...],
        unknowns: tuple[TestPlanUnknown, ...],
        coverage_gaps: tuple[TestPlanCoverageGap, ...],
        origin: IdentityOrigin,
        adopted_from_revision_id: str | None = None,
    ) -> TestPlanRevision:
        provisional = cls(
            test_plan_id,
            revision_id,
            version,
            title,
            objective,
            scope_items,
            prerequisites,
            risks,
            cases,
            citations,
            assumptions,
            unknowns,
            coverage_gaps,
            RevisionStatus.DRAFT,
            origin,
            adopted_from_revision_id,
            "sha256:" + "0" * 64,
        )
        return provisional.with_recomputed_digest()

    def canonical_content(self) -> dict[str, object]:
        return {
            "title": self.title,
            "objective": self.objective,
            "scope": list(self.scope_items),
            "prerequisites": list(self.prerequisites),
            "risks": list(self.risks),
            "cases": [
                {
                    "caseId": case.case_id,
                    "ordinal": case.ordinal,
                    "title": case.title,
                    "objective": case.objective,
                    "critical": case.critical,
                    "scenarios": [
                        {
                            "scenarioId": scenario.scenario_id,
                            "ordinal": scenario.ordinal,
                            "title": scenario.title,
                            "steps": [
                                {
                                    "stepId": step.step_id,
                                    "ordinal": step.ordinal,
                                    "keyword": step.keyword.value,
                                    "text": step.text,
                                    "expectedResult": step.expected_result,
                                    "critical": step.critical,
                                }
                                for step in scenario.steps
                            ],
                        }
                        for scenario in case.scenarios
                    ],
                }
                for case in self.cases
            ],
            "citations": [
                {
                    "citationId": item.citation_id,
                    "sourceRevisionId": item.source_revision_id,
                    "documentRevisionId": item.document_revision_id,
                    "chunkId": item.chunk_id,
                    "contentDigest": item.content_digest,
                    "claimText": item.claim_text,
                    "origin": item.origin.value,
                }
                for item in self.citations
            ],
            "assumptions": [
                {
                    "assumptionId": item.assumption_id,
                    "text": item.text,
                    "graphEdgeId": item.graph_edge_id,
                }
                for item in self.assumptions
            ],
            "unknowns": [
                {"unknownId": item.unknown_id, "text": item.text} for item in self.unknowns
            ],
            "coverageGaps": [
                {
                    "gapId": item.gap_id,
                    "requirementRef": item.requirement_ref,
                    "reason": item.reason,
                    "severity": item.severity.value,
                }
                for item in self.coverage_gaps
            ],
        }

    def compute_content_digest(self) -> str:
        material = json.dumps(
            self.canonical_content(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return "sha256:" + hashlib.sha256(material.encode()).hexdigest()

    def with_recomputed_digest(self) -> TestPlanRevision:
        case_ids = [item.case_id for item in self.cases]
        scenario_ids = [scenario.scenario_id for case in self.cases for scenario in case.scenarios]
        step_ids = [
            step.step_id
            for case in self.cases
            for scenario in case.scenarios
            for step in scenario.steps
        ]
        for name, values in (
            ("test case", case_ids),
            ("scenario", scenario_ids),
            ("BDD step", step_ids),
            ("citation", [item.citation_id for item in self.citations]),
            ("assumption", [item.assumption_id for item in self.assumptions]),
            ("unknown", [item.unknown_id for item in self.unknowns]),
            ("coverage gap", [item.gap_id for item in self.coverage_gaps]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} identities must be unique")
        return replace(self, content_digest=self.compute_content_digest())

    def fork(self, revision_id: str, version: int) -> TestPlanRevision:
        if self.status not in {RevisionStatus.PUBLISHED, RevisionStatus.SUPERSEDED}:
            raise ValueError("only immutable revisions can be forked")
        return replace(
            self,
            revision_id=revision_id,
            version=version,
            status=RevisionStatus.DRAFT,
            adopted_from_revision_id=self.revision_id,
            row_version=1,
            validation_digest=None,
            created_at=None,
            published_at=None,
        )
