from dataclasses import replace

import pytest
from test_knowledge_api import FakeModelPort, FakeSearchPort, api, policy_context, search_hit

from tap.modules.knowledge.domain.models import AnswerRequest
from tap.modules.knowledge.ports.models import AnswerGeneration, GeneratedClaim


@pytest.mark.asyncio
async def test_retrieval_phase_maps_actual_hits_and_relation_citations_use_final_claim_index():
    facts = []

    class Models(FakeModelPort):
        async def answer(self, query, evidence, profile_id, **options):
            assert options["graph_context"][0]["evidenceLabels"] == ["S1"]
            return AnswerGeneration(
                "Bad relation.\n\nA depends on B.",
                (
                    GeneratedClaim("Bad relation.", ("S1",), ("R99",)),
                    GeneratedClaim("A depends on B.", (), ("R1",)),
                ),
                "tap-answer-v1",
                "grounded-answer-v1",
                None,
            )

    async def graph_phase(evidence):
        assert tuple(item.chunk_id for item in evidence) == (search_hit().chunk_id,)
        facts.append(
            {
                "kind": "edge",
                "id": "edge",
                "snapshotId": "snapshot",
                "relationLabel": "R1",
                "sourceNodeId": "a",
                "targetNodeId": "b",
                "sourceLabel": "A",
                "targetLabel": "B",
                "relationType": "DEPENDS_ON",
                "origin": "EXTRACTED",
                "evidence": [{"chunkId": evidence[0].chunk_id}],
                "evidenceLabels": [evidence[0].evidence_label],
            }
        )
        return facts

    response = await api(FakeSearchPort((search_hit(),)), Models()).answer_frozen(
        AnswerRequest(query="What relationship?"),
        policy_context(),
        governance=None,
        graph_phase=graph_phase,
    )
    assert not response.abstained
    assert response.answer == "A depends on B."
    assert response.relation_citations[0].claim_index == 0
    assert response.relation_citations[0].path_edge_ids == ("edge",)
    assert response.claims[0].citation_ids


@pytest.mark.asyncio
async def test_relation_turn_without_agent_calls_path_and_cites_new_edge(tmp_path):
    import json
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from test_litellm_model_gateway import configured_gateway, redact, success

    from tap.contracts.http import RetrievalAnswerRequest
    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.application.tool_loop import AgentToolRuntime
    from tap.modules.chat.application.plan_answer import AnswerPlanner, planning_input
    from tap.modules.chat.domain.conversations import (
        FrozenResource,
        TurnInput,
        TurnInputSnapshot,
        content_digest,
    )
    from tap.modules.graph.application.queries import InMemoryGraphStore
    from tap.modules.graph.domain.models import Evidence as GraphEvidence
    from tap.modules.graph.domain.models import (
        GraphEdge,
        GraphNode,
        GraphSnapshot,
        GraphSnapshotDraft,
        RelationOrigin,
    )
    from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
    from tap.modules.knowledge.api import KnowledgeAPI
    from tap.modules.knowledge.application.answers import AnswerService
    from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
    from tap.modules.knowledge.application.graph_enrichment import GraphAnswerEnricher
    from tap.quality.golden import _GoldenRevisionRepository, _GoldenSearchIndex, _ingest
    from tap.testing.deterministic_model_gateway import DeterministicModelGateway, ToolTurnScript

    class Gateway(DeterministicModelGateway):
        requested = []

        async def chat_with_tools(self, request, messages, tools):
            self.requested.append(tuple(tool.name for tool in tools))
            return await super().chat_with_tools(request, messages, tools)

        async def generate_structured(self, request):
            result = await super().generate_structured(request)
            context = json.loads(request.context)
            if "knowledgeGraph" in context:
                assert any(item.get("id") == "e3" for item in context["knowledgeGraph"])
                edge = next(item for item in context["knowledgeGraph"] if item.get("id") == "e3")
                assert edge["relationLabel"] == "R4"
                return replace(
                    result,
                    output={
                        "answer": "Entity 3 depends on Entity 4.",
                        "claims": [
                            {
                                "text": "Entity 3 depends on Entity 4.",
                                "evidenceLabels": edge["evidenceLabels"],
                                "relationLabels": ["R4"],
                            }
                        ],
                    },
                )
            return result

    gateway = Gateway(
        configured_gateway(success)._config,
        scope=VALIDATION_SCOPE,
        redact=redact,
        tool_script=(ToolTurnScript((("graph.path", {"from": "n0", "to": "n4", "maxHops": 4}),)),),
    )
    models = KnowledgeModelGateway(
        gateway,
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=1,
    )
    (tmp_path / "relations.md").write_text(
        "# Start\nEntity 0 is a service.\n\n# Relations\nEntity 3 depends on Entity 4.\n"
    )
    revisions, chunks = await _ingest(tmp_path, ["relations.md"], models)
    revision = revisions["relations.md"]

    class Search(_GoldenSearchIndex):
        calls = 0

        async def search(self, execution):
            self.calls += 1
            hits = await super().search(execution)
            return tuple(
                hit
                for hit in hits
                if self.calls > 1 or hit.chunk_id == str(chunks[0].draft.chunk_id)
            )

    from test_knowledge_api import CurrentPolicyVerifier, PassthroughRedactor

    knowledge = KnowledgeAPI(
        search=Search(chunks),
        models=models,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
    )
    answers = AnswerService(repository=_GoldenRevisionRepository(revisions), knowledge=knowledge)
    graph_store = InMemoryGraphStore()
    snapshot = GraphSnapshot.create(
        snapshot_id="graph",
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=(revision.revision_id,),
        document_revision_ids=(revision.revision_id,),
    )
    ev = tuple(
        GraphEvidence(
            f"ev{i}",
            "graph",
            revision.revision_id,
            revision.revision_id,
            str(chunk.draft.chunk_id),
            {"kind": "page", "page": i + 1},
            chunk.draft.chunk_content_hash,
        )
        for i, chunk in enumerate(chunks)
    )
    await graph_store.publish(
        VALIDATION_SCOPE,
        GraphSnapshotDraft(
            snapshot,
            tuple(
                GraphNode(
                    f"n{i}",
                    "graph",
                    f"Entity {i}",
                    "ENTITY",
                    f"entity{i}",
                    ("ev0",) if i == 0 else (),
                )
                for i in range(5)
            ),
            tuple(
                GraphEdge(
                    f"e{i}",
                    "graph",
                    f"n{i}",
                    f"n{i + 1}",
                    "DEPENDS_ON",
                    RelationOrigin.EXTRACTED,
                    1.0,
                    ("ev1",),
                )
                for i in range(4)
            ),
            ev,
            (),
        ),
    )
    policy = build_demo_policy_context((revision,))
    frozen = TurnInput(
        message="What is the dependency path from Entity 0 to Entity 4?",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        source_revision_ids=(revision.revision_id,),
        document_revision_ids=(revision.revision_id,),
        resolved_resources=(
            FrozenResource(
                revision.source_id,
                revision.document_id,
                revision.revision_id,
                revision.source_content_hash,
            ),
        ),
        acl_digest=policy.acl_digest,
        retrieval_policy_digest=content_digest(
            {
                "decisionId": policy.decision_id,
                "policyVersion": policy.policy_version,
                "corpusVersion": policy.active_corpus_version,
            }
        ),
    )
    turn = TurnInputSnapshot.create(
        snapshot_id="input",
        project_id=VALIDATION_SCOPE.project_id,
        turn_id="turn",
        value=frozen,
        now=datetime.now(timezone.utc),
    )
    plan = await AnswerPlanner().plan(planning_input(turn))
    scoped = SimpleNamespace(scope=VALIDATION_SCOPE)
    service = KnowledgeHttpService(
        documents=scoped,
        answers=answers,
        citations=scoped,
        models=models,
        graph_enricher=GraphAnswerEnricher(graph_store),
    )
    result = await service.answer_conversation(
        RetrievalAnswerRequest(query=frozen.message),
        frozen,
        answer_plan=plan,
        agent_tools=AgentToolRuntime(gateway),
    )
    assert not result.abstained
    assert result.relation_citations[0].edge_id == "e3"
    assert result.relation_citations[0].claim_index == 0
    assert "graph.path" in gateway.requested[0]
    assert any(citation.evidence_label == "S2" for citation in result.citations)


def test_claim_relations_split_branching_edges_into_continuous_paths():
    from tap.modules.knowledge.application.retrieve import _relation_citations

    context = [
        dict(
            kind="edge",
            relationLabel=f"R{i}",
            id=f"e{i}",
            snapshotId="s",
            sourceNodeId="a",
            targetNodeId=target,
            sourceLabel="A",
            targetLabel=target.upper(),
            relationType="RELATES_TO",
            origin="EXTRACTED",
        )
        for i, target in enumerate(("b", "c", "d"), 1)
    ]
    generation = AnswerGeneration(
        "Connected entities.",
        (GeneratedClaim("Connected entities.", ("S1",), ("R1", "R2", "R3")),),
        "model",
        "profile",
        None,
    )
    values = _relation_citations(generation, context, lambda: "citation")
    assert values[0].path_edge_ids == ("e1", "e2")
    assert values[2].path_edge_ids == ("e3",)
