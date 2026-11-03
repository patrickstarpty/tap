"""SnapshotIndex parity with the pre-index InMemoryGraphStore semantics.

The expected values below were recorded from the linear-scan InMemoryGraphStore
before it delegated to SnapshotIndex; they pin ordering, limits and errors.
"""

from __future__ import annotations

import hashlib
import json
import random

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.index import SnapshotIndex, tokenize
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSearchQuery,
    GraphSnapshot,
    GraphSnapshotDraft,
    GraphSubgraph,
    InferenceProvenance,
    NeighborQuery,
    PathQuery,
    RelationOrigin,
)
from tap.modules.graph.ports.store import GraphFactNotFound

SNAPSHOT = "snapshot-index"


def _evidence(identity: str, chunk_id: str, snapshot_id: str = SNAPSHOT) -> Evidence:
    return Evidence(
        identity,
        snapshot_id,
        "source-revision-1",
        "document-revision-1",
        chunk_id,
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + hashlib.sha256(identity.encode()).hexdigest(),
    )


def _edge(
    identity: str, source: str, target: str, evidence_ids: tuple[str, ...] = ("ev-1",)
) -> GraphEdge:
    return GraphEdge(
        identity,
        SNAPSHOT,
        source,
        target,
        "RELATES_TO",
        RelationOrigin.EXTRACTED,
        1.0,
        evidence_ids,
    )


def _snapshot(snapshot_id: str = SNAPSHOT) -> GraphSnapshot:
    return GraphSnapshot.create(
        snapshot_id=snapshot_id,
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("source-revision-1",),
        document_revision_ids=("document-revision-1",),
    )


def handcrafted_draft() -> GraphSnapshotDraft:
    nodes = (
        GraphNode("n-07", SNAPSHOT, "Login Service 登录服务", "SYSTEM", "login_service", ("ev-1",)),
        GraphNode("n-02", SNAPSHOT, "Logout Handler", "SYSTEM", "logout_handler"),
        GraphNode("n-10", SNAPSHOT, "Payment Gateway", "SYSTEM", "payment-gateway", ("ev-4",)),
        GraphNode("n-01", SNAPSHOT, "支付网关", "SYSTEM", "zhi_fu_wang_guan"),
        GraphNode("n-05", SNAPSHOT, "User Account", "ENTITY", "user.account", ("ev-2", "ev-3")),
        GraphNode("n-03", SNAPSHOT, "Account Lockout Policy", "REQUIREMENT", "account_lockout"),
        GraphNode("n-09", SNAPSHOT, "登录失败", "CONCEPT", "login_failure"),
        GraphNode("n-04", SNAPSHOT, "Session Token", "ENTITY", "session_token"),
        GraphNode("n-08", SNAPSHOT, "Audit Log", "ENTITY", "audit_log"),
        GraphNode("n-06", SNAPSHOT, "MFA Challenge", "CONCEPT", "mfa"),
        GraphNode("n-11", SNAPSHOT, "Isolated", "ACTOR", "isolated"),
        GraphNode("n-12", SNAPSHOT, "Café Menu", "ENTITY", "cafe_menu"),
    )
    edges = (
        _edge("e-05", "n-07", "n-02", ("ev-1",)),
        _edge("e-01", "n-07", "n-05", ("ev-2",)),
        _edge("e-09", "n-05", "n-03", ("ev-3",)),
        _edge("e-03", "n-03", "n-09", ("ev-3", "ev-5")),
        _edge("e-02", "n-09", "n-07", ("ev-5",)),
        _edge("e-08", "n-07", "n-04", ("ev-6",)),
        _edge("e-04", "n-04", "n-08", ("ev-6",)),
        _edge("e-10", "n-08", "n-06", ("ev-6",)),
        _edge("e-06", "n-10", "n-01", ("ev-4",)),
        _edge("e-07", "n-01", "n-05", ("ev-4",)),
        _edge("e-11", "n-07", "n-05", ("ev-1",)),
        _edge("e-12", "n-12", "n-10", ("ev-4",)),
        GraphEdge("e-13", SNAPSHOT, "n-06", "n-02", "IMPLIES", RelationOrigin.INFERRED, 0.5),
    )
    evidence = (
        _evidence("ev-3", "c-2"),
        _evidence("ev-1", "c-1"),
        _evidence("ev-2", "c-1"),
        _evidence("ev-4", "c-3"),
        _evidence("ev-5", "c-2"),
        _evidence("ev-6", "c-4"),
    )
    provenance = (
        InferenceProvenance("prov-1", SNAPSHOT, "e-13", ("e-10", "e-05"), "sha256:" + "c" * 64),
    )
    return GraphSnapshotDraft(_snapshot(), nodes, edges, evidence, provenance)


_WORDS = (
    "login",
    "account",
    "policy",
    "payment",
    "gateway",
    "token",
    "audit",
    "登录",
    "支付",
    "服务",
    "风险",
    "rule",
)


def random_draft(seed: int = 3, nodes: int = 80, edges: int = 300) -> GraphSnapshotDraft:
    rng = random.Random(seed)
    evidence = tuple(_evidence(f"ev-{index}", f"c-{index % 7}") for index in range(20))
    node_ids = [f"r-{index:03d}" for index in range(nodes)]
    rng.shuffle(node_ids)
    graph_nodes = tuple(
        GraphNode(
            identity,
            SNAPSHOT,
            " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 3))).title(),
            "ENTITY",
            "_".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 2))),
            tuple(sorted(rng.sample([item.evidence_id for item in evidence], rng.randint(0, 2)))),
        )
        for identity in node_ids
    )
    edge_ids = [f"x-{index:04d}" for index in range(edges)]
    rng.shuffle(edge_ids)
    graph_edges = []
    for identity in edge_ids:
        source, target = rng.sample(node_ids, 2)
        graph_edges.append(_edge(identity, source, target, (rng.choice(evidence).evidence_id,)))
    return GraphSnapshotDraft(_snapshot(), graph_nodes, tuple(graph_edges), evidence, ())


def _ids(subgraph: GraphSubgraph) -> list[list[str]]:
    return [
        [node.node_id for node in subgraph.nodes],
        [edge.edge_id for edge in subgraph.edges],
        [item.evidence_id for item in subgraph.evidence],
    ]


HANDCRAFTED_SEARCHES = (
    ("*", 50),
    ("*", 3),
    ("login", 50),
    ("LOG", 50),
    ("ogin", 50),
    ("登录", 50),
    ("录", 50),
    ("服务", 50),
    ("account", 50),
    ("account lockout", 50),
    ("ccount lock", 50),
    ("lockout policy", 50),
    ("-gateway", 50),
    ("zhi_fu", 50),
    ("café", 50),
    ("é", 50),
    ("nothing", 50),
    ("a", 4),
    ("session token", 50),
    ("_", 50),
    ("user.acc", 50),
    ("service 登录", 50),
    ("e 登", 50),
    ("Login Service 登录服务", 50),
)
HANDCRAFTED_NEIGHBORS = (
    ("n-07", 1, 50),
    ("n-07", 2, 50),
    ("n-07", 1, 2),
    ("n-07", 2, 4),
    ("n-11", 2, 50),
    ("missing", 1, 50),
    ("n-06", 2, 5),
    ("n-01", 2, 3),
    ("n-12", 2, 1),
)
HANDCRAFTED_PATHS = (
    ("n-02", "n-06", 50),
    ("n-10", "n-08", 50),
    ("n-10", "n-08", 3),
    ("n-11", "n-07", 50),
    ("n-07", "n-07", 50),
    ("n-12", "n-06", 50),
    ("missing", "n-07", 50),
    ("n-07", "missing", 50),
    ("n-03", "n-04", 4),
    ("n-12", "n-03", 5),
)


def random_cases(seed: int = 11) -> tuple[list, list, list]:
    rng = random.Random(seed)
    draft = random_draft()
    ids = [node.node_id for node in draft.nodes]
    searches = []
    for _ in range(40):
        word = rng.choice(_WORDS)
        start = rng.randint(0, max(0, len(word) - 2))
        searches.append((word[start : start + rng.randint(1, len(word))], rng.randint(1, 30)))
    searches += [("*", 17), ("login account", 50), ("Token Rule", 50), ("登录支付", 50)]
    neighbors = [(rng.choice(ids), rng.randint(1, 2), rng.randint(1, 60)) for _ in range(40)]
    paths = [(rng.choice(ids), rng.choice(ids), rng.randint(1, 80)) for _ in range(40)]
    return searches, neighbors, paths


async def observe(store, draft: GraphSnapshotDraft, searches, neighbors, paths) -> dict:
    snapshot_id = draft.snapshot.snapshot_id
    return {
        "search": [
            _ids(await store.search(VALIDATION_SCOPE, GraphSearchQuery(snapshot_id, text, limit)))
            for text, limit in searches
        ],
        "neighbors": [
            _ids(
                await store.neighbors(
                    VALIDATION_SCOPE, NeighborQuery(snapshot_id, node, depth, limit)
                )
            )
            for node, depth, limit in neighbors
        ],
        "path": [
            _ids(
                await store.bounded_path(
                    VALIDATION_SCOPE, PathQuery(snapshot_id, source, target, limit)
                )
            )
            for source, target, limit in paths
        ],
    }


class _IndexAdapter:
    def __init__(self, index: SnapshotIndex) -> None:
        self._index = index

    async def search(self, _scope, query):
        return self._index.search(query)

    async def neighbors(self, _scope, query):
        return self._index.neighbors(query)

    async def bounded_path(self, _scope, query):
        return self._index.bounded_path(query)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


EXPECTED_HANDCRAFTED: dict = {
    "neighbors": [
        [
            ["n-07", "n-02", "n-05", "n-09", "n-04"],
            ["e-05", "e-01", "e-02", "e-08", "e-11"],
            ["ev-3", "ev-1", "ev-2", "ev-5", "ev-6"],
        ],
        [
            ["n-07", "n-02", "n-01", "n-05", "n-03", "n-09", "n-04", "n-08", "n-06"],
            [
                "e-05",
                "e-01",
                "e-09",
                "e-03",
                "e-02",
                "e-08",
                "e-04",
                "e-10",
                "e-07",
                "e-11",
                "e-13",
            ],
            ["ev-3", "ev-1", "ev-2", "ev-4", "ev-5", "ev-6"],
        ],
        [["n-07", "n-02"], ["e-05"], ["ev-1"]],
        [
            ["n-07", "n-02", "n-05", "n-04"],
            ["e-05", "e-01", "e-08", "e-11"],
            ["ev-3", "ev-1", "ev-2", "ev-6"],
        ],
        [["n-11"], [], []],
        [[], [], []],
        [
            ["n-07", "n-02", "n-04", "n-08", "n-06"],
            ["e-05", "e-08", "e-04", "e-10", "e-13"],
            ["ev-1", "ev-6"],
        ],
        [["n-10", "n-01", "n-05"], ["e-06", "e-07"], ["ev-3", "ev-2", "ev-4"]],
        [["n-12"], [], []],
    ],
    "path": [
        [["n-02", "n-06"], ["e-13"], []],
        [
            ["n-07", "n-10", "n-01", "n-05", "n-04", "n-08"],
            ["e-01", "e-08", "e-04", "e-06", "e-07"],
            ["ev-3", "ev-1", "ev-2", "ev-4", "ev-6"],
        ],
        [[], [], []],
        [[], [], []],
        [["n-07"], [], ["ev-1"]],
        [
            ["n-07", "n-02", "n-10", "n-01", "n-05", "n-06", "n-12"],
            ["e-05", "e-01", "e-06", "e-07", "e-12", "e-13"],
            ["ev-3", "ev-1", "ev-2", "ev-4"],
        ],
        [[], [], []],
        [[], [], []],
        [[], [], []],
        [[], [], []],
    ],
    "search": [
        [
            [
                "n-07",
                "n-02",
                "n-10",
                "n-01",
                "n-05",
                "n-03",
                "n-09",
                "n-04",
                "n-08",
                "n-06",
                "n-11",
                "n-12",
            ],
            [
                "e-05",
                "e-01",
                "e-09",
                "e-03",
                "e-02",
                "e-08",
                "e-04",
                "e-10",
                "e-06",
                "e-07",
                "e-11",
                "e-12",
                "e-13",
            ],
            ["ev-3", "ev-1", "ev-2", "ev-4", "ev-5", "ev-6"],
        ],
        [["n-07", "n-02", "n-10"], ["e-05"], ["ev-1", "ev-4"]],
        [["n-07", "n-09"], ["e-02"], ["ev-1", "ev-5"]],
        [["n-07", "n-02", "n-09", "n-08"], ["e-05", "e-02"], ["ev-1", "ev-5"]],
        [["n-07", "n-09"], ["e-02"], ["ev-1", "ev-5"]],
        [["n-07", "n-09"], ["e-02"], ["ev-1", "ev-5"]],
        [["n-07", "n-09"], ["e-02"], ["ev-1", "ev-5"]],
        [["n-07"], [], ["ev-1"]],
        [["n-05", "n-03"], ["e-09"], ["ev-3", "ev-2"]],
        [["n-03"], [], []],
        [["n-03"], [], []],
        [["n-03"], [], []],
        [["n-10"], [], ["ev-4"]],
        [["n-01"], [], []],
        [["n-12"], [], []],
        [["n-12"], [], []],
        [[], [], []],
        [["n-02", "n-10", "n-01", "n-05"], ["e-06", "e-07"], ["ev-3", "ev-2", "ev-4"]],
        [["n-04"], [], []],
        [
            ["n-07", "n-02", "n-01", "n-03", "n-09", "n-04", "n-08", "n-12"],
            ["e-05", "e-03", "e-02", "e-08", "e-04"],
            ["ev-3", "ev-1", "ev-5", "ev-6"],
        ],
        [["n-05"], [], ["ev-3", "ev-2"]],
        [["n-07"], [], ["ev-1"]],
        [["n-07"], [], ["ev-1"]],
        [["n-07"], [], ["ev-1"]],
    ],
}
EXPECTED_RANDOM_DIGEST = "8aa0cf8a702754ba90f8d5bee5c0933bae523cf3612c7ffbdfc1b56b2d2a62c2"


def test_tokenize_lowercases_ascii_words_and_cjk_unigrams_and_bigrams():
    tokens = tokenize("Login Service 登录服务")
    assert {"login", "service", "登", "登录", "录服"} <= tokens
    assert tokens == {
        "login",
        "service",
        "登",
        "录",
        "服",
        "务",
        "登录",
        "录服",
        "服务",
    }
    assert tokenize("user.account_lockout-2") == {"user", "account", "lockout", "2"}
    assert tokenize("  ") == frozenset()


def test_build_indexes_adjacency_degree_chunk_facts_and_size():
    index = SnapshotIndex.build(handcrafted_draft())
    assert index.adjacency["n-07"] == ("e-05", "e-01", "e-02", "e-08", "e-11")
    assert index.adjacency["n-11"] == ()
    assert index.degree["n-07"] == 5
    assert index.degree["n-05"] == 4
    assert index.degree["n-11"] == 0
    assert index.chunk_facts["c-1"] == ("e-01", "e-05", "e-11", "n-05", "n-07")
    assert index.chunk_facts["c-2"] == ("e-02", "e-03", "e-09", "n-05")
    assert index.chunk_facts["c-4"] == ("e-04", "e-08", "e-10")
    assert "login" in index.label_tokens and "n-07" in index.label_tokens["login"]
    assert index.label_tokens["登录"] == frozenset({"n-07", "n-09"})
    assert index.size == 12 + 13 + 6
    assert index.snapshot == handcrafted_draft().snapshot


def test_detail_lookups_and_missing_facts_raise_graph_fact_not_found():
    index = SnapshotIndex.build(handcrafted_draft())
    assert _ids(index.node_detail("n-05")) == [["n-05"], [], ["ev-3", "ev-2"]]
    assert index.evidence_detail("ev-4").chunk_id == "c-3"
    with pytest.raises(GraphFactNotFound, match="graph node not found"):
        index.node_detail("missing")
    with pytest.raises(GraphFactNotFound, match="graph evidence not found"):
        index.evidence_detail("missing")


@pytest.mark.asyncio
async def test_index_matches_recorded_handcrafted_results():
    draft = handcrafted_draft()
    observed = await observe(
        _IndexAdapter(SnapshotIndex.build(draft)),
        draft,
        HANDCRAFTED_SEARCHES,
        HANDCRAFTED_NEIGHBORS,
        HANDCRAFTED_PATHS,
    )
    assert observed == EXPECTED_HANDCRAFTED


@pytest.mark.asyncio
async def test_index_matches_recorded_random_results():
    draft = random_draft()
    observed = await observe(_IndexAdapter(SnapshotIndex.build(draft)), draft, *random_cases())
    assert _digest(observed) == EXPECTED_RANDOM_DIGEST


@pytest.mark.asyncio
async def test_in_memory_store_delegates_with_identical_results():
    draft = handcrafted_draft()
    store = InMemoryGraphStore()
    await store.publish(VALIDATION_SCOPE, draft)
    observed = await observe(
        store, draft, HANDCRAFTED_SEARCHES, HANDCRAFTED_NEIGHBORS, HANDCRAFTED_PATHS
    )
    assert observed == EXPECTED_HANDCRAFTED
    random_store = InMemoryGraphStore()
    other = random_draft()
    await random_store.publish(VALIDATION_SCOPE, other)
    assert _digest(await observe(random_store, other, *random_cases())) == EXPECTED_RANDOM_DIGEST
