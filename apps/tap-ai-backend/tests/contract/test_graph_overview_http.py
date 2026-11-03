import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.graph.application.cache import CachedSnapshotIndexes
from tap.modules.graph.application.overview import (
    GraphOverviewService,
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
from tests.conftest import validation_http_services

URL = "/api/v1/projects/tapper-demo/knowledge/graph/overview"
ORIGIN = "http://127.0.0.1:5173"


def _draft(snapshot_id: str, revision: str) -> GraphSnapshotDraft:
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
        (
            GraphNode(f"{snapshot_id}-login", snapshot_id, "Login", "ENTITY", "login-service"),
            GraphNode(f"{snapshot_id}-auth", snapshot_id, "Auth", "SYSTEM", "auth"),
        ),
        (
            GraphEdge(
                f"{snapshot_id}-edge",
                snapshot_id,
                f"{snapshot_id}-login",
                f"{snapshot_id}-auth",
                "DEPENDS_ON",
                RelationOrigin.EXTRACTED,
                1.0,
                (evidence.evidence_id,),
            ),
        ),
        (evidence,),
        (),
    )


class Sources:
    async def published_source_revisions(self, scope):
        return PublishedSourceListing(
            (
                PublishedSource("source-a", "revision-a"),
                PublishedSource("source-b", "revision-b"),
            ),
            truncated=False,
        )


def _client(**overrides) -> TestClient:
    store = InMemoryGraphStore()
    asyncio.run(store.publish(VALIDATION_SCOPE, _draft("snapshot-a", "revision-a")))
    asyncio.run(store.publish(VALIDATION_SCOPE, _draft("snapshot-b", "revision-b")))
    services = replace(
        validation_http_services(),
        graph=store,
        graph_overview=GraphOverviewService(store, Sources(), CachedSnapshotIndexes()),
        **overrides,
    )
    return TestClient(
        create_app(services, validation_mode=True, allowed_origins=frozenset({ORIGIN})),
        headers={"Origin": ORIGIN},
    )


def test_graph_overview_merges_published_sources():
    response = _client().post(URL, json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"nodes", "edges", "stats", "snapshotIds"}
    assert body["snapshotIds"] == ["snapshot-a", "snapshot-b"]
    assert body["stats"] == {
        "sourceCount": 2,
        "nodeTypeCounts": {"ENTITY": 1, "SYSTEM": 1},
        "truncated": False,
    }
    login = next(node for node in body["nodes"] if node["nodeType"] == "ENTITY")
    assert set(login) == {"overviewNodeId", "label", "nodeType", "degree", "members"}
    assert login["members"] == [
        {"snapshotId": "snapshot-a", "nodeId": "snapshot-a-login", "sourceId": "source-a"},
        {"snapshotId": "snapshot-b", "nodeId": "snapshot-b-login", "sourceId": "source-b"},
    ]
    [edge] = body["edges"]
    assert set(edge) == {"overviewEdgeId", "source", "target", "relationType", "support"}
    assert edge["support"] == 2 and edge["source"] == login["overviewNodeId"]


def test_graph_overview_node_limit_is_bounded():
    client = _client()
    for limit in (0, 301):
        assert client.post(URL, json={"nodeLimit": limit}).status_code == 422
    response = client.post(URL, json={"nodeLimit": 1})
    assert response.status_code == 200
    assert len(response.json()["nodes"]) == 1
    schema = client.app.openapi()["components"]["schemas"]["GraphOverviewRequest"]
    assert schema["properties"]["nodeLimit"]["maximum"] == 300


def test_graph_overview_requires_knowledge_read():
    class DenyKnowledgeRead:
        async def authorize(self, scope, action, resource):
            return AuthorizationDecision(action != "knowledge.read", "test")

    assert _client(authorization_policy=DenyKnowledgeRead()).post(URL, json={}).status_code == 403
    response = _client().post("/api/v1/projects/other-project/knowledge/graph/overview", json={})
    assert response.status_code == 403


def test_graph_overview_outage_is_503():
    client = TestClient(
        create_app(
            validation_http_services(), validation_mode=True, allowed_origins=frozenset({ORIGIN})
        ),
        headers={"Origin": ORIGIN},
    )
    response = client.post(URL, json={})
    assert response.status_code == 503
    assert response.json()["type"].endswith("/graph-unavailable")
