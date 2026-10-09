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
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
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
async def test_agent_fails_when_support_is_outside_the_current_publication() -> None:
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

    assert result.status is RelationContextStatus.FAILED
    assert result.diagnostics["failure"] == 1
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
