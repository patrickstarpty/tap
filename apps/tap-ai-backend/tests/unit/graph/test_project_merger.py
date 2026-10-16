from __future__ import annotations

import hashlib

import pytest

import tap.modules.graph.domain.project as project_domain
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


def test_fragment_digest_changes_with_the_merge_rules_version(monkeypatch):
    # mysql_merge.py only republishes a new project version when fragment_digest
    # moves (ProjectGraphMergeStore.complete compares it against the current
    # READY version's own digest); a rules-only deploy touches no fragment, so
    # without folding MERGE_RULES_VERSION into the digest, every already-merged
    # project would stay on its pre-deploy version until something re-extracts.
    draft_a = _draft("frag-a", [("a1", "保单", "ENTITY", "保单")], [])
    records = [_record("frag-a", draft_a)]

    before = fragment_digest(records)
    monkeypatch.setattr(
        project_domain, "MERGE_RULES_VERSION", project_domain.MERGE_RULES_VERSION + 1
    )
    after = fragment_digest(records)

    assert before != after


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


def _node_with_evidence(
    node_id: str,
    snapshot_id: str,
    label: str,
    node_type: str,
    canonical_key: str,
    aliases: tuple[str, ...] = (),
) -> tuple[GraphNode, Evidence]:
    evidence_id = f"ev-{node_id}"
    evidence = Evidence(
        evidence_id=evidence_id,
        snapshot_id=snapshot_id,
        source_revision_id=f"{snapshot_id}-source",
        document_revision_id=f"{snapshot_id}-document",
        chunk_id=f"chunk-{node_id}",
        anchor={"kind": "text", "start": 0, "end": 4},
        content_digest=_digest(evidence_id),
    )
    node = GraphNode(node_id, snapshot_id, label, node_type, canonical_key, (evidence_id,), aliases)
    return node, evidence


def test_merge_cleans_the_persisted_alias_list():
    # Real-model output left "王先生" and "全残（释义七）" as displayed aliases; the
    # merged alias table should carry neither verbatim -- the person-example is
    # dropped and the clause-numbered one keys on its real term, "全残".
    node, evidence = _node_with_evidence(
        "n1", "frag-a", "全残", "CONCEPT", "全残", ("全残（释义七）", "王先生")
    )
    fragment = GraphSnapshotDraft(_snapshot("frag-a"), (node,), (), (evidence,), ())
    record = _record("frag-a", fragment)

    draft = ProjectGraphMerger().merge(VALIDATION_SCOPE, [record])

    project_node = next(n for n in draft.nodes if n.canonical_key == "全残")
    norms = {alias.alias_norm for alias in draft.aliases if alias.node_id == project_node.node_id}
    assert norms == {"全残"}


def test_merge_does_not_absorb_an_alias_that_names_a_sibling_entity():
    # Real-model output had a "责任免除" node whose model aliases absorbed
    # concrete exclusion items ("故意杀害", ...) that are themselves separately
    # named, evidenced nodes elsewhere in the same document. Different node
    # types keep the two from merging into one node at all (see the report for
    # why the same-type case -- indistinguishable, structurally, from a wanted
    # alias-to-named-entity merge like "Underwriting"/"核保" -- is not fixed
    # here); this is the narrower defense the alignment side can make: a model
    # alias naming a real sibling entity outright is never displayed either.
    exclusion_node, exclusion_evidence = _node_with_evidence(
        "n1", "frag-a", "故意杀害", "REQUIREMENT", "故意杀害"
    )
    liability_node, liability_evidence = _node_with_evidence(
        "n2", "frag-a", "责任免除", "CONCEPT", "责任免除", ("故意杀害", "免责")
    )
    draft_fragment = GraphSnapshotDraft(
        _snapshot("frag-a"),
        (exclusion_node, liability_node),
        (),
        (exclusion_evidence, liability_evidence),
        (),
    )
    record = _record("frag-a", draft_fragment)

    draft = ProjectGraphMerger().merge(VALIDATION_SCOPE, [record])

    project_node = next(n for n in draft.nodes if n.canonical_key == "责任免除")
    liability_aliases = {
        alias.alias_norm for alias in draft.aliases if alias.node_id == project_node.node_id
    }
    assert "故意杀害" not in liability_aliases
    assert "免责" in liability_aliases


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


def _shared_chunk_draft() -> GraphSnapshotDraft:
    """One batch where the model emitted the same node twice (two ids whose
    canonical keys align) and the same edge three times, all grounded in one chunk -- the
    shape that collided on the merged provenance primary keys."""

    snapshot = _snapshot("frag-dup")
    evidence = tuple(
        Evidence(
            evidence_id=evidence_id,
            snapshot_id="frag-dup",
            source_revision_id="frag-dup-source",
            document_revision_id="frag-dup-document",
            chunk_id="chunk-shared",
            anchor={"kind": "text", "start": start, "end": start + 4},
            content_digest=_digest(evidence_id),
        )
        for start, evidence_id in enumerate(("ev-n1", "ev-n2", "ev-n3", "ev-e1", "ev-e2", "ev-e3"))
    )
    nodes = (
        GraphNode("n1", "frag-dup", "效力中止", "ENTITY", "效力中止", ("ev-n1",)),
        GraphNode("n2", "frag-dup", "效力中止", "ENTITY", "效力中止。", ("ev-n2",)),
        GraphNode("n3", "frag-dup", "保单", "ENTITY", "保单", ("ev-n3",)),
    )
    edges = tuple(
        GraphEdge(
            edge_id=edge_id,
            snapshot_id="frag-dup",
            source_node_id="n3",
            target_node_id=target,
            relation_type="RELATED_TO",
            origin=RelationOrigin.EXTRACTED,
            confidence=0.8,
            evidence_ids=(f"ev-{edge_id}",),
            relation_label="适用",
        )
        for edge_id, target in (("e1", "n1"), ("e2", "n2"), ("e3", "n1"))
    )
    return GraphSnapshotDraft(snapshot, nodes, edges, evidence, ())


def test_merge_dedupes_provenance_on_its_primary_keys():
    records = [_record("frag-dup", _shared_chunk_draft())]

    draft = ProjectGraphMerger().merge(VALIDATION_SCOPE, records)

    def keys(rows, *names):
        return [tuple(getattr(row, name) for name in names) for row in rows]

    for rows, names in (
        (draft.nodes, ("node_id",)),
        (draft.edges, ("edge_id",)),
        (draft.node_sources, ("node_id", "source_revision_id", "chunk_id")),
        (draft.edge_evidence, ("edge_id", "source_revision_id", "chunk_id")),
        (draft.aliases, ("alias_norm", "node_id")),
    ):
        primary_keys = keys(rows, *names)
        assert len(primary_keys) == len(set(primary_keys)), names
    assert len(draft.edges) == 1
    assert len(draft.edge_evidence) == 1
    assert draft.edge_evidence[0].fragment_edge_id == "e1"
    suspended = next(node for node in draft.nodes if node.canonical_key == "效力中止")
    sources = [source for source in draft.node_sources if source.node_id == suspended.node_id]
    assert [source.fragment_node_id for source in sources] == ["n1"]


def test_merge_is_deterministic_across_reruns():
    records = [_record("frag-dup", _shared_chunk_draft())]

    assert ProjectGraphMerger().merge(VALIDATION_SCOPE, records) == ProjectGraphMerger().merge(
        VALIDATION_SCOPE, records
    )
