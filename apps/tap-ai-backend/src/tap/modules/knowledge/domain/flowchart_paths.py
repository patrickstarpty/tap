"""Bounded paths over published visual edge evidence, never inferred connections."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from tap.modules.knowledge.domain.models import DocumentAnchor, Evidence

_EDGE = re.compile(
    r"流程图连线 ([A-Za-z0-9_-]+) → ([A-Za-z0-9_-]+)：(.+?) → (.+?)(?:；条件：(.*))?$"
)


def flowchart_path_context(
    evidence: tuple[Evidence, ...], *, max_paths: int = 32, max_hops: int = 12
) -> dict[str, Any]:
    """Enumerate branch alternatives, allowing a retry with a new outcome per visit.

    An edge is traversed at most once per path. Revisiting a decision is a new
    decision occurrence, so failure then success is not a contradictory branch.
    Only retrieved approved image edges participate; coverage is never complete.
    """
    if not 1 <= max_paths <= 64 or not 1 <= max_hops <= 20:
        raise ValueError("flow path budget is invalid")
    groups: dict[tuple[str, str], list[tuple[Evidence, re.Match[str]]]] = defaultdict(list)
    for item in evidence:
        anchor = item.source.anchor
        match = _EDGE.fullmatch(item.content)
        if (
            match
            and item.publication_id
            and item.approval_digest
            and item.approved_item_id
            and isinstance(anchor, DocumentAnchor)
            and anchor.bbox
            and anchor.inventory_item_id == item.approved_item_id
        ):
            groups[item.source.source_id, item.source.revision].append((item, match))
    if not groups:
        return {}
    paths: list[dict[str, Any]] = []
    truncated = False
    for (source, revision), edges in sorted(groups.items()):
        outgoing: dict[str, list[tuple[Evidence, re.Match[str]]]] = defaultdict(list)
        for item, match in sorted(
            edges, key=lambda pair: (pair[1][1], pair[1][2], pair[0].chunk_id)
        ):
            outgoing[match[1]].append((item, match))
        targets = {match[2] for _, match in edges}
        roots = sorted(set(outgoing) - targets) or sorted(outgoing)

        def visit(nodes: list[str], steps: list[dict[str, str]], used: set[str]) -> None:
            nonlocal truncated
            if len(paths) >= max_paths:
                truncated = True
                return
            choices = [
                (item, match)
                for item, match in outgoing.get(nodes[-1], ())
                if item.chunk_id not in used
            ]
            cycle = nodes[-1] in nodes[:-1]
            if not choices or len(steps) >= max_hops or cycle:
                paths.append(
                    {
                        "sourceId": source,
                        "sourceRevision": revision,
                        "nodeIds": nodes,
                        "steps": steps,
                        "evidenceLabels": [step["evidenceLabel"] for step in steps],
                        "termination": "cycle"
                        if cycle
                        else "hop-limit"
                        if choices
                        else "retrieved-boundary",
                    }
                )
                if len(steps) >= max_hops:
                    truncated |= bool(choices)
                    return
            for item, match in choices:
                visit(
                    [*nodes, match[2]],
                    [
                        *steps,
                        {
                            "sourceNodeId": match[1],
                            "targetNodeId": match[2],
                            "sourceLabel": match[3],
                            "targetLabel": match[4],
                            "condition": match[5] or "",
                            "evidenceLabel": item.evidence_label,
                        },
                    ],
                    used | {item.chunk_id},
                )

        for root in roots:
            visit([root], [], set())
    image_sources = {key[0] for key in groups}
    rule_labels = [
        item.evidence_label
        for item in evidence
        if item.source.source_id not in image_sources
        and item.publication_id
        and item.approved_item_id
        and isinstance(item.source.anchor, DocumentAnchor)
        and not item.source.anchor.bbox
    ]
    return {
        "kind": "flowchartPaths",
        "approvedEdgeScopes": [
            {
                "sourceId": source,
                "sourceRevision": revision,
                "evidenceLabels": [item.evidence_label for item, _ in edges],
            }
            for (source, revision), edges in sorted(groups.items())
        ],
        "paths": paths,
        "truncated": truncated,
        "coverage": "retrieved-approved-edges-only",
        "businessRulesStatus": "unknown-from-image",
        "businessRuleCandidateEvidenceLabels": rule_labels,
        "instruction": (
            "Paths are conditional alternatives over retrieved approved edges, not complete flows. "
            "Never join alternative outgoing branches at the same decision occurrence. "
            "Describe different branch alternatives in separate claims and paragraphs. "
            "A repeated node is a new visit; retry may change the outcome. "
            "Do not invent missing edges, "
            "thresholds, priorities or business rules. "
            "Missing business rules are unknown from the image. "
            "Document labels are candidates only: "
            "use a rule only when its text explicitly supports it; "
            "otherwise state it is unknown. "
            "Cite every traversed edge and any supporting document rule."
        ),
    }


def flowchart_claim_is_consistent(
    evidence_labels: tuple[str, ...], context: dict[str, Any]
) -> bool:
    """Fail closed when one claim joins branches absent from any supported path."""
    labels_by_revision: dict[tuple[str, str], set[str]] = defaultdict(set)
    paths_by_revision: dict[tuple[str, str], list[set[str]]] = defaultdict(list)
    for path in context.get("paths", ()):
        identity = (path["sourceId"], path["sourceRevision"])
        labels = set(path["evidenceLabels"])
        labels_by_revision[identity].update(labels)
        paths_by_revision[identity].append(labels)
    for scope in context.get("approvedEdgeScopes", ()):
        labels_by_revision[scope["sourceId"], scope["sourceRevision"]].update(
            scope["evidenceLabels"]
        )
    for identity, flow_labels in labels_by_revision.items():
        cited = flow_labels.intersection(evidence_labels)
        if cited and not any(cited <= labels for labels in paths_by_revision[identity]):
            return False
    return True
