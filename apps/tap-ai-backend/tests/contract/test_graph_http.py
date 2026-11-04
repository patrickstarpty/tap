import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tests.conftest import validation_http_services


def test_graph_routes_are_project_scoped_and_bounded():
    services = replace(validation_http_services(), graph=InMemoryGraphStore())
    client = TestClient(create_app(services, validation_mode=True))
    paths = client.app.openapi()["paths"]
    assert "/api/v1/projects/{project_id}/knowledge/graph/snapshots" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/query" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/nodes/{node_id}" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/evidence/{evidence_id}" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/nodes/{node_id}/neighbors" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/path" in paths
    schema = client.app.openapi()["components"]["schemas"]["GraphNeighborRequest"]
    assert schema["properties"]["depth"]["maximum"] == 2
    assert schema["properties"]["nodeLimit"]["maximum"] == 500


def test_graph_outage_is_503_not_an_empty_graph():
    client = TestClient(create_app(validation_http_services(), validation_mode=True))
    response = client.get(
        "/api/v1/projects/tapper-demo/knowledge/graph/snapshots",
        params={"sourceRevisionId": "source-revision-1"},
    )
    assert response.status_code == 503
    assert response.json()["type"].endswith("/graph-unavailable")


def test_graph_project_path_cannot_widen_the_trusted_scope():
    services = replace(validation_http_services(), graph=InMemoryGraphStore())
    client = TestClient(create_app(services, validation_mode=True))
    response = client.get(
        "/api/v1/projects/other-project/knowledge/graph/snapshots",
        params={"sourceRevisionId": "source-revision-1"},
    )
    assert response.status_code == 403


def test_graph_node_and_evidence_deep_links_are_snapshot_scoped():
    store = InMemoryGraphStore()
    evidence = Evidence(
        "evidence-1",
        "snapshot-1",
        "source-revision-1",
        "document-revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 6},
        "sha256:" + "a" * 64,
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
                        "node-1",
                        "snapshot-1",
                        "Policy",
                        "ENTITY",
                        "policy",
                        ("evidence-1",),
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
    client = TestClient(
        create_app(replace(validation_http_services(), graph=store), validation_mode=True)
    )

    node = client.get(
        "/api/v1/projects/tapper-demo/knowledge/graph/nodes/node-1",
        params={"snapshotId": "snapshot-1"},
    )
    fact = client.get(
        "/api/v1/projects/tapper-demo/knowledge/graph/evidence/evidence-1",
        params={"snapshotId": "snapshot-1"},
    )
    assert node.status_code == 200
    assert node.json()["nodes"][0]["evidenceIds"] == ["evidence-1"]
    assert fact.status_code == 200
    assert fact.json()["documentRevisionId"] == "document-revision-1"


def test_graph_subgraph_is_bounded_and_rejects_foreign_project():
    store = InMemoryGraphStore()
    draft = GraphSnapshotDraft(
        GraphSnapshot.create(
            snapshot_id="s",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("sr",),
            document_revision_ids=("dr",),
        ),
        (GraphNode("a", "s", "A", "ENTITY", "a"), GraphNode("b", "s", "B", "ENTITY", "b")),
        (GraphEdge("e", "s", "a", "b", "DEPENDS_ON", RelationOrigin.EXTRACTED, 1.0, ("ev",)),),
        (Evidence("ev", "s", "sr", "dr", "c", {"kind": "page", "page": 1}, "sha256:" + "a" * 64),),
        (),
    )
    asyncio.run(store.publish(VALIDATION_SCOPE, draft))
    client = TestClient(
        create_app(
            replace(validation_http_services(), graph=store),
            validation_mode=True,
            allowed_origins=frozenset({"http://localhost:3000"}),
        ),
        headers={"Origin": "http://localhost:3000"},
    )
    body = {"snapshotId": "s", "edgeIds": ["e"]}
    response = client.post("/api/v1/projects/tapper-demo/knowledge/graph/subgraph", json=body)
    assert response.status_code == 200
    assert response.json()["edges"][0]["edgeId"] == "e"
    assert response.json()["isActiveSnapshot"] is True
    assert len(response.json()["nodes"]) == 2
    assert (
        client.post(
            "/api/v1/projects/other-project/knowledge/graph/subgraph", json=body
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/projects/tapper-demo/knowledge/graph/subgraph",
            json={"snapshotId": "foreign", "edgeIds": ["e"]},
        ).status_code
        == 404
    )
