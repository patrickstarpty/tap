"""Bounded Project-scoped Knowledge Graph API."""

from fastapi import APIRouter, Depends, Query, Request, status

from tap.contracts.http import (
    GraphEdgeView,
    GraphEvidenceView,
    GraphNeighborRequest,
    GraphNodeView,
    GraphOverviewEdgeView,
    GraphOverviewMemberView,
    GraphOverviewNodeView,
    GraphOverviewRequest,
    GraphOverviewStatsView,
    GraphOverviewView,
    GraphPathRequest,
    GraphSearchRequest,
    GraphSnapshotPage,
    GraphSnapshotView,
    GraphSubgraphRequest,
    GraphSubgraphView,
)
from tap.interfaces.http.dependencies import graph_overview_service, graph_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.graph.domain.models import (
    GraphFactNotFound,
    GraphSearchQuery,
    GraphSubgraph,
    NeighborQuery,
    PathQuery,
)

router = APIRouter(prefix="/knowledge/graph", tags=["knowledge-graph"])


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


@router.post(
    "/query",
    operation_id="graph_search",
    response_model=GraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def search_graph(request: Request, body: GraphSearchRequest) -> GraphSubgraphView:
    graph = await graph_service(request).search(
        request.state.project_scope,
        GraphSearchQuery(body.snapshot_id, body.query, body.node_limit),
    )
    return _subgraph(graph)


@router.get(
    "/nodes/{node_id}",
    operation_id="graph_get_node",
    response_model=GraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def node_detail(
    request: Request, node_id: str, snapshot_id: str = Query(alias="snapshotId")
) -> GraphSubgraphView:
    graph = await graph_service(request).node_detail(
        request.state.project_scope, snapshot_id, node_id
    )
    return _subgraph(graph)


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


@router.post(
    "/path",
    operation_id="graph_bounded_path",
    response_model=GraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def bounded_path(request: Request, body: GraphPathRequest) -> GraphSubgraphView:
    graph = await graph_service(request).bounded_path(
        request.state.project_scope,
        PathQuery(body.snapshot_id, body.source_node_id, body.target_node_id, body.node_limit),
    )
    return _subgraph(graph)


@router.post(
    "/subgraph",
    operation_id="graph_get_subgraph",
    response_model=GraphSubgraphView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def highlighted_subgraph(request: Request, body: GraphSubgraphRequest) -> GraphSubgraphView:
    store = graph_service(request)
    scope = request.state.project_scope
    snapshot = await store.get_snapshot(scope, body.snapshot_id)
    if snapshot is None or snapshot.status != "READY":
        raise GraphFactNotFound("graph snapshot not found")
    index = await store.load_index(scope, snapshot)
    if any(identity not in index.edges for identity in body.edge_ids):
        raise GraphFactNotFound("graph edge not found")
    nodes = {
        identity
        for key in body.edge_ids
        for identity in (index.edges[key].source_node_id, index.edges[key].target_node_id)
    }
    result = _subgraph(index._subgraph(nodes, set(body.edge_ids)))
    active = await store.active_snapshot(scope, snapshot.source_revision_ids)
    result.is_active_snapshot = active is not None and active.snapshot_id == snapshot.snapshot_id
    return result


@router.post(
    "/overview",
    operation_id="graph_overview",
    response_model=GraphOverviewView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: problem_response_metadata("Graph unavailable")},
)
async def domain_overview(request: Request, body: GraphOverviewRequest) -> GraphOverviewView:
    overview = await graph_overview_service(request).overview(
        request.state.project_scope, node_limit=body.node_limit
    )
    return GraphOverviewView(
        nodes=[
            GraphOverviewNodeView(
                overview_node_id=node.overview_node_id,
                label=node.label,
                node_type=node.node_type,
                degree=node.degree,
                members=[
                    GraphOverviewMemberView(
                        snapshot_id=member.snapshot_id,
                        node_id=member.node_id,
                        source_id=member.source_id,
                    )
                    for member in node.members
                ],
            )
            for node in overview.nodes
        ],
        edges=[
            GraphOverviewEdgeView(
                overview_edge_id=edge.overview_edge_id,
                source=edge.source,
                target=edge.target,
                relation_type=edge.relation_type,
                support=edge.support,
            )
            for edge in overview.edges
        ],
        stats=GraphOverviewStatsView(
            source_count=overview.source_count,
            node_type_counts=dict(overview.node_type_counts),
            truncated=overview.truncated,
        ),
        snapshot_ids=list(overview.snapshot_ids),
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
