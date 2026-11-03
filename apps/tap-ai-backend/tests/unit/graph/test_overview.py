import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.cache import CachedSnapshotIndexes
from tap.modules.graph.application.overview import (
    GraphOverviewService,
    OverviewMember,
    PublishedSource,
    PublishedSourceListing,
)
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)


def _draft(
    snapshot_id: str,
    revision: str,
    nodes: tuple[tuple[str, str, str, str], ...],
    edges: tuple[tuple[str, str, str, str], ...],
) -> GraphSnapshotDraft:
    evidence = Evidence(
        f"{snapshot_id}-evidence",
        snapshot_id,
        revision,
        revision,
        "chunk-1",
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "a" * 64,
    )
    return GraphSnapshotDraft(
        GraphSnapshot.create(
            snapshot_id=snapshot_id,
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=(revision,),
            document_revision_ids=(revision,),
        ),
        tuple(
            GraphNode(node_id, snapshot_id, label, node_type, key)
            for node_id, label, node_type, key in nodes
        ),
        tuple(
            GraphEdge(
                edge_id,
                snapshot_id,
                source,
                target,
                relation,
                RelationOrigin.EXTRACTED,
                1.0,
                (evidence.evidence_id,),
            )
            for edge_id, source, target, relation in edges
        ),
        (evidence,),
        (),
    )


class Sources:
    def __init__(self, *items: PublishedSource, truncated: bool = False) -> None:
        self.items = items
        self.truncated = truncated
        self.calls = 0

    async def published_source_revisions(self, scope):
        self.calls += 1
        return PublishedSourceListing(self.items, truncated=self.truncated)


async def _store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    await store.publish(
        VALIDATION_SCOPE,
        _draft(
            "snapshot-a",
            "revision-a",
            (
                ("a-login", "Login Service", "ENTITY", "login-service"),
                ("a-auth", "Auth", "SYSTEM", "auth"),
                ("a-user", "User", "ACTOR", "user"),
            ),
            (
                ("a-e1", "a-login", "a-auth", "DEPENDS_ON"),
                ("a-e2", "a-user", "a-login", "USES"),
            ),
        ),
    )
    await store.publish(
        VALIDATION_SCOPE,
        _draft(
            "snapshot-b",
            "revision-b",
            (
                ("b-login", "login service", "ENTITY", "login-service"),
                ("b-auth", "Auth", "SYSTEM", "auth"),
                ("b-audit", "Audit", "CONCEPT", "audit"),
            ),
            (
                ("b-e1", "b-login", "b-auth", "DEPENDS_ON"),
                ("b-e2", "b-audit", "b-auth", "OBSERVES"),
            ),
        ),
    )
    await store.publish(
        VALIDATION_SCOPE,
        _draft(
            "snapshot-c",
            "revision-c",
            (("c-login", "Login Service", "ENTITY", "login-service"),),
            (),
        ),
    )
    return store


SOURCES = (
    PublishedSource("source-a", "revision-a"),
    PublishedSource("source-b", "revision-b"),
    PublishedSource("source-c", "revision-c"),
)


@pytest.mark.asyncio
async def test_overview_merges_nodes_and_edges_across_published_sources():
    service = GraphOverviewService(await _store(), Sources(*SOURCES), CachedSnapshotIndexes())

    overview = await service.overview(VALIDATION_SCOPE)

    assert overview.snapshot_ids == ("snapshot-a", "snapshot-b", "snapshot-c")
    assert overview.source_count == 3
    login = next(node for node in overview.nodes if node.node_type == "ENTITY")
    assert login.overview_node_id.startswith("overview:")
    assert login.label == "Login Service"
    assert login.members == (
        OverviewMember("snapshot-a", "a-login", "source-a"),
        OverviewMember("snapshot-b", "b-login", "source-b"),
        OverviewMember("snapshot-c", "c-login", "source-c"),
    )
    depends = [edge for edge in overview.edges if edge.relation_type == "DEPENDS_ON"]
    assert len(depends) == 1 and depends[0].support == 2
    assert depends[0].source == login.overview_node_id
    assert len(overview.edges) == 3
    assert dict(overview.node_type_counts) == {
        "ACTOR": 1,
        "CONCEPT": 1,
        "ENTITY": 1,
        "SYSTEM": 1,
    }


@pytest.mark.asyncio
async def test_overview_orders_by_merged_degree_then_label_then_id():
    service = GraphOverviewService(await _store(), Sources(*SOURCES), CachedSnapshotIndexes())

    overview = await service.overview(VALIDATION_SCOPE)

    assert [(node.label, node.degree) for node in overview.nodes] == [
        ("Auth", 2),
        ("Login Service", 2),
        ("Audit", 1),
        ("User", 1),
    ]


@pytest.mark.asyncio
async def test_overview_label_ties_resolve_to_the_smallest_label():
    store = await _store()
    sources = Sources(SOURCES[0], SOURCES[1])
    overview = await GraphOverviewService(store, sources, CachedSnapshotIndexes()).overview(
        VALIDATION_SCOPE
    )

    login = next(node for node in overview.nodes if node.node_type == "ENTITY")
    assert login.label == "Login Service"


@pytest.mark.asyncio
async def test_overview_node_limit_keeps_only_edges_between_kept_nodes():
    service = GraphOverviewService(await _store(), Sources(*SOURCES), CachedSnapshotIndexes())

    overview = await service.overview(VALIDATION_SCOPE, node_limit=2)

    kept = {node.overview_node_id for node in overview.nodes}
    assert [node.label for node in overview.nodes] == ["Auth", "Login Service"]
    assert [edge.relation_type for edge in overview.edges] == ["DEPENDS_ON"]
    assert all(edge.source in kept and edge.target in kept for edge in overview.edges)


@pytest.mark.asyncio
async def test_overview_without_published_sources_is_empty():
    service = GraphOverviewService(await _store(), Sources(), CachedSnapshotIndexes())

    overview = await service.overview(VALIDATION_SCOPE)

    assert overview.nodes == () and overview.edges == ()
    assert overview.source_count == 0 and overview.snapshot_ids == ()
    assert dict(overview.node_type_counts) == {}


@pytest.mark.asyncio
async def test_overview_skips_sources_without_an_active_snapshot():
    sources = Sources(SOURCES[0], PublishedSource("source-x", "revision-x"))
    service = GraphOverviewService(await _store(), sources, CachedSnapshotIndexes())

    overview = await service.overview(VALIDATION_SCOPE)

    assert overview.snapshot_ids == ("snapshot-a",)
    assert overview.source_count == 1


@pytest.mark.asyncio
async def test_overview_result_is_cached_by_snapshot_set_and_limit():
    indexes = CachedSnapshotIndexes()
    sources = Sources(*SOURCES)
    service = GraphOverviewService(await _store(), sources, indexes)

    first = await service.overview(VALIDATION_SCOPE)
    loads = indexes.stats()
    second = await service.overview(VALIDATION_SCOPE)

    assert second is first
    assert indexes.stats() == loads
    assert sources.calls == 2
    other = await service.overview(VALIDATION_SCOPE, node_limit=2)
    assert other is not first


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 301])
async def test_overview_rejects_out_of_range_node_limits(limit):
    service = GraphOverviewService(InMemoryGraphStore(), Sources(), CachedSnapshotIndexes())
    with pytest.raises(ValueError):
        await service.overview(VALIDATION_SCOPE, node_limit=limit)


@pytest.mark.asyncio
async def test_overview_reports_truncated_source_listing():
    service = GraphOverviewService(
        await _store(), Sources(*SOURCES, truncated=True), CachedSnapshotIndexes()
    )
    assert (await service.overview(VALIDATION_SCOPE)).truncated is True
    untruncated = GraphOverviewService(await _store(), Sources(*SOURCES), CachedSnapshotIndexes())
    assert (await untruncated.overview(VALIDATION_SCOPE)).truncated is False


@pytest.mark.asyncio
async def test_overview_bounds_concurrent_active_snapshot_lookups():
    import asyncio

    store = await _store()

    class CountingStore:
        def __init__(self) -> None:
            self.in_flight = 0
            self.peak = 0

        async def active_snapshot(self, scope, source_ids):
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.in_flight -= 1
            return await store.active_snapshot(scope, source_ids)

        async def load_index(self, scope, snapshot):
            return await store.load_index(scope, snapshot)

    counting = CountingStore()
    many = tuple(PublishedSource(f"source-{i}", f"revision-{i}") for i in range(40))
    service = GraphOverviewService(counting, Sources(*SOURCES, *many), CachedSnapshotIndexes())
    overview = await service.overview(VALIDATION_SCOPE)
    assert overview.source_count == len(SOURCES)
    assert 1 < counting.peak <= 8


def _ready_revisions(count: int, *, flowchart: int | None = None):
    from tap.modules.knowledge.adapters.mysql_ready_sources import ReadySourceRevision

    return tuple(
        ReadySourceRevision(
            f"source-{i}", f"revision-{i}", "flow.png" if i == flowchart else f"doc-{i}.md"
        )
        for i in range(count)
    )


@pytest.mark.asyncio
async def test_published_knowledge_sources_include_more_than_one_hundred_sources():
    from tap.modules.knowledge.application.graph_sources import (
        OVERVIEW_SOURCE_CAP,
        PublishedKnowledgeGraphSources,
    )

    limits: list[int] = []

    async def list_revisions(*, limit: int):
        limits.append(limit)
        return _ready_revisions(min(limit, 150))

    listing = await PublishedKnowledgeGraphSources(
        list_revisions, project_id=VALIDATION_SCOPE.project_id
    ).published_source_revisions(VALIDATION_SCOPE)
    assert OVERVIEW_SOURCE_CAP == 2000
    assert limits == [OVERVIEW_SOURCE_CAP + 1]
    assert len(listing.sources) == 150
    assert listing.truncated is False


@pytest.mark.asyncio
async def test_published_knowledge_sources_flag_truncation_and_gate_flowcharts(caplog):
    from types import SimpleNamespace

    from tap.modules.knowledge.application.graph_sources import PublishedKnowledgeGraphSources

    async def list_revisions(*, limit: int):
        return _ready_revisions(min(limit, 10), flowchart=1)

    class Gate:
        async def current(self, project_id):
            return SimpleNamespace(source_revision_ids=("revision-9",))

    with caplog.at_level("WARNING"):
        listing = await PublishedKnowledgeGraphSources(
            list_revisions, project_id=VALIDATION_SCOPE.project_id, cap=3, gate=Gate()
        ).published_source_revisions(VALIDATION_SCOPE)
    assert listing.truncated is True
    assert listing.sources == (
        PublishedSource("source-0", "revision-0"),
        PublishedSource("source-2", "revision-2"),
    )
    assert "truncated" in caplog.text


@pytest.mark.asyncio
async def test_published_knowledge_sources_skip_denied_and_foreign_projects():
    from types import SimpleNamespace

    from tap.modules.access.domain.policy import AuthorizationDenied
    from tap.modules.knowledge.application.graph_sources import PublishedKnowledgeGraphSources

    async def list_sources(*, limit: int):
        return [
            SimpleNamespace(source_id="source-a", revision_id="revision-a", filename="a.md"),
            SimpleNamespace(source_id="source-a", revision_id="revision-a", filename="a.md"),
            SimpleNamespace(source_id="source-b", revision_id="revision-b", filename="b.md"),
        ]

    class Authority:
        async def authorize_selection(self, project_id, revisions):
            if revisions == ("revision-b",):
                raise AuthorizationDenied("withdrawn")

    sources = PublishedKnowledgeGraphSources(
        list_sources, project_id=VALIDATION_SCOPE.project_id, authority=Authority()
    )
    assert (await sources.published_source_revisions(VALIDATION_SCOPE)).sources == (
        PublishedSource("source-a", "revision-a"),
    )
    foreign = PublishedKnowledgeGraphSources(list_sources, project_id="other-project")
    assert (await foreign.published_source_revisions(VALIDATION_SCOPE)).sources == ()
