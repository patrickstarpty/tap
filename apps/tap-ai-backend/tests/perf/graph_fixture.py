"""Deterministic synthetic graph for query latency benchmarks."""

from __future__ import annotations

import hashlib
import itertools
import random

from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)

PROJECT_ID = "tapper-demo"
SNAPSHOT_ID = "snapshot-bench"
CHUNKS = 2_000
_NODE_TYPES = ("ENTITY", "CONCEPT", "REQUIREMENT", "SYSTEM", "ACTOR")
_RELATIONS = ("DEPENDS_ON", "GOVERNS", "SUPPORTS", "RELATES_TO", "IMPLEMENTS")
_SYLLABLES = ("ka", "lo", "mi", "ren", "sor", "tu", "vex", "zan", "qui", "dor", "pel", "nax")
_CJK = ("登录", "支付", "服务", "风险", "账户", "订单", "网关", "审计")


def _vocabulary(rng: random.Random) -> tuple[str, ...]:
    words = {"".join(rng.choices(_SYLLABLES, k=rng.randint(2, 3))) for _ in range(600)}
    return tuple(sorted(words))


def synthetic_draft(
    *, nodes: int = 10_000, edges: int = 50_000, seed: int = 7
) -> GraphSnapshotDraft:
    rng = random.Random(seed)
    words = _vocabulary(rng)
    snapshot = GraphSnapshot.create(
        snapshot_id=SNAPSHOT_ID,
        project_id=PROJECT_ID,
        source_revision_ids=("source-revision-bench",),
        document_revision_ids=("document-revision-bench",),
        status="READY",
    )
    node_ids = [f"node-{index:05d}" for index in range(nodes)]
    graph_nodes = []
    for index, identity in enumerate(node_ids):
        parts = rng.sample(words, rng.randint(1, 3))
        if index % 5 == 0:
            parts.append(rng.choice(_CJK))
        label = " ".join(parts).title()
        graph_nodes.append(
            GraphNode(
                identity,
                SNAPSHOT_ID,
                label,
                _NODE_TYPES[index % len(_NODE_TYPES)],
                "_".join(parts) + f"_{index}",
            )
        )
    weights = list(itertools.accumulate(rng.paretovariate(1.5) for _ in node_ids))
    graph_edges = []
    evidence = []
    for index in range(edges):
        source, target = rng.choices(node_ids, cum_weights=weights, k=2)
        while target == source:
            target = rng.choices(node_ids, cum_weights=weights)[0]
        evidence_id = f"evidence-{index:05d}"
        evidence.append(
            Evidence(
                evidence_id,
                SNAPSHOT_ID,
                "source-revision-bench",
                "document-revision-bench",
                f"chunk-{index % CHUNKS:04d}",
                {"kind": "text", "start": 0, "end": 16},
                "sha256:" + hashlib.sha256(evidence_id.encode()).hexdigest(),
            )
        )
        graph_edges.append(
            GraphEdge(
                f"edge-{index:05d}",
                SNAPSHOT_ID,
                source,
                target,
                _RELATIONS[index % len(_RELATIONS)],
                RelationOrigin.EXTRACTED,
                1.0,
                (evidence_id,),
            )
        )
    # The draft exceeds the per-publication extraction bounds (500 nodes / 2,000
    # edges) on purpose: the benchmark measures index queries at scale, so the
    # facts are validated individually and the aggregate bound check is skipped.
    draft = object.__new__(GraphSnapshotDraft)
    for name, value in (
        ("snapshot", snapshot),
        ("nodes", tuple(graph_nodes)),
        ("edges", tuple(graph_edges)),
        ("evidence", tuple(evidence)),
        ("provenance", ()),
    ):
        object.__setattr__(draft, name, value)
    return draft
