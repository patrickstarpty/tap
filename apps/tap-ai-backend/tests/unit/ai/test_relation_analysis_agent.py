"""Unit tests for `RelationAnalysisAgent` (PR 3 task 3): the deterministic
relation analysis pipeline run as a contract agent subgraph, with span
coverage and fail-closed status handling.

Reuses Task 2's `FakeProjectGraphStore`/fixture graph (nodes A/B/C/D/E; see
`tests.unit.knowledge.test_relation_analysis` for the fixture docstring),
extended here with `get_current`/`set_version` and an optional `neighbors`
failure hook.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

import pytest
from opentelemetry import context as otel_context

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.agents.protocol import AgentContext
from tap.modules.ai.application.agents.relation_analysis import RelationAnalysisAgent
from tap.modules.graph.domain.project import ProjectGraphVersion, ProjectNode
from tap.modules.knowledge.application.publication import (
    FlowchartPublicationGate,
    PublishedKnowledgeAuthority,
)
from tap.modules.knowledge.application.relation_analysis import (
    EvidenceRef,
    RelationAnalysisInput,
    RelationContextStatus,
)
from tap.modules.knowledge.domain.review import KnowledgePublication
from tap.platform.telemetry import span
from tests.unit.knowledge.test_relation_analysis import FakeProjectGraphStore, _build_store

_SCOPE = VALIDATION_SCOPE
_SPAN_NAMES = {"graph.seed", "graph.expand", "graph.path", "relation.rank"}
_NOW = datetime(2026, 9, 23, 9, tzinfo=UTC)


class FakeSnippets:
    def __init__(
        self,
        content: Mapping[str, str] | None = None,
        *,
        document_ids: Mapping[str, str] | None = None,
    ) -> None:
        self._content = dict(content or {})
        self._document_ids = dict(document_ids or {})

    async def snippets(self, refs: tuple[tuple[str, str], ...]) -> Mapping[str, str]:
        return {
            chunk_id: self._content[chunk_id]
            for _document_revision_id, chunk_id in refs
            if chunk_id in self._content
        }

    async def document_ids(self, revision_ids: tuple[str, ...]) -> Mapping[str, str]:
        return {
            revision_id: self._document_ids[revision_id]
            for revision_id in revision_ids
            if revision_id in self._document_ids
        }


def _fixture_kwargs(store: FakeProjectGraphStore | None = None) -> dict[str, Any]:
    base = store or _build_store()
    return {
        "nodes": tuple(base._nodes_by_id.values()),
        "edges": base._edges,
        "edge_evidence": base._edge_evidence,
        "node_chunks": base._node_chunks,
        "node_sources": base._node_sources,
    }


class VersionedStore(FakeProjectGraphStore):
    """Adds `get_current`/`set_version` and an optional `neighbors` failure
    hook to Task 2's fixture-only `FakeProjectGraphStore`."""

    def __init__(self, *, version: int = 7, status: str = "READY", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._version = version
        self._status: str | None = status
        self.fail_neighbors = False

    def set_version(self, version: int, status: str = "READY") -> None:
        self._version = version
        self._status = status

    async def get_current(self, scope: object) -> ProjectGraphVersion | None:
        if self._status is None:
            return None
        return ProjectGraphVersion(
            project_id=getattr(scope, "project_id", "project"),
            version=self._version,
            status=self._status,  # type: ignore[arg-type]
            fragment_digest="sha256:" + "a" * 64,
            node_count=0,
            edge_count=0,
            merged_at=None,
        )

    async def neighbors(self, *args: object, **kwargs: object) -> Any:
        if self.fail_neighbors:
            raise RuntimeError("boom")
        return await super().neighbors(*args, **kwargs)  # type: ignore[arg-type]


class BumpAfterFirstCallStore(VersionedStore):
    """`get_current` returns `version` on its first call and `version + 1`
    on every call after -- the agent's post-ranking recheck must catch a
    version bump that happened mid-run."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._calls = 0

    async def get_current(self, scope: object) -> ProjectGraphVersion | None:
        self._calls += 1
        bump = 0 if self._calls <= 1 else 1
        original = self._version
        self._version = original + bump
        try:
            return await super().get_current(scope)
        finally:
            self._version = original


class _PublicationRepository:
    def __init__(self, publication: KnowledgePublication | None) -> None:
        self._publication = publication

    async def current_publication(self) -> KnowledgePublication | None:
        return self._publication


def _publication(**changes: Any) -> KnowledgePublication:
    values: dict[str, Any] = {
        "publication_id": "publication-1",
        "project_id": _SCOPE.project_id,
        "review_id": "review-1",
        "review_version": 1,
        "approval_digest": "sha256:" + "a" * 64,
        "source_revision_ids": ("rev-1",),
        "approved_item_ids": ("item-allowed",),
        "generation": "gen-1",
        "published_by": "reviewer-1",
        "published_at": _NOW - timedelta(hours=1),
        "expires_at": _NOW + timedelta(hours=1),
    }
    values.update(changes)
    return KnowledgePublication(**values)


def _single_edge_store(*, anchor: Mapping[str, object]) -> VersionedStore:
    """One `x -> y` edge whose sole support chunk carries `anchor` -- used to
    drive `_publication_authority.authorize_evidence` down a specific
    accept/deny path without the five-node fixture graph's noise."""
    from tap.modules.graph.application.alias_index import AliasIndex
    from tap.modules.graph.domain.models import RelationOrigin
    from tap.modules.graph.domain.project import Alias, EdgeEvidence, ProjectEdge

    node_x = ProjectNode(node_id="x", label="节点 X", node_type="ENTITY", canonical_key="x")
    node_y = ProjectNode(node_id="y", label="节点 Y", node_type="ENTITY", canonical_key="y")
    edge = ProjectEdge(
        edge_id="e-1",
        source_node_id="x",
        target_node_id="y",
        relation_type="RELATED_TO",
        relation_label="RELATED_TO",
        origin=RelationOrigin.EXTRACTED,
        confidence=0.9,
    )
    edge_evidence = {
        "e-1": (
            EdgeEvidence(
                edge_id="e-1",
                source_revision_id="rev-1",
                document_revision_id="rev-1",
                chunk_id="chunk-1",
                anchor=anchor,
                content_digest="digest-1",
                fragment_snapshot_id="fragment-1",
                fragment_edge_id="fe-1",
            ),
        )
    }
    store = VersionedStore(
        version=7,
        nodes=(node_x, node_y),
        edges=(edge,),
        edge_evidence=edge_evidence,
        node_chunks={"chunk-x": ("x",)},
        node_sources={"x": ("rev-1",), "y": ("rev-1",)},
    )
    store._alias_index = AliasIndex.build((Alias(alias_norm="x", node_id="x", origin="LABEL"),))
    return store


def _input(**overrides: Any) -> RelationAnalysisInput:
    defaults: dict[str, Any] = {
        "query": "健康告知",
        "source_revision_ids": frozenset({"rev-1"}),
        "evidence": (),
        "graph_version": None,
    }
    defaults.update(overrides)
    return RelationAnalysisInput(**defaults)


def _context(**overrides: Any) -> AgentContext:
    defaults: dict[str, Any] = {
        "scope": _SCOPE,
        "source_revision_ids": frozenset({"rev-1"}),
        "graph_version": None,
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


@pytest.mark.asyncio
async def test_agent_applies_with_spans_under_parent(span_recorder: Any) -> None:
    store = VersionedStore(**_fixture_kwargs())
    agent = RelationAnalysisAgent(
        store, FakeSnippets({"chunk-2": "B 到 C 的片段", "chunk-3": "C 到 D 的片段"})
    )

    with span("turn.execute") as turn_span:
        outer_span_id = turn_span.get_span_context().span_id
        parent_context = otel_context.get_current()

    result = await agent.run(_context(parent_context=parent_context), _input())

    assert result.status is RelationContextStatus.APPLIED
    assert result.relations

    recorded = span_recorder.get_finished_spans()
    agent_spans = [item for item in recorded if item.name != "turn.execute"]
    assert {item.name for item in agent_spans} == _SPAN_NAMES
    assert all(item.name != "graph.enrich" for item in recorded)
    for item in agent_spans:
        assert item.parent is not None
        assert item.parent.span_id == outer_span_id


@pytest.mark.asyncio
async def test_agent_not_ready_without_current_version() -> None:
    store = VersionedStore(status=None, **_fixture_kwargs())
    agent = RelationAnalysisAgent(store, FakeSnippets())

    result = await agent.run(_context(), _input())

    assert result.status is RelationContextStatus.NOT_READY
    assert result.relations == ()


@pytest.mark.asyncio
async def test_version_change_between_seed_and_rank_is_stale() -> None:
    store = BumpAfterFirstCallStore(version=7, **_fixture_kwargs())
    agent = RelationAnalysisAgent(store, FakeSnippets())

    result = await agent.run(_context(), _input())

    assert result.status is RelationContextStatus.STALE
    assert result.relations == ()


@pytest.mark.asyncio
async def test_agent_empty_when_no_seed_matches() -> None:
    store = VersionedStore(version=7, **_fixture_kwargs())
    agent = RelationAnalysisAgent(store, FakeSnippets())

    result = await agent.run(_context(), _input(query="不存在的问题", evidence=()))

    assert result.status is RelationContextStatus.EMPTY
    assert result.graph_version == "7"
    assert result.relations == ()


@pytest.mark.asyncio
async def test_agent_failed_on_store_exception_without_raising() -> None:
    store = VersionedStore(version=7, **_fixture_kwargs())
    store.fail_neighbors = True
    agent = RelationAnalysisAgent(store, FakeSnippets())

    result = await agent.run(_context(), _input())

    assert result.status is RelationContextStatus.FAILED
    assert result.diagnostics["failure"] == 1
    assert result.relations == ()


@pytest.mark.asyncio
async def test_agent_caps_augment_chunks_at_five() -> None:
    from tap.modules.graph.application.alias_index import AliasIndex
    from tap.modules.graph.domain.models import RelationOrigin
    from tap.modules.graph.domain.project import Alias, EdgeEvidence, ProjectEdge, ProjectNode

    hub = ProjectNode(node_id="hub", label="节点 Hub", node_type="ENTITY", canonical_key="hub")
    leaves = tuple(
        ProjectNode(
            node_id=f"leaf-{index}",
            label=f"节点 Leaf{index}",
            node_type="ENTITY",
            canonical_key=f"leaf-{index}",
        )
        for index in range(8)
    )
    edges = tuple(
        ProjectEdge(
            edge_id=f"e-{index}",
            source_node_id="hub",
            target_node_id=f"leaf-{index}",
            relation_type="RELATED_TO",
            relation_label="RELATED_TO",
            origin=RelationOrigin.EXTRACTED,
            confidence=0.5,
        )
        for index in range(8)
    )
    edge_evidence = {
        f"e-{index}": (
            EdgeEvidence(
                edge_id=f"e-{index}",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                chunk_id=f"chunk-leaf-{index}",
                anchor={"page": 1},
                content_digest=f"digest-{index}",
                fragment_snapshot_id="fragment-1",
                fragment_edge_id=f"fe-{index}",
            ),
        )
        for index in range(8)
    }
    node_sources = {"hub": ("rev-1",), **{f"leaf-{index}": ("rev-1",) for index in range(8)}}

    store = VersionedStore(
        version=7,
        nodes=(hub, *leaves),
        edges=edges,
        edge_evidence=edge_evidence,
        node_chunks={"chunk-hub": ("hub",)},
        node_sources=node_sources,
    )
    store._alias_index = AliasIndex.build((Alias(alias_norm="hub", node_id="hub", origin="LABEL"),))
    agent = RelationAnalysisAgent(
        store,
        FakeSnippets({f"chunk-leaf-{index}": f"片段内容 {index}" for index in range(8)}),
    )

    result = await agent.run(_context(), _input(query="hub", evidence=()))

    assert result.status is RelationContextStatus.APPLIED
    assert len(result.augment_chunks) == 5


@pytest.mark.asyncio
async def test_agent_empty_when_only_relation_is_outside_the_current_publication() -> None:
    """Denial drops the relation whose only support failed authorization, not
    the whole context: with no other relation left, the outcome is EMPTY, not
    FAILED -- same as finding no relation evidence at all."""
    store = _single_edge_store(anchor={"inventoryItemId": "item-blocked"})
    authority = PublishedKnowledgeAuthority(
        _PublicationRepository(_publication()), now=lambda: _NOW
    )
    agent = RelationAnalysisAgent(
        store,
        FakeSnippets({"chunk-1": "x 到 y 的片段"}),
        publication_authority=authority,
    )

    result = await agent.run(_context(), _input(query="x", evidence=()))

    assert result.status is RelationContextStatus.EMPTY
    assert result.relations == ()


@pytest.mark.asyncio
async def test_agent_applies_when_support_is_authorized_by_the_current_publication() -> None:
    store = _single_edge_store(anchor={"inventoryItemId": "item-allowed"})
    authority = PublishedKnowledgeAuthority(
        _PublicationRepository(_publication()), now=lambda: _NOW
    )
    agent = RelationAnalysisAgent(
        store,
        FakeSnippets({"chunk-1": "x 到 y 的片段"}),
        publication_authority=authority,
    )

    result = await agent.run(_context(), _input(query="x", evidence=()))

    assert result.status is RelationContextStatus.APPLIED
    assert result.relations


@pytest.mark.asyncio
async def test_seed_outside_the_selection_is_excluded_from_seeds() -> None:
    """`seed_from_evidence` does not itself filter by selection (it trusts
    `value.evidence` to already be in-selection S labels), so this proves the
    agent's own `_visible_seed_nodes` -- which only keeps seeds that survived
    `expand`'s source-revision filter -- is what actually excludes a node
    whose only source lies outside `source_revision_ids`."""
    outside = ProjectNode(
        node_id="outside", label="节点 Outside", node_type="ENTITY", canonical_key="outside"
    )
    store = VersionedStore(
        version=7,
        nodes=(outside,),
        edges=(),
        edge_evidence={},
        node_chunks={"chunk-outside": ("outside",)},
        node_sources={"outside": ("rev-2",)},
    )
    agent = RelationAnalysisAgent(store, FakeSnippets())

    result = await agent.run(
        _context(),
        _input(
            query="不存在的问题",
            evidence=(
                EvidenceRef(
                    label="S1",
                    chunk_id="chunk-outside",
                    source_revision_id="rev-2",
                    document_revision_id="doc-outside",
                ),
            ),
        ),
    )

    assert result.seeds == ()


@pytest.mark.asyncio
async def test_agent_reserves_query_seed_slot_ahead_of_evidence_seeds_at_node_limit() -> None:
    """With one evidence chunk alone fanning out to 61 evidence-seed nodes --
    more than `expand()`'s default `node_limit=60` -- the query seed's own
    slot must still survive the node-limit cut, so its edge is still ranked
    (I1): combining seeds query-first, not evidence-first, is what protects
    it."""
    from tap.modules.graph.application.alias_index import AliasIndex
    from tap.modules.graph.domain.models import RelationOrigin
    from tap.modules.graph.domain.project import Alias, EdgeEvidence, ProjectEdge, ProjectNode

    query_node = ProjectNode(node_id="q", label="节点 Q", node_type="ENTITY", canonical_key="q")
    evidence_nodes = tuple(
        ProjectNode(
            node_id=f"e{index}",
            label=f"节点 E{index}",
            node_type="ENTITY",
            canonical_key=f"e{index}",
        )
        for index in range(1, 62)
    )
    edge = ProjectEdge(
        edge_id="e-q-e1",
        source_node_id="q",
        target_node_id="e1",
        relation_type="RELATED_TO",
        relation_label="RELATED_TO",
        origin=RelationOrigin.EXTRACTED,
        confidence=0.9,
    )
    edge_evidence = {
        "e-q-e1": (
            EdgeEvidence(
                edge_id="e-q-e1",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                chunk_id="chunk-edge",
                anchor={"page": 1},
                content_digest="digest-edge",
                fragment_snapshot_id="fragment-1",
                fragment_edge_id="fe-edge",
            ),
        )
    }
    node_chunks = {"chunk-ev": tuple(f"e{index}" for index in range(1, 62))}
    node_sources = {"q": ("rev-1",), **{f"e{index}": ("rev-1",) for index in range(1, 62)}}

    store = VersionedStore(
        version=7,
        nodes=(query_node, *evidence_nodes),
        edges=(edge,),
        edge_evidence=edge_evidence,
        node_chunks=node_chunks,
        node_sources=node_sources,
    )
    store._alias_index = AliasIndex.build((Alias(alias_norm="q", node_id="q", origin="LABEL"),))
    agent = RelationAnalysisAgent(store, FakeSnippets({"chunk-edge": "q 到 e1 的片段"}))

    result = await agent.run(
        _context(),
        _input(
            query="q",
            evidence=(
                EvidenceRef(
                    label="S1",
                    chunk_id="chunk-ev",
                    source_revision_id="rev-1",
                    document_revision_id="doc-1",
                ),
            ),
        ),
    )

    assert result.status is RelationContextStatus.APPLIED
    assert any(relation.edge_id == "e-q-e1" for relation in result.relations)


@pytest.mark.asyncio
async def test_agent_caps_evidence_refs_used_for_seeding_at_ten() -> None:
    """Only the first 10 evidence refs (S order) ever seed a node lookup --
    `seed_from_evidence` must not even be asked about chunks beyond that, so
    a `nodes_for_chunks` lookup for an 11th-and-later chunk never happens."""

    class _RecordingStore(VersionedStore):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.seen_chunk_ids: set[str] = set()

        async def nodes_for_chunks(self, scope: object, chunk_ids: tuple[str, ...], **kwargs: Any):
            self.seen_chunk_ids.update(chunk_ids)
            return await super().nodes_for_chunks(scope, chunk_ids, **kwargs)

    store = _RecordingStore(version=7, **_fixture_kwargs())
    agent = RelationAnalysisAgent(store, FakeSnippets())
    evidence = tuple(
        EvidenceRef(
            label=f"S{index}",
            chunk_id=f"chunk-{index}",
            source_revision_id="rev-1",
            document_revision_id="doc-1",
        )
        for index in range(1, 16)
    )

    await agent.run(_context(), _input(query="不存在的问题", evidence=evidence))

    assert store.seen_chunk_ids == {f"chunk-{index}" for index in range(1, 11)}


@pytest.mark.asyncio
async def test_agent_maps_project_graph_version_mismatch_to_stale() -> None:
    """A pinned version evicted from the store's retention window mid-run
    (`ProjectGraphVersionMismatch`) must be reported as STALE, not FAILED --
    the answer can be retried against the new current version instead of
    surfacing a bare failure."""
    from tap.modules.graph.ports.project_store import ProjectGraphVersionMismatch

    class _MismatchStore(VersionedStore):
        async def neighbors(self, *args: object, **kwargs: object) -> Any:
            raise ProjectGraphVersionMismatch(current=8)

    store = _MismatchStore(version=7, **_fixture_kwargs())
    agent = RelationAnalysisAgent(store, FakeSnippets())

    result = await agent.run(_context(), _input())

    assert result.status is RelationContextStatus.STALE


@pytest.mark.asyncio
async def test_agent_drops_paths_whose_edges_never_became_a_relation() -> None:
    """A->B->C->D is found as a path (A from evidence, D from the query), but
    C->D's only evidence chunk (chunk-3) has no snippet and is not an already
    cited S label, so `assemble` can never resolve its support and no R
    relation is produced for it. The path must then be dropped from
    `RelationContext.paths` entirely (I1 part 3) -- it would otherwise point
    at an edge the reader can never see cited."""
    store = VersionedStore(version=7, **_fixture_kwargs())
    agent = RelationAnalysisAgent(store, FakeSnippets({"chunk-2": "B 到 C 的片段"}))

    result = await agent.run(
        _context(),
        _input(
            query="健康告知",
            evidence=(
                EvidenceRef(
                    label="S1",
                    chunk_id="chunk-1",
                    source_revision_id="rev-1",
                    document_revision_id="doc-1",
                ),
            ),
        ),
    )

    assert result.status is RelationContextStatus.APPLIED
    assembled_edge_ids = {relation.edge_id for relation in result.relations}
    assert "e-cd" not in assembled_edge_ids
    assert result.paths == ()


@pytest.mark.asyncio
async def test_agent_drops_flowchart_image_support_that_is_not_approved() -> None:
    """Image-region evidence (anchor carries a `bbox`) is model-proposed and
    must answer only as an approved item of the current publication, same as
    retrieval's `_flowchart_hit_is_approved` -- R evidence must not bypass
    that review gate (I2)."""
    store = _single_edge_store(anchor={"bbox": [1, 2, 3, 4], "inventoryItemId": "item-blocked"})
    gate = FlowchartPublicationGate(
        PublishedKnowledgeAuthority(_PublicationRepository(_publication()), now=lambda: _NOW)
    )
    agent = RelationAnalysisAgent(
        store, FakeSnippets({"chunk-1": "x 到 y 的片段"}), flowchart_gate=gate
    )

    result = await agent.run(_context(), _input(query="x", evidence=()))

    assert result.status is RelationContextStatus.EMPTY
    assert result.relations == ()


@pytest.mark.asyncio
async def test_agent_keeps_flowchart_image_support_that_is_approved() -> None:
    store = _single_edge_store(anchor={"bbox": [1, 2, 3, 4], "inventoryItemId": "item-allowed"})
    gate = FlowchartPublicationGate(
        PublishedKnowledgeAuthority(_PublicationRepository(_publication()), now=lambda: _NOW)
    )
    agent = RelationAnalysisAgent(
        store, FakeSnippets({"chunk-1": "x 到 y 的片段"}), flowchart_gate=gate
    )

    result = await agent.run(_context(), _input(query="x", evidence=()))

    assert result.status is RelationContextStatus.APPLIED
    assert result.relations


@pytest.mark.asyncio
async def test_agent_keeps_a_path_edge_whose_intermediate_node_is_cut_by_node_limit() -> None:
    """Query seed q, 59 isolated
    evidence seeds (one evidence chunk fanning out to e1..e59), and a path
    q -> m -> e1 where m is *not* itself a seed. With 60 total seeds and the
    default `node_limit=60`, `expand()`'s own subgraph keeps every seed node
    but has no room left for m, so its own candidate_edges filter (both
    endpoints must be selected) drops both q-m and m-e1 entirely -- `m` only
    re-enters through `PathExpansion.nodes` (found by `find_paths`, merged
    into the agent's `nodes_by_id`). Without that merge, `assemble()` would
    drop both edges (their object/subject node would resolve to `None`), and
    the path would never be kept (its edges are never assembled)."""
    from tap.modules.graph.application.alias_index import AliasIndex
    from tap.modules.graph.domain.models import RelationOrigin
    from tap.modules.graph.domain.project import Alias, EdgeEvidence, ProjectEdge, ProjectNode

    query_node = ProjectNode(node_id="q", label="节点 Q", node_type="ENTITY", canonical_key="q")
    intermediate_node = ProjectNode(
        node_id="m", label="节点 M", node_type="ENTITY", canonical_key="m"
    )
    evidence_nodes = tuple(
        ProjectNode(
            node_id=f"e{index}",
            label=f"节点 E{index}",
            node_type="ENTITY",
            canonical_key=f"e{index}",
        )
        for index in range(1, 60)
    )
    edge_q_m = ProjectEdge(
        edge_id="e-q-m",
        source_node_id="q",
        target_node_id="m",
        relation_type="RELATED_TO",
        relation_label="RELATED_TO",
        origin=RelationOrigin.EXTRACTED,
        confidence=0.9,
    )
    edge_m_e1 = ProjectEdge(
        edge_id="e-m-e1",
        source_node_id="m",
        target_node_id="e1",
        relation_type="RELATED_TO",
        relation_label="RELATED_TO",
        origin=RelationOrigin.EXTRACTED,
        confidence=0.9,
    )
    edge_evidence = {
        "e-q-m": (
            EdgeEvidence(
                edge_id="e-q-m",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                chunk_id="chunk-qm",
                anchor={"page": 1},
                content_digest="digest-qm",
                fragment_snapshot_id="fragment-1",
                fragment_edge_id="fe-qm",
            ),
        ),
        "e-m-e1": (
            EdgeEvidence(
                edge_id="e-m-e1",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                chunk_id="chunk-me1",
                anchor={"page": 1},
                content_digest="digest-me1",
                fragment_snapshot_id="fragment-1",
                fragment_edge_id="fe-me1",
            ),
        ),
    }
    node_chunks = {"chunk-ev": tuple(f"e{index}" for index in range(1, 60))}
    node_sources = {
        "q": ("rev-1",),
        "m": ("rev-1",),
        **{f"e{index}": ("rev-1",) for index in range(1, 60)},
    }

    store = VersionedStore(
        version=7,
        nodes=(query_node, intermediate_node, *evidence_nodes),
        edges=(edge_q_m, edge_m_e1),
        edge_evidence=edge_evidence,
        node_chunks=node_chunks,
        node_sources=node_sources,
    )
    store._alias_index = AliasIndex.build((Alias(alias_norm="q", node_id="q", origin="LABEL"),))
    agent = RelationAnalysisAgent(
        store, FakeSnippets({"chunk-qm": "q 到 m 的片段", "chunk-me1": "m 到 e1 的片段"})
    )

    result = await agent.run(
        _context(),
        _input(
            query="q",
            evidence=(
                EvidenceRef(
                    label="S1",
                    chunk_id="chunk-ev",
                    source_revision_id="rev-1",
                    document_revision_id="doc-1",
                ),
            ),
        ),
    )

    assert result.status is RelationContextStatus.APPLIED
    assembled_edge_ids = {relation.edge_id for relation in result.relations}
    assert {"e-q-m", "e-m-e1"} <= assembled_edge_ids
    assert len(result.paths) == 1
    assert result.paths[0].node_ids == ("q", "m", "e1")


@pytest.mark.asyncio
async def test_agent_caps_non_s_snippet_refs_fetched_per_edge_at_two() -> None:
    """An edge with 5 fresh
    (never-cited) evidence chunks must only ever have 2 of them actually
    fetched for snippet text -- the cap must apply in `snippet_chunk_refs`
    itself (which builds the refs `ChunkSnippetReader.snippets` is called
    with), not only afterward in `_resolve_support`'s support-building."""
    from tap.modules.graph.application.alias_index import AliasIndex
    from tap.modules.graph.domain.models import RelationOrigin
    from tap.modules.graph.domain.project import Alias, EdgeEvidence, ProjectEdge, ProjectNode

    node_x = ProjectNode(node_id="x", label="节点 X", node_type="ENTITY", canonical_key="x")
    node_y = ProjectNode(node_id="y", label="节点 Y", node_type="ENTITY", canonical_key="y")
    edge = ProjectEdge(
        edge_id="e-1",
        source_node_id="x",
        target_node_id="y",
        relation_type="RELATED_TO",
        relation_label="RELATED_TO",
        origin=RelationOrigin.EXTRACTED,
        confidence=0.9,
    )
    edge_evidence = {
        "e-1": tuple(
            EdgeEvidence(
                edge_id="e-1",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                chunk_id=f"chunk-{index}",
                anchor={"page": 1},
                content_digest=f"digest-{index}",
                fragment_snapshot_id="fragment-1",
                fragment_edge_id=f"fe-{index}",
            )
            for index in range(5)
        )
    }
    store = VersionedStore(
        version=7,
        nodes=(node_x, node_y),
        edges=(edge,),
        edge_evidence=edge_evidence,
        node_chunks={"chunk-x": ("x",)},
        node_sources={"x": ("rev-1",), "y": ("rev-1",)},
    )
    store._alias_index = AliasIndex.build((Alias(alias_norm="x", node_id="x", origin="LABEL"),))

    class _RecordingSnippets(FakeSnippets):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.last_refs: tuple[tuple[str, str], ...] = ()

        async def snippets(self, refs: tuple[tuple[str, str], ...]) -> Mapping[str, str]:
            self.last_refs = refs
            return await super().snippets(refs)

    snippets = _RecordingSnippets({f"chunk-{index}": f"片段内容 {index}" for index in range(5)})
    agent = RelationAnalysisAgent(store, snippets)

    result = await agent.run(_context(), _input(query="x", evidence=()))

    assert result.status is RelationContextStatus.APPLIED
    assert len(snippets.last_refs) == 2
    assert len(result.relations[0].support) == 2


async def _fallback_only_project_graph() -> Any:
    """Two documents whose text matches no relation trigger, run through the
    fake extractor and the real merger: each publishes only its
    ``document:<revision>`` fallback node, carrying every chunk's evidence."""
    from tap.modules.graph.adapters.fake_extraction import rule_based_draft
    from tap.modules.graph.application.merger import ProjectGraphMerger
    from tap.modules.graph.application.project_queries import InMemoryProjectGraphStore
    from tap.modules.graph.domain.models import GraphSnapshot
    from tap.modules.graph.domain.project import FragmentRecord

    records = []
    for revision_id, content in (
        ("rev-a", "The waiting period is 90 days for every new policy."),
        ("rev-b", "Premiums are payable monthly by direct debit."),
    ):
        snapshot = GraphSnapshot.create(
            snapshot_id=f"snapshot-{revision_id}",
            project_id=_SCOPE.project_id,
            source_revision_ids=(revision_id,),
            document_revision_ids=(revision_id,),
        )
        chunk = {
            "chunkId": f"chunk-{revision_id}",
            "sourceRevisionId": revision_id,
            "documentRevisionId": revision_id,
            "anchor": {"kind": "text", "start": 0, "end": len(content)},
            "contentDigest": "sha256:" + "b" * 64,
            "content": content,
        }
        draft = rule_based_draft(snapshot, (chunk,), filename=f"{revision_id}.md")
        assert draft is not None
        records.append(
            FragmentRecord(
                snapshot_id=snapshot.snapshot_id,
                revision_id=revision_id,
                status="READY",
                content_digest="sha256:" + "c" * 64,
                draft=draft,
            )
        )
    store = InMemoryProjectGraphStore()
    project_draft = ProjectGraphMerger().merge(_SCOPE, records)
    assert {node.canonical_key for node in project_draft.nodes} == {
        "document:rev-a",
        "document:rev-b",
    }
    await store.publish(_SCOPE, project_draft, now=_NOW)
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "How long is the waiting period?",
        "What does rev-a.md say about premiums in rev-b.md?",
    ],
    ids=("ordinary-question", "question-naming-document-labels"),
)
async def test_document_fallback_nodes_never_seed_relation_analysis(query: str) -> None:
    store = await _fallback_only_project_graph()
    agent = RelationAnalysisAgent(store, FakeSnippets())
    sources = frozenset({"rev-a", "rev-b"})

    result = await agent.run(
        _context(source_revision_ids=sources),
        _input(
            query=query,
            source_revision_ids=sources,
            evidence=(
                EvidenceRef("S1", "chunk-rev-a", "rev-a", "rev-a"),
                EvidenceRef("S2", "chunk-rev-b", "rev-b", "rev-b"),
            ),
        ),
    )

    assert result.status is RelationContextStatus.EMPTY
    assert result.seeds == ()
    assert result.query_seed_count == 0
