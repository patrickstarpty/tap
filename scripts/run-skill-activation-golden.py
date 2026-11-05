#!/usr/bin/env python3
"""Score Skill auto-activation precision (Skill Hub spec 7) against the
`skill-activation-golden-v1` fixture: ten questions, each judged against
every candidate Skill in the fixture catalog. Scoring lives in
`tap.quality.skill_activation_golden`; this script owns file I/O, the
real-model call and the CLI, mirroring `scripts/run-intent-golden.py`.

- `fake` (deterministic, no external services, no model): validates the
  fixture, scores the committed `skill-activation-observations-v1`
  observations and exits 0/1 by the gate. It checks the fixture and the
  scorer, not model behaviour.
- `real` (opt-in, requires `TAP_RUN_QUALITY_SKILL_ACTIVATION=1` and
  `TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION=approved:<reviewer>`): builds
  the production `KnowledgeModelGateway` from `TapperSettings` (the same
  `_create_embeddings` factory `tap.entrypoints.tapper_runtime` uses),
  renders the fixture Skills as the "Available Skills" catalog and calls
  `answer` once per case with the case's evidence and
  `tools=("knowledge.search", "skill.activate")`. Only the names passed to
  `skill_activate` in that first tool round are observed; they are written
  to `--observations-out`, then scored. A case whose model call fails is
  recorded under `errors` and fails the gate. Never run by an automated
  agent -- a human runs this against a real model.

Exit codes: 0 gate passed, 1 gate failed, 2 refused or config/IO error.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tap.modules.ai.domain.skill_activation import (
    SkillCatalogEntry,
    TurnSkillContext,
    truncate_description,
)
from tap.modules.ai.domain.skill_metadata import SKILL_CATEGORIES
from tap.modules.ai.domain.tools import tool_for_wire_name
from tap.quality.skill_activation_golden import (
    OBSERVATIONS_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    compute_activation_metrics,
    gate_passed,
    no_skill_cases_with_any_activation,
    validate_activation_golden,
    validate_activation_observations,
)

_PROFILE_ID = "quick-hybrid-v1"
_TOOLS: tuple[str, ...] = ("knowledge.search", "skill.activate")
# Recorded for a `skill_activate` call whose arguments carry no usable name;
# never a fixture Skill, so it scores as a wrong activation.
_INVALID_ARGUMENTS = "(invalid skill_activate arguments)"


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def catalog_entries(golden: Mapping[str, Any]) -> tuple[SkillCatalogEntry, ...]:
    """The fixture Skills as catalog entries with synthetic ids, ordered by
    category then name like `build_skill_catalog`."""
    entries = [
        SkillCatalogEntry(
            skill_id=f"skill-activation-golden-{index}",
            revision_id=f"skill-activation-golden-{index}-r1",
            name=str(skill["name"]),
            description=truncate_description(str(skill["description"])),
            category=skill["category"],
        )
        for index, skill in enumerate(golden["skills"], start=1)
    ]
    entries.sort(key=lambda entry: (SKILL_CATEGORIES.index(entry.category), entry.name))
    return tuple(entries)


def activated_names(tool_calls: Sequence[Any]) -> list[str]:
    """Names passed to `skill_activate` in one tool round, in call order."""
    names: list[str] = []
    for call in tool_calls:
        definition = tool_for_wire_name(call.name)
        if definition is None or definition.tool_id != "skill.activate":
            continue
        try:
            arguments = json.loads(call.arguments_json)
        except (TypeError, ValueError):
            arguments = None
        name = arguments.get("name") if isinstance(arguments, dict) else None
        names.append(
            name if isinstance(name, str) and name.strip() else _INVALID_ARGUMENTS
        )
    return names


def _evidence(case: Mapping[str, Any]) -> tuple[Any, ...]:
    from tap.modules.knowledge.domain.models import (
        ContentRole,
        DocumentAnchor,
        Evidence,
        IndexRevision,
        RevisionKind,
        SourceFamily,
        SourceRevisionRef,
    )

    case_id = str(case["id"])
    items: list[Evidence] = []
    for ordinal, passage in enumerate(case["evidence"], start=1):
        text = str(passage)
        items.append(
            Evidence(
                family=SourceFamily.DOC,
                chunk_id=f"{case_id}-{ordinal}",
                logical_chunk_id=f"{case_id}-{ordinal}",
                title=case_id,
                content=text,
                source=SourceRevisionRef(
                    source_id=f"source-{case_id}",
                    source_type="doc",
                    revision_kind=RevisionKind.BLOB_VERSION,
                    revision=f"rev-{case_id}",
                    source_content_hash=_digest(f"skill-activation-source-{case_id}"),
                    anchor=DocumentAnchor(start_offset=0, end_offset=len(text)),
                ),
                chunk_content_hash=_digest(text),
                content_role=ContentRole.SOURCE,
                citation_id=f"c-{case_id}-{ordinal}",
                evidence_label=f"S{ordinal}",
                index_revision=IndexRevision(
                    physical_index="skill-activation-golden",
                    schema_version="v1",
                    corpus_version="skill-activation-golden-v1",
                ),
                embedding_model_version="skill-activation-golden",
                acl_decision_id="skill-activation-golden",
                score=1.0,
            )
        )
    return tuple(items)


async def run_real(golden: Mapping[str, Any]) -> dict[str, Any]:
    """One production `answer` call per case; opt-in only (module docstring)."""
    from tap.entrypoints.tapper_runtime import TapperSettings, _create_embeddings
    from tap.modules.knowledge.ports.errors import AnswerUnavailable

    settings = TapperSettings.from_mapping(dict(os.environ))
    models = _create_embeddings(settings)
    skills = TurnSkillContext(catalog=catalog_entries(golden))
    observations: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    try:
        for case in golden["cases"]:
            case_id = str(case["id"])
            try:
                generation = await models.answer(
                    str(case["question"]),
                    _evidence(case),
                    _PROFILE_ID,
                    tools=_TOOLS,
                    skills=skills,
                )
            except AnswerUnavailable as error:
                observations[case_id] = []
                errors[case_id] = str(error) or type(error).__name__
                continue
            observations[case_id] = activated_names(generation.tool_calls)
    finally:
        aclose = getattr(models.gateway, "aclose", None)
        if aclose is not None:
            await aclose()
    document: dict[str, Any] = {
        "version": OBSERVATIONS_SCHEMA_VERSION,
        "mode": "real",
        "observations": observations,
    }
    if errors:
        document["errors"] = errors
    return document


def evaluate(
    golden: Mapping[str, Any], observations_document: Mapping[str, Any]
) -> dict[str, Any]:
    """Score an observations document and build the report; pure, no I/O."""
    observations = validate_activation_observations(observations_document)
    metrics = compute_activation_metrics(golden, observations)
    errors = observations_document.get("errors") or {}
    passed = gate_passed(metrics) and not errors
    no_skill_hits, no_skill_total = no_skill_cases_with_any_activation(metrics)
    return {
        "version": REPORT_SCHEMA_VERSION,
        "shouldRate": metrics.should_rate,
        "shouldNotRate": metrics.should_not_rate,
        "shouldPairs": sum(pair.expected for pair in metrics.pairs),
        "shouldNotPairs": sum(not pair.expected for pair in metrics.pairs),
        "misses": [
            {
                "caseId": pair.case_id,
                "skill": pair.skill_name,
                "expected": pair.expected,
                "activated": pair.activated,
            }
            for pair in metrics.pairs
            if not pair.hit
        ],
        # Informational, never gated: no-skill cases with any activation.
        "noSkillCasesWithAnyActivation": f"{no_skill_hits}/{no_skill_total}",
        "errors": dict(errors),
        "passed": passed,
    }


def _real_mode_refusal() -> str | None:
    if os.environ.get("TAP_RUN_QUALITY_SKILL_ACTIVATION") != "1":
        return "error: --mode real requires TAP_RUN_QUALITY_SKILL_ACTIVATION=1"
    authorization = os.environ.get("TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION", "")
    if not authorization.startswith("approved:") or authorization == "approved:":
        return (
            "error: --mode real requires explicit model execution authorization "
            "(TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION=approved:<reviewer>)"
        )
    return None


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "real"), required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument(
        "--observations", type=Path, help="fake mode: committed observations to score"
    )
    parser.add_argument(
        "--observations-out",
        type=Path,
        help="real mode: where to write the observations",
    )
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args(argv)

    if arguments.mode == "real":
        refusal = _real_mode_refusal()
        if refusal is not None:
            print(refusal, file=sys.stderr)
            return 2
        if arguments.observations_out is None:
            print("error: --mode real requires --observations-out", file=sys.stderr)
            return 2
    elif arguments.observations is None:
        print("error: --mode fake requires --observations", file=sys.stderr)
        return 2

    try:
        golden = json.loads(arguments.golden.read_text(encoding="utf-8"))
        validate_activation_golden(golden)
        if arguments.mode == "fake":
            observations_document = json.loads(
                arguments.observations.read_text(encoding="utf-8")
            )
        else:
            observations_document = asyncio.run(run_real(golden))
            _write_json(arguments.observations_out, observations_document)
        report = evaluate(golden, observations_document)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if arguments.report is not None:
        _write_json(arguments.report, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
