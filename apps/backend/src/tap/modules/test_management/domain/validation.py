"""Deterministic Test Plan publication validation."""

from __future__ import annotations

import hashlib
import json

from tap.modules.test_management.domain.models import (
    BddKeyword,
    CitationOrigin,
    RevisionStatus,
    TestPlanRevision,
)


class RevisionConflict(Exception):
    """The caller's If-Match version no longer owns the draft."""


class RevisionImmutable(Exception):
    """An immutable revision was selected for mutation."""


def _unique(values: list[str], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} identities must be unique")


def _ordered(values: list[int], name: str) -> None:
    if values != list(range(1, len(values) + 1)):
        raise ValueError(f"{name} ordinals must be contiguous")


def validate_draft_structure(revision: TestPlanRevision) -> str:
    return _validate(revision, require_citations=False)


def validate_publishable(revision: TestPlanRevision) -> str:
    return _validate(revision, require_citations=True)


def _validate(revision: TestPlanRevision, *, require_citations: bool) -> str:
    if revision.status not in {RevisionStatus.DRAFT, RevisionStatus.VALIDATING}:
        raise RevisionImmutable("published and superseded revisions are immutable")
    if revision.content_digest != revision.compute_content_digest():
        raise ValueError("test plan content digest is stale")
    if not revision.scope_items or not revision.prerequisites or not revision.risks:
        raise ValueError("objective, scope, prerequisites, and risks are required")
    if not revision.cases:
        raise ValueError("test cases are required")
    if require_citations and not revision.citations:
        raise ValueError("grounded citations are required")

    case_ids = [item.case_id for item in revision.cases]
    _unique(case_ids, "test case")
    _ordered([item.ordinal for item in revision.cases], "test case")
    scenario_ids: list[str] = []
    step_ids: list[str] = []
    critical_expectations = 0
    for case in revision.cases:
        if not case.scenarios:
            raise ValueError("every test case requires a BDD scenario")
        _ordered([item.ordinal for item in case.scenarios], "scenario")
        for scenario in case.scenarios:
            scenario_ids.append(scenario.scenario_id)
            if not scenario.steps or scenario.steps[0].keyword is not BddKeyword.GIVEN:
                raise ValueError("every BDD scenario must begin with Given")
            _ordered([item.ordinal for item in scenario.steps], "BDD step")
            seen_when = False
            seen_then = False
            previous: BddKeyword | None = None
            for step in scenario.steps:
                step_ids.append(step.step_id)
                if step.keyword is BddKeyword.WHEN:
                    seen_when = True
                elif step.keyword is BddKeyword.THEN:
                    if not seen_when:
                        raise ValueError("Then requires an earlier When")
                    seen_then = True
                    if step.expected_result is None:
                        raise ValueError("Then step requires an expected result")
                    if case.critical or step.critical:
                        critical_expectations += 1
                elif step.keyword in {BddKeyword.AND, BddKeyword.BUT} and previous is None:
                    raise ValueError("And/But requires a preceding BDD step")
                previous = step.keyword
            if not seen_when or not seen_then:
                raise ValueError("every BDD scenario requires When and Then")
    _unique(scenario_ids, "scenario")
    _unique(step_ids, "BDD step")
    if any(item.critical for item in revision.cases) and critical_expectations == 0:
        raise ValueError("critical test case requires a critical expected result")

    citation_ids = [item.citation_id for item in revision.citations]
    _unique(citation_ids, "citation")
    if any(item.origin is CitationOrigin.GRAPH_INFERRED for item in revision.citations):
        raise ValueError("Graph INFERRED facts must be recorded as Assumption")
    _unique([item.assumption_id for item in revision.assumptions], "assumption")
    _unique([item.unknown_id for item in revision.unknowns], "unknown")
    _unique([item.gap_id for item in revision.coverage_gaps], "coverage gap")

    facts = {
        "contentDigest": revision.content_digest,
        "caseCount": len(case_ids),
        "scenarioCount": len(scenario_ids),
        "stepCount": len(step_ids),
        "citationCount": len(citation_ids),
        "criticalExpectationCount": critical_expectations,
    }
    material = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode()).hexdigest()
