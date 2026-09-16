"""Deterministic Test Plan generator for the isolated fake E2E profile."""

from __future__ import annotations

from typing import cast

from tap.modules.test_management.domain.models import (
    BddKeyword,
    CitationOrigin,
    GapSeverity,
    IdentityOrigin,
    TestCase,
    TestPlanCitation,
    TestPlanCoverageGap,
    TestPlanRevision,
    TestPlanStep,
    TestPlanUnknown,
    TestScenario,
)
from tap.modules.test_management.ports.generation import TestDesignContext


class DeterministicTestDesign:
    async def generate(self, context: TestDesignContext) -> TestPlanRevision:
        evidence = context.answer_evidence_snapshot.get("authorizedEvidence", [])
        citations: tuple[TestPlanCitation, ...] = ()
        gaps: tuple[TestPlanCoverageGap, ...] = ()
        unknowns: tuple[TestPlanUnknown, ...] = ()
        if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
            item = cast(dict[str, object], evidence[0])
            citations = (
                TestPlanCitation(
                    "citation_grounded",
                    str(item["sourceRevisionId"]),
                    str(item["documentRevisionId"]),
                    str(item["chunkId"]),
                    str(item["contentDigest"]),
                    "The selected evidence supports this test objective.",
                    CitationOrigin.SOURCE,
                ),
            )
        else:
            unknowns = (
                TestPlanUnknown(
                    "unknown_source_evidence",
                    "No source evidence was available for this generated draft.",
                ),
            )
            gaps = (
                TestPlanCoverageGap(
                    "gap_source_evidence",
                    "Grounded behavior",
                    "Source evidence is required before publication.",
                    GapSeverity.CRITICAL,
                ),
            )
        return TestPlanRevision.create(
            test_plan_id=context.request.test_plan_id,
            revision_id=context.request.revision_id,
            version=1,
            title="Generated Test Plan",
            objective=context.request.objective,
            scope_items=("Selected conversation evidence",),
            prerequisites=("The selected system is available",),
            risks=("Unverified behavior may require correction",),
            cases=(
                TestCase(
                    "case_primary_flow",
                    1,
                    "Primary flow",
                    context.request.objective,
                    True,
                    (
                        TestScenario(
                            "scenario_primary_flow",
                            1,
                            "Expected outcome",
                            (
                                TestPlanStep(
                                    "step_given_context",
                                    1,
                                    BddKeyword.GIVEN,
                                    "the selected context is available",
                                ),
                                TestPlanStep(
                                    "step_when_action",
                                    2,
                                    BddKeyword.WHEN,
                                    "the business action is performed",
                                ),
                                TestPlanStep(
                                    "step_then_outcome",
                                    3,
                                    BddKeyword.THEN,
                                    "the expected outcome is observed",
                                    "The expected outcome is visible and persisted.",
                                    True,
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            citations=citations,
            assumptions=(),
            unknowns=unknowns,
            coverage_gaps=gaps,
            origin=IdentityOrigin.VALIDATION,
        )
