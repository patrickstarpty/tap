"""Unit tests for `RelationAnalysisAgent` (PR 3 task 3): the deterministic
relation analysis pipeline run as a contract agent subgraph, with span
coverage and fail-closed status handling.

Reuses Task 2's `FakeProjectGraphStore`/fixture graph (nodes A/B/C/D/E; see
`tests.unit.knowledge.test_relation_analysis` for the fixture docstring),
extended here with `get_current`/`set_version` and an optional `neighbors`
failure hook.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest
from opentelemetry import context as otel_context

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.agents.protocol import AgentContext
from tap.modules.ai.application.agents.relation_analysis import RelationAnalysisAgent
from tap.modules.graph.domain.project import ProjectGraphVersion
from tap.modules.knowledge.application.relation_analysis import (
    RelationAnalysisInput,
    RelationContextStatus,
)
from tap.platform.telemetry import span
from tests.unit.knowledge.test_relation_analysis import FakeProjectGraphStore, _build_store

_SCOPE = VALIDATION_SCOPE
_SPAN_NAMES = {"graph.seed", "graph.expand", "graph.path", "relation.rank"}


class FakeSnippets:
    def __init__(self, content: Mapping[str, str] | None = None) -> None:
        self._content = dict(content or {})

    async def snippets(self, refs: tuple[tuple[str, str], ...]) -> Mapping[str, str]:
        return {
            chunk_id: self._content[chunk_id]
            for _document_revision_id, chunk_id in refs
            if chunk_id in self._content
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
