import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_PROFILE_DIGEST
from tap.modules.graph.application.jobs import InMemoryGraphJobStore
from tap.modules.graph.application.merge_jobs import InMemoryProjectMergeQueue
from tap.modules.graph.application.node_enrichment import InMemoryGraphNodeEnrichment
from tap.modules.graph.application.project_queries import InMemoryProjectGraphStore
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.jobs import (
    GraphBatchStatus,
    GraphFragmentBatch,
    GraphJobRequest,
)
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.graph.domain.project import (
    Alias,
    EdgeEvidence,
    NodeSource,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectNode,
)
from tap.modules.graph.domain.vocabulary import normalize_key
from tap.modules.graph.ports.project_store import ProjectGraphVersionMismatch
from tests.conftest import validation_http_services

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_ORIGIN = "http://127.0.0.1:15181"


def _client(services) -> TestClient:
    return TestClient(
        create_app(services, validation_mode=True, allowed_origins=frozenset({_ORIGIN})),
        headers={"Origin": _ORIGIN},
    )


def _project_draft() -> ProjectGraphDraft:
    policy_id = "gpn_policy"
    claim_id = "gpn_claim"
    edge_id = "gpe_policy_governs_claim"
    return ProjectGraphDraft(
        fragment_digest="sha256:" + "a" * 64,
        nodes=(
            ProjectNode(
                node_id=policy_id,
                label="健康告知书",
                node_type="ENTITY",
                canonical_key=normalize_key("健康告知书"),
                degree=1,
                community_id="community-1",
                aliases=("健康告知书",),
            ),
            ProjectNode(
                node_id=claim_id,
                label="Claim",
                node_type="ENTITY",
                canonical_key=normalize_key("Claim"),
                degree=1,
                community_id="community-1",
                aliases=(),
            ),
        ),
        edges=(
            ProjectEdge(
                edge_id=edge_id,
                source_node_id=policy_id,
                target_node_id=claim_id,
                relation_type="GOVERNS",
                relation_label="governs",
                origin=RelationOrigin.EXTRACTED,
                confidence=1.0,
            ),
        ),
        node_sources=(
            NodeSource(
                node_id=policy_id,
                source_revision_id="source-revision-1",
                document_revision_id="document-revision-1",
                chunk_id="chunk-1",
                anchor={"kind": "text", "start": 0, "end": 6},
                fragment_snapshot_id="snapshot-1",
                fragment_node_id="node-1",
            ),
        ),
        edge_evidence=(
            EdgeEvidence(
                edge_id=edge_id,
                source_revision_id="source-revision-1",
                document_revision_id="document-revision-1",
                chunk_id="chunk-1",
                anchor={"kind": "text", "start": 0, "end": 6},
                content_digest="sha256:" + "b" * 64,
                fragment_snapshot_id="snapshot-1",
                fragment_edge_id="edge-1",
            ),
        ),
        aliases=(
            Alias(alias_norm=normalize_key("健康告知书"), node_id=policy_id, origin="LABEL"),
            Alias(alias_norm=normalize_key("健康告知"), node_id=policy_id, origin="LABEL"),
        ),
        communities=(),
        merge_log=(),
    )


def _legacy_snapshot_graph() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    evidence = Evidence(
        "evidence-1",
        "snapshot-1",
        "source-revision-1",
        "document-revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 6},
        "sha256:" + "c" * 64,
    )
    asyncio.run(
        store.publish(
            VALIDATION_SCOPE,
            GraphSnapshotDraft(
                GraphSnapshot.create(
                    snapshot_id="snapshot-1",
                    project_id=VALIDATION_SCOPE.project_id,
                    source_revision_ids=("source-revision-1",),
                    document_revision_ids=("document-revision-1",),
                ),
                (
                    GraphNode(
                        "node-1", "snapshot-1", "Policy", "ENTITY", "policy", ("evidence-1",)
                    ),
                    GraphNode("node-2", "snapshot-1", "Claim", "ENTITY", "claim"),
                ),
                (
                    GraphEdge(
                        "edge-1",
                        "snapshot-1",
                        "node-1",
                        "node-2",
                        "GOVERNS",
                        RelationOrigin.EXTRACTED,
                        1.0,
                        ("evidence-1",),
                    ),
                ),
                (evidence,),
                (),
            ),
        )
    )
    return store


def test_project_graph_routes_exist_with_camel_case_schemas():
    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=InMemoryProjectGraphStore(),
        graph_jobs=InMemoryGraphJobStore(),
    )
    client = _client(services)
    paths = client.app.openapi()["paths"]
    assert "/api/v1/projects/{project_id}/knowledge/graph/project" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/overview" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/neighbors" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/highlight" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/fragments/{revision_id}/retry" in paths
    schema = client.app.openapi()["components"]["schemas"]["ProjectGraphView"]
    assert "graphVersion" in schema["properties"]
    assert "extractingRevisionIds" in schema["properties"]


def test_project_is_empty_before_any_merge():
    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=InMemoryProjectGraphStore(),
        graph_jobs=InMemoryGraphJobStore(),
    )
    client = _client(services)
    response = client.get("/api/v1/projects/tapper-demo/knowledge/graph/project")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "EMPTY"
    assert body["graphVersion"] is None
    assert body["nodeCount"] == 0


def test_project_is_merging_while_queue_is_due():
    project_graph = InMemoryProjectGraphStore()
    merge_queue = InMemoryProjectMergeQueue(store=project_graph)
    asyncio.run(merge_queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=_NOW))
    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=project_graph,
        graph_jobs=InMemoryGraphJobStore(merge_queue=merge_queue),
    )
    client = _client(services)
    response = client.get("/api/v1/projects/tapper-demo/knowledge/graph/project")
    assert response.status_code == 200
    assert response.json()["status"] == "MERGING"


def test_overview_query_neighbors_path_and_highlight_carry_graph_version():
    project_graph = InMemoryProjectGraphStore()
    asyncio.run(project_graph.publish(VALIDATION_SCOPE, _project_draft(), now=_NOW))
    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=project_graph,
        graph_jobs=InMemoryGraphJobStore(),
    )
    client = _client(services)
    base = "/api/v1/projects/tapper-demo/knowledge/graph"

    overview = client.get(f"{base}/overview")
    assert overview.status_code == 200
    assert overview.json()["graphVersion"] == 1

    query = client.post(f"{base}/query", json={"query": "*"})
    assert query.status_code == 200
    assert query.json()["graphVersion"] == 1

    neighbors = client.post(f"{base}/neighbors", json={"nodeId": "gpn_policy"})
    assert neighbors.status_code == 200
    assert neighbors.json()["graphVersion"] == 1

    path = client.post(
        f"{base}/path",
        json={"sourceNodeId": "gpn_policy", "targetNodeId": "gpn_claim"},
    )
    assert path.status_code == 200
    assert path.json()["graphVersion"] == 1

    highlight = client.post(f"{base}/highlight", json={"edgeIds": ["gpe_policy_governs_claim"]})
    assert highlight.status_code == 200
    assert highlight.json()["graphVersion"] == 1


def test_stale_graph_version_is_409():
    project_graph = InMemoryProjectGraphStore()
    asyncio.run(project_graph.publish(VALIDATION_SCOPE, _project_draft(), now=_NOW))
    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=project_graph,
        graph_jobs=InMemoryGraphJobStore(),
    )
    client = _client(services)
    response = client.post(
        "/api/v1/projects/tapper-demo/knowledge/graph/query",
        json={"query": "*", "graphVersion": 99},
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/graph-version-mismatch")
    assert "1" in response.json()["detail"]


def test_store_level_version_mismatch_race_is_also_409():
    """`_require_current_version` matches the request against `get_current()`
    before the store call, but a concurrent merge can still evict the pinned
    version from the cache between that check and the call itself -- the
    store then raises its own `ProjectGraphVersionMismatch`, which must map
    to the same public 409 as the route-level check."""

    project_graph = InMemoryProjectGraphStore()
    asyncio.run(project_graph.publish(VALIDATION_SCOPE, _project_draft(), now=_NOW))

    class _RacyStore(InMemoryProjectGraphStore):
        async def search(self, scope, text, **kwargs):
            raise ProjectGraphVersionMismatch(1)

    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=_RacyStore(),
        graph_jobs=InMemoryGraphJobStore(),
    )
    asyncio.run(services.project_graph.publish(VALIDATION_SCOPE, _project_draft(), now=_NOW))
    client = _client(services)
    response = client.post(
        "/api/v1/projects/tapper-demo/knowledge/graph/query", json={"query": "*"}
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/graph-version-mismatch")
    assert "1" in response.json()["detail"]


def test_busy_graph_job_retry_is_409():
    jobs = InMemoryGraphJobStore()
    scope = VALIDATION_SCOPE
    request = GraphJobRequest.create(
        scope=scope,
        revision_id="revision-busy",
        chunks_locator="locator-busy",
        extraction_profile_digest=GRAPH_EXTRACTION_PROFILE_DIGEST,
        model_alias="alias-1",
    )
    real_now = datetime.now(UTC)
    asyncio.run(jobs.request(scope, request, now=real_now))
    # Claim and leave RUNNING with an unexpired lease (no batch recorded as
    # FAILED, but the busy check must fire before even looking at batches).
    # Uses real wall-clock time since the HTTP route itself calls
    # `datetime.now(UTC)`, not the fixed `_NOW` fixture timestamp.
    asyncio.run(
        jobs.claim(
            scope, worker_id="worker-1", now=real_now, lease_duration=timedelta(minutes=5), limit=1
        )
    )
    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=InMemoryProjectGraphStore(),
        graph_jobs=jobs,
    )
    client = _client(services)
    response = client.post(
        "/api/v1/projects/tapper-demo/knowledge/graph/fragments/revision-busy/retry"
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/graph-job-busy")


def test_query_without_snapshot_id_uses_project_graph():
    project_graph = InMemoryProjectGraphStore()
    asyncio.run(project_graph.publish(VALIDATION_SCOPE, _project_draft(), now=_NOW))
    services = replace(
        validation_http_services(),
        graph=_legacy_snapshot_graph(),
        project_graph=project_graph,
        graph_jobs=InMemoryGraphJobStore(),
    )
    client = _client(services)
    base = "/api/v1/projects/tapper-demo/knowledge/graph"

    project_response = client.post(f"{base}/query", json={"query": "健康"})
    assert project_response.status_code == 200
    project_body = project_response.json()
    assert "graphVersion" in project_body
    assert any(node["nodeId"] == "gpn_policy" for node in project_body["nodes"])

    legacy_response = client.post(f"{base}/query", json={"snapshotId": "snapshot-1", "query": "*"})
    assert legacy_response.status_code == 200
    legacy_body = legacy_response.json()
    assert legacy_body["snapshotId"] == "snapshot-1"
    assert "graphVersion" not in legacy_body


def test_node_detail_groups_sources_and_relations():
    project_graph = InMemoryProjectGraphStore()
    asyncio.run(project_graph.publish(VALIDATION_SCOPE, _project_draft(), now=_NOW))
    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=project_graph,
        graph_jobs=InMemoryGraphJobStore(),
    )
    client = _client(services)
    response = client.get("/api/v1/projects/tapper-demo/knowledge/graph/nodes/gpn_policy")
    assert response.status_code == 200
    body = response.json()
    assert body["node"]["nodeId"] == "gpn_policy"
    assert len(body["sources"]) == 1
    assert body["sources"][0]["sourceRevisionId"] == "source-revision-1"
    assert len(body["relations"]) == 1
    assert body["relations"][0]["relationType"] == "GOVERNS"
    assert any(neighbor["nodeId"] == "gpn_claim" for neighbor in body["neighbors"])


def test_node_detail_resolves_source_name_and_snippet_from_enrichment_port():
    project_graph = InMemoryProjectGraphStore()
    asyncio.run(project_graph.publish(VALIDATION_SCOPE, _project_draft(), now=_NOW))
    enrichment = InMemoryGraphNodeEnrichment()
    enrichment.put_source_name(VALIDATION_SCOPE, "source-revision-1", "AIA Health Policy")
    enrichment.put_snippet(
        VALIDATION_SCOPE, "source-revision-1", "chunk-1", "Health declaration text."
    )
    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=project_graph,
        graph_jobs=InMemoryGraphJobStore(),
        graph_node_enrichment=enrichment,
    )
    client = _client(services)
    response = client.get("/api/v1/projects/tapper-demo/knowledge/graph/nodes/gpn_policy")
    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0]["sourceName"] == "AIA Health Policy"
    assert body["sources"][0]["evidence"][0]["snippet"] == "Health declaration text."


def test_node_detail_leaves_source_name_and_snippet_none_when_unresolved():
    project_graph = InMemoryProjectGraphStore()
    asyncio.run(project_graph.publish(VALIDATION_SCOPE, _project_draft(), now=_NOW))
    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=project_graph,
        graph_jobs=InMemoryGraphJobStore(),
        graph_node_enrichment=InMemoryGraphNodeEnrichment(),
    )
    client = _client(services)
    response = client.get("/api/v1/projects/tapper-demo/knowledge/graph/nodes/gpn_policy")
    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0]["sourceName"] is None
    assert body["sources"][0]["evidence"][0]["snippet"] is None


def test_fragment_retry_requeues_failed_batches():
    jobs = InMemoryGraphJobStore()
    scope = VALIDATION_SCOPE
    request = GraphJobRequest.create(
        scope=scope,
        revision_id="revision-1",
        chunks_locator="locator-1",
        extraction_profile_digest=GRAPH_EXTRACTION_PROFILE_DIGEST,
        model_alias="alias-1",
    )
    asyncio.run(jobs.request(scope, request, now=_NOW))
    claimed = asyncio.run(
        jobs.claim(
            scope, worker_id="worker-1", now=_NOW, lease_duration=timedelta(minutes=5), limit=1
        )
    )[0]
    batch = GraphFragmentBatch(
        snapshot_id=claimed.snapshot.snapshot_id,
        batch_index=0,
        chunk_ids=("chunk-1",),
        status=GraphBatchStatus.FAILED,
        attempt=3,
        failure_code="boom",
    )
    asyncio.run(jobs.record_batch(scope, claimed, batch, now=_NOW))
    asyncio.run(jobs.fail(scope, claimed, failure_code="boom", now=_NOW))

    services = replace(
        validation_http_services(),
        graph=InMemoryGraphStore(),
        project_graph=InMemoryProjectGraphStore(),
        graph_jobs=jobs,
    )
    client = _client(services)
    response = client.post(
        "/api/v1/projects/tapper-demo/knowledge/graph/fragments/revision-1/retry"
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "revisionId": "revision-1",
        "requeuedBatches": 1,
        "jobStatus": "PENDING",
    }
