"""Bounded Project-scoped Knowledge Graph API: legacy per-snapshot fragment
routes (retained for this PR) and the project-level merged graph routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status

from tap.contracts.http import (
    GraphEdgeView,
    GraphEvidenceView,
    GraphFragmentRetryView,
    GraphNeighborRequest,
    GraphNodeView,
    GraphPathRequest,
    GraphSearchRequest,
    GraphSnapshotPage,
    GraphSnapshotView,
    GraphSubgraphView,
    ProjectGraphCommunityView,
    ProjectGraphEdgeView,
    ProjectGraphEvidenceView,
    ProjectGraphHighlightRequest,
    ProjectGraphNeighborRequest,
    ProjectGraphNodeDetailView,
    ProjectGraphNodeView,
    ProjectGraphRelationGroupView,
    ProjectGraphSourceGroupView,
    ProjectGraphSubgraphView,
    ProjectGraphView,
)
from tap.interfaces.http.dependencies import (
    GraphVersionMismatch,
    graph_jobs_service,
    graph_node_enrichment_service,
    graph_overview_limit,
    graph_service,
    project_graph_service,
)
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.models import (
    GraphSearchQuery,
    GraphSubgraph,
    NeighborQuery,
    PathQuery,
)
from tap.modules.graph.domain.project import (
    EdgeEvidence,
    NodeSource,
    ProjectEdge,
    ProjectNode,
    ProjectNodeDetail,
    ProjectSubgraph,
)
from tap.modules.graph.ports.project_store import ProjectGraphNotReady, ProjectGraphStorePort

router = APIRouter(prefix="/knowledge/graph", tags=["knowledge-graph"])

_EMPTY_PROJECT_SUBGRAPH = ProjectGraphSubgraphView(graph_version=0, nodes=[], edges=[])


@router.get(
    "/snapshots",
    operation_id="graph_list_active_snapshots",
    response_model=GraphSnapshotPage,
    dependencies=[Depends(project_authorization("knowledge.read"))],
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: problem_response_metadata("Graph unavailable")},
)
async def list_active_snapshots(
    request: Request,
    source_revision_ids: list[str] = Query(alias="sourceRevisionId", min_length=1, max_length=50),
) -> GraphSnapshotPage:
    snapshot = await graph_service(request).active_snapshot(
        request.state.project_scope, tuple(source_revision_ids)
    )
    if snapshot is None:
        return GraphSnapshotPage(items=[])
    return GraphSnapshotPage(
        items=[
            GraphSnapshotView(
                snapshot_id=snapshot.snapshot_id,
                source_set_digest=snapshot.source_set_digest,
                source_revision_ids=list(snapshot.source_revision_ids),
                document_revision_ids=list(snapshot.document_revision_ids),
                status=snapshot.status,
            )
        ]
    )


@router.get(
    "/project",
    operation_id="graph_get_project",
    response_model=ProjectGraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def get_project_graph(request: Request) -> ProjectGraphView:
    scope = request.state.project_scope
    store = project_graph_service(request)
    jobs = graph_jobs_service(request)
    now = datetime.now(UTC)
    fragment_states = await jobs.list_fragment_states(scope)
    extracting = sorted(
        {
            revision
            for revision, job_status, _ in fragment_states
            if job_status in ("PENDING", "RUNNING")
        }
    )
    partial = sorted(
        {
            revision
            for revision, _, snapshot_status in fragment_states
            if snapshot_status == "PARTIAL"
        }
    )
    current = await store.get_current(scope)
    if current is not None:
        communities = await store.communities(scope)
        return ProjectGraphView(
            graph_version=current.version,
            status="READY",
            node_count=current.node_count,
            edge_count=current.edge_count,
            communities=[_community_view(community) for community in communities],
            merged_at=current.merged_at,
            extracting_revision_ids=list(extracting),
            partial_revision_ids=list(partial),
        )
    merge_state = await jobs.merge_state(scope, now=now)
    project_status: Literal["EMPTY", "MERGING", "FAILED"]
    if merge_state in ("PENDING", "RUNNING"):
        project_status = "MERGING"
    elif merge_state == "FAILED":
        project_status = "FAILED"
    else:
        project_status = "EMPTY"
    return ProjectGraphView(
        graph_version=None,
        status=project_status,
        node_count=0,
        edge_count=0,
        communities=[],
        merged_at=None,
        extracting_revision_ids=list(extracting),
        partial_revision_ids=list(partial),
    )


@router.get(
    "/overview",
    operation_id="graph_get_overview",
    response_model=ProjectGraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def get_overview(
    request: Request,
    source_revision_ids: list[str] = Query(default=[], alias="sourceRevisionId", max_length=50),
    community_ids: list[str] = Query(default=[], alias="communityId", max_length=50),
    node_limit: int | None = Query(default=None, alias="nodeLimit", ge=1, le=500),
    graph_version: int | None = Query(default=None, alias="graphVersion"),
) -> ProjectGraphSubgraphView:
    store = project_graph_service(request)
    scope = request.state.project_scope
    current_version = await _require_current_version(store, scope, graph_version)
    limit = node_limit if node_limit is not None else graph_overview_limit(request)
    try:
        subgraph = await store.overview(
            scope,
            source_revision_ids=tuple(source_revision_ids),
            community_ids=tuple(community_ids),
            node_limit=limit,
            version=current_version,
        )
    except ProjectGraphNotReady:
        return _EMPTY_PROJECT_SUBGRAPH
    return _project_subgraph(subgraph)


@router.post(
    "/query",
    operation_id="graph_search",
    response_model=ProjectGraphSubgraphView | GraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def search_graph(
    request: Request, body: GraphSearchRequest
) -> ProjectGraphSubgraphView | GraphSubgraphView:
    scope = request.state.project_scope
    if body.snapshot_id is not None:
        graph = await graph_service(request).search(
            scope, GraphSearchQuery(body.snapshot_id, body.query, body.node_limit)
        )
        return _subgraph(graph)
    store = project_graph_service(request)
    current_version = await _require_current_version(store, scope, body.graph_version)
    try:
        subgraph = await store.search(
            scope,
            body.query,
            source_revision_ids=tuple(body.source_revision_ids),
            node_limit=body.node_limit,
            version=current_version,
        )
    except ProjectGraphNotReady:
        return _EMPTY_PROJECT_SUBGRAPH
    return _project_subgraph(subgraph)


@router.post(
    "/neighbors",
    operation_id="graph_project_neighbors",
    response_model=ProjectGraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def project_neighbors(
    request: Request, body: ProjectGraphNeighborRequest
) -> ProjectGraphSubgraphView:
    store = project_graph_service(request)
    scope = request.state.project_scope
    current_version = await _require_current_version(store, scope, body.graph_version)
    try:
        subgraph = await store.neighbors(
            scope,
            body.node_id,
            depth=body.depth,
            node_limit=body.node_limit,
            source_revision_ids=tuple(body.source_revision_ids),
            version=current_version,
        )
    except ProjectGraphNotReady:
        return _EMPTY_PROJECT_SUBGRAPH
    return _project_subgraph(subgraph)


@router.post(
    "/path",
    operation_id="graph_bounded_path",
    response_model=ProjectGraphSubgraphView | GraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def bounded_path(
    request: Request, body: GraphPathRequest
) -> ProjectGraphSubgraphView | GraphSubgraphView:
    scope = request.state.project_scope
    if body.snapshot_id is not None:
        graph = await graph_service(request).bounded_path(
            scope,
            PathQuery(body.snapshot_id, body.source_node_id, body.target_node_id, body.node_limit),
        )
        return _subgraph(graph)
    store = project_graph_service(request)
    current_version = await _require_current_version(store, scope, body.graph_version)
    try:
        subgraph = await store.path(
            scope,
            body.source_node_id,
            body.target_node_id,
            max_hops=body.max_hops,
            source_revision_ids=tuple(body.source_revision_ids),
            version=current_version,
        )
    except ProjectGraphNotReady:
        return _EMPTY_PROJECT_SUBGRAPH
    return _project_subgraph(subgraph)


@router.get(
    "/nodes/{node_id}",
    operation_id="graph_get_node",
    response_model=ProjectGraphNodeDetailView | GraphSubgraphView | ProjectGraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def node_detail(
    request: Request,
    node_id: str,
    snapshot_id: str | None = Query(default=None, alias="snapshotId"),
    graph_version: int | None = Query(default=None, alias="graphVersion"),
) -> ProjectGraphNodeDetailView | GraphSubgraphView | ProjectGraphSubgraphView:
    scope = request.state.project_scope
    if snapshot_id is not None:
        graph = await graph_service(request).node_detail(scope, snapshot_id, node_id)
        return _subgraph(graph)
    store = project_graph_service(request)
    current_version = await _require_current_version(store, scope, graph_version)
    try:
        detail = await store.node_detail(scope, node_id, version=current_version)
    except ProjectGraphNotReady:
        return _EMPTY_PROJECT_SUBGRAPH
    return await _project_node_detail(request, detail)


@router.post(
    "/highlight",
    operation_id="graph_highlight",
    response_model=ProjectGraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def highlight(
    request: Request, body: ProjectGraphHighlightRequest
) -> ProjectGraphSubgraphView:
    store = project_graph_service(request)
    scope = request.state.project_scope
    current_version = await _require_current_version(store, scope, body.graph_version)
    try:
        subgraph = await store.highlight(scope, tuple(body.edge_ids), version=current_version)
    except ProjectGraphNotReady:
        return _EMPTY_PROJECT_SUBGRAPH
    return _project_subgraph(subgraph)


@router.post(
    "/fragments/{revision_id}/retry",
    operation_id="graph_retry_fragment",
    response_model=GraphFragmentRetryView,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def retry_fragment(request: Request, revision_id: str) -> GraphFragmentRetryView:
    scope = request.state.project_scope
    jobs = graph_jobs_service(request)
    now = datetime.now(UTC)
    requeued, job_status = await jobs.retry_failed_batches(scope, revision_id, now=now)
    return GraphFragmentRetryView(
        revision_id=revision_id,
        requeued_batches=requeued,
        job_status=job_status.value,
    )


@router.get(
    "/evidence/{evidence_id}",
    operation_id="graph_get_evidence",
    response_model=GraphEvidenceView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def evidence_detail(
    request: Request, evidence_id: str, snapshot_id: str = Query(alias="snapshotId")
) -> GraphEvidenceView:
    item = await graph_service(request).evidence(
        request.state.project_scope, snapshot_id, evidence_id
    )
    return _evidence(item)


@router.post(
    "/nodes/{node_id}/neighbors",
    operation_id="graph_get_neighbors",
    response_model=GraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def neighbors(
    request: Request, node_id: str, body: GraphNeighborRequest
) -> GraphSubgraphView:
    graph = await graph_service(request).neighbors(
        request.state.project_scope,
        NeighborQuery(body.snapshot_id, node_id, body.depth, body.node_limit),
    )
    return _subgraph(graph)


async def _require_current_version(
    store: ProjectGraphStorePort, scope: ProjectScopeContext, requested: int | None
) -> int | None:
    """The public contract requires an exact match against the Project's
    current READY version -- stricter than `ProjectGraphStorePort`'s own
    retention-window check, which still serves an older cached version."""

    current = await store.get_current(scope)
    current_version = current.version if current is not None else None
    if requested is not None and requested != current_version:
        raise GraphVersionMismatch(current_version)
    return current_version


def _community_view(community: object) -> ProjectGraphCommunityView:
    return ProjectGraphCommunityView(
        community_id=community.community_id,  # type: ignore[attr-defined]
        label=community.label,  # type: ignore[attr-defined]
        size=community.size,  # type: ignore[attr-defined]
    )


def _project_node_view(node: ProjectNode) -> ProjectGraphNodeView:
    return ProjectGraphNodeView(
        node_id=node.node_id,
        label=node.label,
        node_type=node.node_type,
        canonical_key=node.canonical_key,
        degree=node.degree,
        community_id=node.community_id,
        aliases=list(node.aliases),
    )


def _project_edge_view(edge: ProjectEdge) -> ProjectGraphEdgeView:
    return ProjectGraphEdgeView(
        edge_id=edge.edge_id,
        source_node_id=edge.source_node_id,
        target_node_id=edge.target_node_id,
        relation_type=edge.relation_type,
        relation_label=edge.relation_label,
        origin=edge.origin.value,
        confidence=edge.confidence,
    )


def _node_source_evidence(
    source: NodeSource, *, snippet: str | None = None
) -> ProjectGraphEvidenceView:
    return ProjectGraphEvidenceView(
        owner_kind="node",
        owner_id=source.node_id,
        source_revision_id=source.source_revision_id,
        document_revision_id=source.document_revision_id,
        chunk_id=source.chunk_id,
        anchor=dict(source.anchor),
        content_digest=None,
        snippet=snippet,
    )


def _edge_evidence_view(evidence: EdgeEvidence) -> ProjectGraphEvidenceView:
    return ProjectGraphEvidenceView(
        owner_kind="edge",
        owner_id=evidence.edge_id,
        source_revision_id=evidence.source_revision_id,
        document_revision_id=evidence.document_revision_id,
        chunk_id=evidence.chunk_id,
        anchor=dict(evidence.anchor),
        content_digest=evidence.content_digest,
    )


def _project_subgraph(subgraph: ProjectSubgraph) -> ProjectGraphSubgraphView:
    return ProjectGraphSubgraphView(
        graph_version=subgraph.version,
        nodes=[_project_node_view(node) for node in subgraph.nodes],
        edges=[_project_edge_view(edge) for edge in subgraph.edges],
        evidence=(
            [_node_source_evidence(source) for source in subgraph.sources]
            + [_edge_evidence_view(evidence) for evidence in subgraph.evidence]
        ),
    )


async def _project_node_detail(
    request: Request, detail: ProjectNodeDetail
) -> ProjectGraphNodeDetailView:
    community = _community_view(detail.community) if detail.community is not None else None
    enrichment = graph_node_enrichment_service(request)
    scope = request.state.project_scope

    source_order: list[tuple[str, str]] = []
    sources_by_key: dict[tuple[str, str], list[NodeSource]] = {}
    for source in detail.sources:
        key = (source.source_revision_id, source.document_revision_id)
        if key not in sources_by_key:
            sources_by_key[key] = []
            source_order.append(key)
        sources_by_key[key].append(source)

    # The node's own evidence snippet is the only place the public contract
    # fills `snippet`; every other response (including this one's sibling
    # edge evidence) leaves it unset per spec. `source_name` and `snippet`
    # are both best-effort -- `None` when no enrichment provider is wired or
    # nothing resolves -- and cached per (source_revision_id, chunk_id) so a
    # node with many sources from the same revision does not repeat lookups.
    source_names: dict[str, str | None] = {}
    snippets: dict[tuple[str, str], str | None] = {}

    async def _resolve_source_name(source_revision_id: str) -> str | None:
        if source_revision_id not in source_names:
            source_names[source_revision_id] = (
                None
                if enrichment is None
                else await enrichment.source_name(scope, source_revision_id)
            )
        return source_names[source_revision_id]

    async def _resolve_snippet(source_revision_id: str, chunk_id: str) -> str | None:
        snippet_key = (source_revision_id, chunk_id)
        if snippet_key not in snippets:
            snippets[snippet_key] = (
                None
                if enrichment is None
                else await enrichment.snippet(scope, source_revision_id, chunk_id)
            )
        return snippets[snippet_key]

    sources = []
    for key in source_order:
        source_revision_id, document_revision_id = key
        name = await _resolve_source_name(source_revision_id)
        evidence = [
            _node_source_evidence(
                source, snippet=await _resolve_snippet(source_revision_id, source.chunk_id)
            )
            for source in sources_by_key[key]
        ]
        sources.append(
            ProjectGraphSourceGroupView(
                source_revision_id=source_revision_id,
                document_revision_id=document_revision_id,
                source_name=name,
                evidence=evidence,
            )
        )

    relation_order: list[str] = []
    edges_by_relation: dict[str, list[ProjectEdge]] = {}
    for edge in detail.edges:
        if edge.relation_type not in edges_by_relation:
            edges_by_relation[edge.relation_type] = []
            relation_order.append(edge.relation_type)
        edges_by_relation[edge.relation_type].append(edge)
    relations = [
        ProjectGraphRelationGroupView(
            relation_type=relation_type,
            edges=[_project_edge_view(edge) for edge in edges_by_relation[relation_type]],
        )
        for relation_type in relation_order
    ]

    return ProjectGraphNodeDetailView(
        graph_version=detail.version,
        node=_project_node_view(detail.node),
        community=community,
        sources=sources,
        relations=relations,
        neighbors=[_project_node_view(neighbor) for neighbor in detail.neighbors],
    )


def _subgraph(graph: GraphSubgraph) -> GraphSubgraphView:
    return GraphSubgraphView(
        snapshot_id=graph.snapshot_id,
        nodes=[
            GraphNodeView(
                node_id=node.node_id,
                label=node.label,
                node_type=node.node_type,
                canonical_key=node.canonical_key,
                evidence_ids=list(node.evidence_ids),
            )
            for node in graph.nodes
        ],
        edges=[
            GraphEdgeView(
                edge_id=edge.edge_id,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                relation_type=edge.relation_type,
                origin=edge.origin.value,
                confidence=edge.confidence,
                evidence_ids=list(edge.evidence_ids),
            )
            for edge in graph.edges
        ],
        evidence=[_evidence(item) for item in graph.evidence],
    )


def _evidence(item) -> GraphEvidenceView:
    return GraphEvidenceView(
        evidence_id=item.evidence_id,
        source_revision_id=item.source_revision_id,
        document_revision_id=item.document_revision_id,
        chunk_id=item.chunk_id,
        anchor=dict(item.anchor),
        content_digest=item.content_digest,
    )
