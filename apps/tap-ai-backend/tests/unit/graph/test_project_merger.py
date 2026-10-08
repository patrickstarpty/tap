from __future__ import annotations

import hashlib

import pytest

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.graph.application.alignment import alignment_key
from tap.modules.graph.application.merger import ProjectGraphMerger
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.graph.domain.project import FragmentRecord, fragment_digest, project_node_id

VALIDATION_SCOPE = ProjectScopeContext(
    enterprise_id="enterprise-1",
    project_id="project-1",
    actor_id="actor-1",
    identity_mode=IdentityMode.VALIDATION,
)


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _snapshot(snapshot_id: str, *, project_id: str = "project-1") -> GraphSnapshot:
    return GraphSnapshot.create(
        snapshot_id=snapshot_id,
        project_id=project_id,
        source_revision_ids=(f"{snapshot_id}-source",),
        document_revision_ids=(f"{snapshot_id}-document",),
        status="READY",
    )


def _draft(
    snapshot_id: str,
    nodes: list[tuple[str, str, str, str]],
    edges: list[tuple[str, str, str, str, float, str]],
    *,
    project_id: str = "project-1",
) -> GraphSnapshotDraft:
    snapshot = _snapshot(snapshot_id, project_id=project_id)
    evidence = []
    graph_nodes = []
    for node_id, label, node_type, canonical_key in nodes:
        evidence_id = f"ev-{node_id}"
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                snapshot_id=snapshot_id,
                source_revision_id=f"{snapshot_id}-source",
                document_revision_id=f"{snapshot_id}-document",
                chunk_id=f"chunk-{node_id}",
                anchor={"kind": "text", "start": 0, "end": 4},
                content_digest=_digest(evidence_id),
            )
        )
        graph_nodes.append(
            GraphNode(node_id, snapshot_id, label, node_type, canonical_key, (evidence_id,))
        )
    graph_edges = []
    for edge_id, source, target, relation_type, confidence, label in edges:
        evidence_id = f"ev-{edge_id}"
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                snapshot_id=snapshot_id,
                source_revision_id=f"{snapshot_id}-source",
                document_revision_id=f"{snapshot_id}-document",
                chunk_id=f"chunk-{edge_id}",
                anchor={"kind": "text", "start": 0, "end": 4},
                content_digest=_digest(evidence_id),
            )
        )
        graph_edges.append(
            GraphEdge(
                edge_id=edge_id,
                snapshot_id=snapshot_id,
                source_node_id=source,
                target_node_id=target,
                relation_type=relation_type,
                origin=RelationOrigin.EXTRACTED,
                confidence=confidence,
                evidence_ids=(evidence_id,),
                relation_label=label,
            )
        )
    return GraphSnapshotDraft(snapshot, tuple(graph_nodes), tuple(graph_edges), tuple(evidence), ())


def _record(snapshot_id: str, draft: GraphSnapshotDraft) -> FragmentRecord:
    return FragmentRecord(
        snapshot_id=snapshot_id,
        revision_id=f"{snapshot_id}-revision",
        status="READY",
        content_digest=_digest(snapshot_id),
        draft=draft,
    )


def test_merge_produces_stable_ids_degrees_communities_and_log():
    draft_a = _draft(
        "frag-a",
        [("a1", "保单", "ENTITY", "保单"), ("a2", "免责条款", "REQUIREMENT", "免责条款")],
        [("ea1", "a1", "a2", "REQUIRES", 0.8, "需要")],
    )
    draft_b = _draft(
        "frag-b",
        [("b1", "保单", "ENTITY", "保单"), ("b2", "理赔", "PROCESS", "理赔")],
        [("eb1", "b1", "b2", "RELATED_TO", 0.7, "关联")],
    )
    records = [_record("frag-a", draft_a), _record("frag-b", draft_b)]

    draft = ProjectGraphMerger().merge(VALIDATION_SCOPE, records)

    assert draft.fragment_digest == fragment_digest(records)
    assert all(
        n.node_id == project_node_id(n.node_type, alignment_key(n.canonical_key))
        for n in draft.nodes
    )
    assert {n.community_id for n in draft.nodes} <= {c.community_id for c in draft.communities}
    assert any(entry.rule == "EXACT" and len(entry.merged_from) == 2 for entry in draft.merge_log)
    policy_node = next(n for n in draft.nodes if n.canonical_key == "保单")
    assert policy_node.degree == 2


def test_merge_of_no_fragments_is_an_empty_draft():
    draft = ProjectGraphMerger().merge(VALIDATION_SCOPE, [])

    assert draft.nodes == ()
    assert draft.edges == ()
    assert draft.fragment_digest == fragment_digest([])


def test_merge_drops_aliases_that_exceed_the_project_alias_column_bound():
    # graph_project_alias.alias_norm is a String(255) column; a label whose
    # normalized form exceeds that bound (node.label itself is bounded only
    # to 512) must be dropped from the alias set instead of failing
    # publication, while the node it names still merges normally.
    oversized_label = "长" * 300
    draft_a = _draft(
        "frag-a",
        [("a1", oversized_label, "ENTITY", "保单")],
        [],
    )
    record = _record("frag-a", draft_a)

    draft = ProjectGraphMerger().merge(VALIDATION_SCOPE, [record])

    assert all(len(alias.alias_norm) <= 255 for alias in draft.aliases)
    node = next(n for n in draft.nodes if n.canonical_key == "保单")
    assert node.label == oversized_label
    assert not any(alias.node_id == node.node_id for alias in draft.aliases)


def test_merge_rejects_scope_mismatch():
    draft_a = _draft(
        "frag-a",
        [("a1", "保单", "ENTITY", "保单")],
        [],
        project_id="other-project",
    )
    record = _record("frag-a", draft_a)

    with pytest.raises(ValueError):
        ProjectGraphMerger().merge(VALIDATION_SCOPE, [record])
