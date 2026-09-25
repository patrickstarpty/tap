"""Schema-locked grounded Test Plan generation through the shared ModelGateway."""

from __future__ import annotations

import hashlib
import json
import math
from typing import cast

from tap.modules.ai.adapters.litellm import ProviderModelMapping
from tap.modules.ai.domain.models import ModelOperation, ModelRequest, schema_digest, text_digest
from tap.modules.ai.ports.gateway import ModelGateway
from tap.modules.test_management.domain.models import (
    BddKeyword,
    CitationOrigin,
    GapSeverity,
    IdentityOrigin,
    TestCase,
    TestPlanAssumption,
    TestPlanCitation,
    TestPlanCoverageGap,
    TestPlanRevision,
    TestPlanStep,
    TestPlanUnknown,
    TestScenario,
)
from tap.modules.test_management.domain.validation import validate_draft_structure
from tap.modules.test_management.ports.generation import TestDesignContext

_STRING = {"type": "string"}
_STRING_ARRAY = {"type": "array", "items": _STRING}


def _object(required: list[str], properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


TEST_DESIGN_SCHEMA: dict[str, object] = _object(
    [
        "title",
        "objective",
        "scope",
        "prerequisites",
        "risks",
        "cases",
        "citations",
        "assumptions",
        "unknowns",
        "coverageGaps",
    ],
    {
        "title": _STRING,
        "objective": _STRING,
        "scope": _STRING_ARRAY,
        "prerequisites": _STRING_ARRAY,
        "risks": _STRING_ARRAY,
        "cases": {
            "type": "array",
            "items": _object(
                [
                    "id",
                    "title",
                    "objective",
                    "critical",
                    "coveredRequirementIds",
                    "scenarios",
                ],
                {
                    "id": _STRING,
                    "title": _STRING,
                    "objective": _STRING,
                    "critical": {"type": "boolean"},
                    "coveredRequirementIds": _STRING_ARRAY,
                    "scenarios": {
                        "type": "array",
                        "items": _object(
                            ["id", "title", "steps"],
                            {
                                "id": _STRING,
                                "title": _STRING,
                                "steps": {
                                    "type": "array",
                                    "items": _object(
                                        [
                                            "id",
                                            "keyword",
                                            "text",
                                            "expectedResult",
                                            "critical",
                                            "citationIds",
                                            "unknownIds",
                                        ],
                                        {
                                            "id": _STRING,
                                            "keyword": {
                                                "type": "string",
                                                "enum": ["Given", "When", "Then", "And", "But"],
                                            },
                                            "text": _STRING,
                                            "expectedResult": _STRING,
                                            "critical": {"type": "boolean"},
                                            "citationIds": _STRING_ARRAY,
                                            "unknownIds": _STRING_ARRAY,
                                        },
                                    ),
                                },
                            },
                        ),
                    },
                },
            ),
        },
        "citations": {
            "type": "array",
            "items": _object(
                ["citationSnapshotId"],
                {"citationSnapshotId": _STRING},
            ),
        },
        "assumptions": {
            "type": "array",
            "items": _object(
                ["id", "text", "graphEdgeId"],
                {
                    "id": _STRING,
                    "text": _STRING,
                    "graphEdgeId": _STRING,
                },
            ),
        },
        "unknowns": {
            "type": "array",
            "items": _object(
                ["id", "text", "requirementRef"],
                {"id": _STRING, "text": _STRING, "requirementRef": _STRING},
            ),
        },
        "coverageGaps": {
            "type": "array",
            "items": _object(
                ["id", "requirementRef", "reason", "severity"],
                {
                    "id": _STRING,
                    "requirementRef": _STRING,
                    "reason": _STRING,
                    "severity": {
                        "type": "string",
                        "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    },
                },
            ),
        },
    },
)

TEST_DESIGN_PROMPT = (
    "Design a grounded Test Plan from the frozen input and answer-evidence snapshots. Return "
    "exactly the JSON schema. Title, objective, scope, prerequisites, risks, cases, scenarios, "
    "and steps must all be non-empty. Use stable lowercase identifiers beginning with a letter. "
    "Every "
    "scenario must start with Given, contain When, and contain Then. Every Then must include a "
    "nonblank expectedResult; mark critical outcomes explicitly and bind every critical Then to "
    "authorized citationSnapshotIds. Never turn an unresolved unknown into a definite Then. "
    "Return citationSnapshotId only; the server reconstructs every evidence field from the "
    "authorized immutable snapshot. "
    "Never present inferred graph context as fact: record it under assumptions. Record missing "
    "information under unknowns and uncovered requirements under coverageGaps. Use only IDs from "
    "the frozen RequirementScopeSnapshot and keep uncovered requirements in Coverage Gaps. "
    "Generate Draft "
    "content only; never publish or change workflow state."
)
TEST_DESIGN_PROFILE_DIGEST = text_digest(
    TEST_DESIGN_PROMPT + "\n" + schema_digest(TEST_DESIGN_SCHEMA)
)


def design_model_revision_id(model_alias: str, mapping: ProviderModelMapping) -> str:
    """Freeze the actual private provider/model route plus the workload profile."""

    if not isinstance(model_alias, str) or not model_alias.strip():
        raise ValueError("test design model alias must be nonblank")
    if not isinstance(mapping, ProviderModelMapping):
        raise TypeError("test design model mapping is required")
    material = json.dumps(
        {
            "modelAlias": model_alias,
            "provider": mapping.provider,
            "providerModel": mapping.model,
            "promptSchemaProfileDigest": TEST_DESIGN_PROFILE_DIGEST,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "tmr_" + hashlib.sha256(material.encode()).hexdigest()


class ModelGatewayTestDesign:
    def __init__(self, gateway: ModelGateway, *, timeout_seconds: float = 30.0) -> None:
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise ValueError("test design timeout must be between 0 and 60 seconds")
        self._gateway = gateway
        self._timeout_seconds = timeout_seconds

    async def generate(self, context: TestDesignContext) -> TestPlanRevision:
        if context.request.project_id != context.scope.project_id:
            raise ValueError("test design context is outside Project scope")
        frozen = {
            "objective": context.request.objective,
            "inputSnapshot": dict(context.input_snapshot),
            "answerEvidenceSnapshot": dict(context.answer_evidence_snapshot),
            "requirementScopeSnapshot": (
                None
                if context.request.requirement_scope is None
                else {
                    "scopeId": context.request.requirement_scope.scope_id,
                    "version": context.request.requirement_scope.version,
                    "contentDigest": context.request.requirement_scope.content_digest,
                    "requirements": [
                        {
                            "requirementId": item.requirement_id,
                            "sourceRevisionId": item.source_revision_id,
                            "locator": item.locator,
                        }
                        for item in context.request.requirement_scope.requirements
                    ],
                }
            ),
            "approvedKnowledgeRevisionIds": list(context.request.approved_knowledge_revision_ids),
            "modelRevisionId": context.request.model_revision_id,
        }
        agent_digest = context.input_snapshot.get("agent_revision_digest")
        skill_digests = context.input_snapshot.get("skill_revision_digests")
        if (
            not isinstance(agent_digest, str)
            or not isinstance(skill_digests, list)
            or not all(isinstance(item, str) for item in skill_digests)
        ):
            raise ValueError("test design governance digests are missing from frozen input")
        result = await self._gateway.generate_structured(
            ModelRequest(
                scope=context.scope,
                alias=context.request.model_alias,
                operation=ModelOperation.STRUCTURED,
                prompt=TEST_DESIGN_PROMPT,
                prompt_digest=text_digest(TEST_DESIGN_PROMPT),
                context=json.dumps(frozen, sort_keys=True, separators=(",", ":")),
                timeout_seconds=self._timeout_seconds,
                idempotency_key=context.request.request_digest,
                schema=TEST_DESIGN_SCHEMA,
                schema_digest=schema_digest(TEST_DESIGN_SCHEMA),
                tool_allowlist=frozenset({"knowledge.answer"}),
                governance_digests=(agent_digest, *cast(list[str], skill_digests)),
            )
        )
        if not isinstance(result.output, dict):
            raise ValueError("test design output must be an object")
        return _revision(context, cast(dict[str, object], result.output))


def _revision(context: TestDesignContext, raw: dict[str, object]) -> TestPlanRevision:
    expected = cast(dict[str, object], TEST_DESIGN_SCHEMA["properties"])
    if set(raw) != set(expected):
        raise ValueError("test design output has invalid fields")
    try:
        cases = tuple(
            TestCase(
                _string(case, "id"),
                case_index,
                _string(case, "title"),
                _string(case, "objective"),
                _boolean(case, "critical"),
                tuple(
                    TestScenario(
                        _string(scenario, "id"),
                        scenario_index,
                        _string(scenario, "title"),
                        tuple(
                            TestPlanStep(
                                _string(step, "id"),
                                step_index,
                                BddKeyword(_string(step, "keyword")),
                                _string(step, "text"),
                                _nullable_string(step, "expectedResult"),
                                _boolean(step, "critical"),
                                _strings(step, "citationIds"),
                                _strings(step, "unknownIds"),
                            )
                            for step_index, step in enumerate(_objects(scenario, "steps"), 1)
                        ),
                    )
                    for scenario_index, scenario in enumerate(_objects(case, "scenarios"), 1)
                ),
                _strings(case, "coveredRequirementIds"),
            )
            for case_index, case in enumerate(_objects(raw, "cases"), 1)
        )
        raw_authorized = context.answer_evidence_snapshot.get(
            "authorizedEvidence", context.answer_evidence_snapshot.get("citations", [])
        )
        if not isinstance(raw_authorized, list):
            raise ValueError("frozen evidence citations are malformed")
        authorized_evidence = {
            item.get("citationSnapshotId"): item
            for item in raw_authorized
            if isinstance(item, dict) and isinstance(item.get("citationSnapshotId"), str)
        }
        raw_citations = _objects(raw, "citations")
        if any(set(item) != {"citationSnapshotId"} for item in raw_citations):
            raise ValueError("test design citation is malformed")
        citations = tuple(
            TestPlanCitation(
                _string(item, "citationSnapshotId"),
                _string(
                    authorized_evidence[_string(item, "citationSnapshotId")], "sourceRevisionId"
                ),
                _string(
                    authorized_evidence[_string(item, "citationSnapshotId")],
                    "documentRevisionId",
                ),
                _string(authorized_evidence[_string(item, "citationSnapshotId")], "chunkId"),
                _string(authorized_evidence[_string(item, "citationSnapshotId")], "contentDigest"),
                _string(authorized_evidence[_string(item, "citationSnapshotId")], "claimText"),
                CitationOrigin(
                    _string(authorized_evidence[_string(item, "citationSnapshotId")], "origin")
                ),
                (
                    authorized_evidence.get(_string(item, "citationSnapshotId"), {}).get("anchor")
                    if isinstance(
                        authorized_evidence.get(_string(item, "citationSnapshotId"), {}).get(
                            "anchor"
                        ),
                        dict,
                    )
                    else None
                ),
            )
            for item in raw_citations
        )
        assumptions = tuple(
            TestPlanAssumption(
                _string(item, "id"),
                _string(item, "text"),
                _nullable_string(item, "graphEdgeId"),
            )
            for item in _objects(raw, "assumptions")
        )
        unknowns = tuple(
            TestPlanUnknown(
                _string(item, "id"),
                _string(item, "text"),
                _string(item, "requirementRef"),
            )
            for item in _objects(raw, "unknowns")
        )
        gaps = tuple(
            TestPlanCoverageGap(
                _string(item, "id"),
                _string(item, "requirementRef"),
                _string(item, "reason"),
                GapSeverity(_string(item, "severity")),
            )
            for item in _objects(raw, "coverageGaps")
        )
        revision = TestPlanRevision.create(
            test_plan_id=context.request.test_plan_id,
            revision_id=context.request.revision_id,
            version=1,
            title=_string(raw, "title"),
            objective=_string(raw, "objective"),
            scope_items=_strings(raw, "scope"),
            prerequisites=_strings(raw, "prerequisites"),
            risks=_strings(raw, "risks"),
            cases=cases,
            citations=citations,
            assumptions=assumptions,
            unknowns=unknowns,
            coverage_gaps=gaps,
            origin=IdentityOrigin.VALIDATION,
            requirement_scope=context.request.requirement_scope,
            approved_knowledge_revision_ids=context.request.approved_knowledge_revision_ids,
            model_revision_id=context.request.model_revision_id,
            agent_revision_id=context.request.agent_revision_id,
            skill_revision_ids=context.request.skill_revision_ids,
            author_actor_id=context.scope.actor_id,
            strict_review_required=context.request.strict_review_required,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("test design output is malformed") from error
    _validate_citations(context, revision.citations)
    validate_draft_structure(revision)
    return revision


def _validate_citations(
    context: TestDesignContext, citations: tuple[TestPlanCitation, ...]
) -> None:
    raw_evidence = context.answer_evidence_snapshot.get(
        "authorizedEvidence", context.answer_evidence_snapshot.get("citations", [])
    )
    if not isinstance(raw_evidence, list):
        raise ValueError("frozen evidence citations are malformed")
    authorized = {
        (
            item.get("citationSnapshotId"),
            item.get("sourceRevisionId"),
            item.get("documentRevisionId"),
            item.get("chunkId"),
            item.get("contentDigest"),
            item.get("claimText"),
            item.get("origin"),
        )
        for item in raw_evidence
        if isinstance(item, dict)
    }
    for citation in citations:
        identity = (
            citation.citation_id,
            citation.source_revision_id,
            citation.document_revision_id,
            citation.chunk_id,
            citation.content_digest,
            citation.claim_text,
            citation.origin.value,
        )
        if identity not in authorized:
            raise ValueError("test design citation is outside frozen evidence")


def _objects(value: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    raw = value[key]
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise TypeError(key)
    return tuple(cast(dict[str, object], item) for item in raw)


def _string(value: dict[str, object], key: str) -> str:
    raw = value[key]
    if not isinstance(raw, str):
        raise TypeError(key)
    return raw


def _nullable_string(value: dict[str, object], key: str) -> str | None:
    raw = value[key]
    if not isinstance(raw, str):
        raise TypeError(key)
    return raw or None


def _strings(value: dict[str, object], key: str) -> tuple[str, ...]:
    raw = value[key]
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise TypeError(key)
    return tuple(cast(list[str], raw))


def _boolean(value: dict[str, object], key: str) -> bool:
    raw = value[key]
    if type(raw) is not bool:
        raise TypeError(key)
    return cast(bool, raw)
