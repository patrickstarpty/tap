import json
from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.domain.index import SnapshotIndex
from tests.unit.knowledge.test_graph_expansion import chain


@pytest.mark.asyncio
async def test_path_tool_appends_stable_labels_and_bounds_hops_and_scope():
    from tap.modules.knowledge.application.graph_tools import GraphTurnTools

    facts = []
    tools = GraphTurnTools(VALIDATION_SCOPE, SnapshotIndex.build(chain(7)), facts)
    path = tools.builtins()["graph.path"][1]
    result = await path.invoke(
        VALIDATION_SCOPE, {"from": "Entity 0", "to": "Entity 3", "maxHops": 3}
    )
    assert not result.is_error
    assert [item["id"] for item in json.loads(result.content)["relations"]] == ["e0", "e1", "e2"]
    assert [item["relationLabel"] for item in facts] == ["R1", "R2", "R3"]
    again = await path.invoke(VALIDATION_SCOPE, {"from": "n0", "to": "n3", "maxHops": 3})
    assert not again.is_error
    assert len(facts) == 3
    bounded = await path.invoke(VALIDATION_SCOPE, {"from": "n0", "to": "n5", "maxHops": 4})
    assert json.loads(bounded.content)["relations"] == []
    invalid = await path.invoke(VALIDATION_SCOPE, {"from": "n0", "to": "n5", "maxHops": 5})
    assert invalid.is_error
    denied = await path.invoke(
        replace(VALIDATION_SCOPE, project_id="foreign"), {"from": "n0", "to": "n3"}
    )
    assert denied.is_error


@pytest.mark.asyncio
async def test_neighbor_tool_is_bounded_read_and_never_accepts_snapshot_override():
    from tap.modules.knowledge.application.graph_tools import GraphTurnTools

    tools = GraphTurnTools(VALIDATION_SCOPE, SnapshotIndex.build(chain(7)), [])
    descriptor, neighbors = tools.builtins()["graph.neighbors"]
    assert descriptor.effect.value == "read"
    result = await neighbors.invoke(VALIDATION_SCOPE, {"nodeId": "n0", "depth": 2})
    assert len(json.loads(result.content)["relations"]) == 2
    assert (
        await neighbors.invoke(VALIDATION_SCOPE, {"nodeId": "n0", "snapshotId": "foreign"})
    ).is_error


@pytest.mark.asyncio
async def test_graph_tools_are_discoverable_builtins_before_turn_execution():
    from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools

    registry = ToolRegistry(builtin_tools())
    allowed = await registry.require_allowlist(VALIDATION_SCOPE, {"graph.neighbors", "graph.path"})
    assert allowed == frozenset({"graph.neighbors", "graph.path"})
    assert (await registry.get(VALIDATION_SCOPE, "graph.path")).effect.value == "read"


@pytest.mark.asyncio
async def test_new_tool_edges_revalidate_publication_before_mutating_turn_context():
    from tap.modules.access.domain.policy import AuthorizationDenied
    from tap.modules.graph.application.queries import InMemoryGraphStore
    from tap.modules.graph.domain.models import Evidence
    from tap.modules.knowledge.application.graph_enrichment import GraphAnswerEnricher
    from tap.modules.knowledge.application.graph_tools import GraphTurnTools

    draft = chain(5)
    evidence = tuple(
        Evidence(
            f"ev{i}",
            "s",
            "sr",
            "dr",
            "hit" if i == 0 else f"chunk{i}",
            {
                "type": "document",
                "startOffset": 0,
                "endOffset": 1,
                "headingPath": [],
                "inventoryItemId": "revoked" if i > 1 else "approved",
            },
            "sha256:" + "a" * 64,
        )
        for i in range(4)
    )
    draft = replace(
        draft,
        nodes=tuple(
            replace(node, evidence_ids=("ev0",) if node.node_id == "n0" else ())
            for node in draft.nodes
        ),
        edges=tuple(replace(edge, evidence_ids=(f"ev{i}",)) for i, edge in enumerate(draft.edges)),
        evidence=evidence,
    )

    class Authority:
        checked = []

        async def authorize_selection(self, project_id, source_ids):
            return object()

        async def authorize_evidence(self, project_id, **values):
            self.checked.append(values["approved_item_id"])
            if values["approved_item_id"] == "revoked":
                raise AuthorizationDenied("revoked graph evidence")

        async def revalidate(self, binding):
            pass

    authority = Authority()
    store = InMemoryGraphStore()
    await store.publish(VALIDATION_SCOPE, draft)
    enricher = GraphAnswerEnricher(store, publication_authority=authority)
    context = await enricher.enrich(
        VALIDATION_SCOPE, ("sr",), "unmatched", chunk_ids=("hit",), hops=1
    )
    assert context.snapshot_id == "s"
    assert authority.checked == ["approved", "approved"]
    facts = [dict(item) for item in context.facts]
    before = list(facts)

    async def authorize_graph(edge_ids):
        await enricher.authorize_facts(VALIDATION_SCOPE, context.index, edge_ids)

    tools = GraphTurnTools(VALIDATION_SCOPE, context.index, facts, authorize_graph=authorize_graph)
    result = await tools.builtins()["graph.path"][1].invoke(
        VALIDATION_SCOPE, {"from": "n0", "to": "n4", "maxHops": 4}
    )
    assert result.is_error
    assert facts == before
    assert "revoked" in authority.checked
