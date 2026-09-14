#!/usr/bin/env python3
"""Generate the deterministic, reviewable QUALITY-GRAPH-01 candidate dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tap.modules.ai.domain.models import schema_digest, text_digest
from tap.modules.graph.adapters.model_gateway_extraction import (
    GRAPH_EXTRACTION_PROMPT,
    GRAPH_EXTRACTION_SCHEMA,
)

_INFERENCE_RULE = "When X DEPENDS_ON Y and Y GOVERNS Z, derive X INDIRECTLY_GOVERNS Z."
_INFERENCE_RULE_DIGEST = (
    "sha256:" + hashlib.sha256(_INFERENCE_RULE.encode()).hexdigest()
)


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _document(index: int) -> tuple[dict[str, object], list[dict[str, object]]]:
    number = f"{index:02d}"
    actor = f"reviewer-{number}"
    system = f"claims-portal-{number}"
    requirement_a = f"requirement-{number}-evidence"
    requirement_b = f"requirement-{number}-approval"
    content = (
        f"# Claims control {number}\n\n"
        f"Actor {actor} uses system {system}.\n\n"
        f"Requirement {requirement_a} applies to {actor} and governs {system}: every claim "
        "must cite immutable source evidence.\n\n"
        f"Requirement {requirement_b} depends on {requirement_a}: approval is allowed only "
        "after the evidence check succeeds.\n\n"
        f"The same actor {actor} records the final approval decision in {system}.\n\n"
        "## Canonical graph facts\n\n"
        f"- `{actor} --USES--> {system}`\n"
        f"- `{requirement_a} --APPLIES_TO--> {actor}`\n"
        f"- `{requirement_a} --GOVERNS--> {system}`\n"
        f"- `{requirement_b} --DEPENDS_ON--> {requirement_a}`\n\n"
        "## Declared inference rule\n\n"
        f"{_INFERENCE_RULE} Cite both input edges as provenance. Rule digest: "
        f"`{_INFERENCE_RULE_DIGEST}`.\n"
    )
    document_id = f"quality-graph-{number}"
    expectations = [
        ("node", "EXTRACTED", {"canonicalKey": actor, "nodeType": "ACTOR"}),
        ("node", "EXTRACTED", {"canonicalKey": system, "nodeType": "SYSTEM"}),
        (
            "node",
            "EXTRACTED",
            {"canonicalKey": requirement_a, "nodeType": "REQUIREMENT"},
        ),
        (
            "node",
            "EXTRACTED",
            {"canonicalKey": requirement_b, "nodeType": "REQUIREMENT"},
        ),
        (
            "edge",
            "EXTRACTED",
            {"source": actor, "relationType": "USES", "target": system},
        ),
        (
            "edge",
            "EXTRACTED",
            {"source": requirement_a, "relationType": "APPLIES_TO", "target": actor},
        ),
        (
            "edge",
            "EXTRACTED",
            {"source": requirement_a, "relationType": "GOVERNS", "target": system},
        ),
        (
            "edge",
            "EXTRACTED",
            {
                "source": requirement_b,
                "relationType": "DEPENDS_ON",
                "target": requirement_a,
            },
        ),
        (
            "merge",
            "EXTRACTED",
            {"canonicalKey": actor, "mentionCount": 2, "shouldMerge": True},
        ),
        (
            "edge",
            "INFERRED",
            {
                "source": requirement_b,
                "relationType": "INDIRECTLY_GOVERNS",
                "target": system,
                "inputFacts": [
                    f"{requirement_b}:DEPENDS_ON:{requirement_a}",
                    f"{requirement_a}:GOVERNS:{system}",
                ],
            },
        ),
    ]
    labels = [
        {
            "labelId": f"{document_id}-label-{ordinal:02d}",
            "documentId": document_id,
            "kind": kind,
            "origin": origin,
            "expected": expected,
            "predicted": True,
            "correct": True,
            "evidenceResolvable": True,
            "provenanceComplete": True,
            "reviewer": "generated-candidate-requires-independent-human-review",
        }
        for ordinal, (kind, origin, expected) in enumerate(expectations, start=1)
    ]
    return (
        {
            "documentId": document_id,
            "mediaType": "text/markdown",
            "content": content,
            "contentDigest": _digest(content),
        },
        labels,
    )


def build(evaluator: Path) -> dict[str, object]:
    documents: list[dict[str, object]] = []
    labels: list[dict[str, object]] = []
    for index in range(1, 21):
        document, judgments = _document(index)
        documents.append(document)
        labels.extend(judgments)
    return {
        "schemaVersion": "quality-graph-profile-v1",
        "profileId": "QUALITY-GRAPH-01",
        "dataset": {
            "version": "candidate-v2",
            "reviewStatus": "pending",
            "labelingMethod": "generated-candidate-requires-independent-human-review",
            "notice": (
                "The corpus and expected facts are generated review material. The observation "
                "booleans are not acceptance evidence until an independent reviewer approves them."
            ),
        },
        "bindings": {
            "modelAlias": "tapper-chat",
            "actualModel": "pending/real-model-run",
            "promptDigest": text_digest(GRAPH_EXTRACTION_PROMPT),
            "schemaDigest": schema_digest(GRAPH_EXTRACTION_SCHEMA),
            "evaluatorDigest": "sha256:"
            + hashlib.sha256(evaluator.read_bytes()).hexdigest(),
        },
        "documents": documents,
        "labels": labels,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--evaluator",
        type=Path,
        default=Path("scripts/evaluate-quality-graph.py"),
    )
    arguments = parser.parse_args()
    profile = build(arguments.evaluator)
    arguments.output.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
