from types import SimpleNamespace

import pytest

from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.chat.application.process_turn import ProviderResult, TurnProcessor
from tap.modules.chat.domain.conversations import CitationEvidence, GraphContextStatus


def test_graph_snapshot_is_legal_only_when_graph_context_was_applied():
    with pytest.raises(ValueError, match="graph snapshot"):
        ProviderResult(
            answer="x", graph_context_status=GraphContextStatus.UNAVAILABLE, graph_snapshot_id="g"
        )
    with pytest.raises(ValueError, match="requires"):
        ProviderResult(answer="x", graph_context_status=GraphContextStatus.APPLIED)


@pytest.mark.asyncio
async def test_failure_produces_closed_answer_evidence_and_unknown_provider_events_are_redacted():
    async def provider(_snapshot):
        raise RuntimeError("credential sk-secret should never escape")

    captured = []
    processor = TurnProcessor(provider=provider, complete=captured.append)
    result = await processor.process(
        object(), provider_events=[{"type": "future", "token": "secret"}]
    )
    assert result.outcome == "failed"
    assert result.retrieval_summary.status == "failed"
    assert result.graph_context_status is GraphContextStatus.FAILED
    assert result.citations == ()
    assert result.diagnostics == ("unknown-provider-event",)
    assert "secret" not in repr(result)


@pytest.mark.asyncio
async def test_generation_worker_emits_recoverable_delta_then_closes_the_turn():
    class Repository:
        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(value=SimpleNamespace(message="question")),
                    ),
                ),
            )

    class Conversations:
        repository = Repository()
        events = []
        completed = []

        async def emit(self, conversation_id, turn_id, event_type, payload, **_):
            self.events.append((conversation_id, turn_id, event_type, payload))

        async def complete_evidence(self, conversation_id, turn_id, evidence, **_):
            self.completed.append((conversation_id, turn_id, evidence))

    class Knowledge:
        requests = []

        async def answer(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                answer="grounded", citations=[], abstained=False, trace_id="trace-1"
            )

    conversations = Conversations()
    knowledge = Knowledge()
    turn = conversations.repository.claim_queued
    original = await turn(limit=1)
    value = original[0][1].input_snapshot.value
    value.source_revision_ids = ("revision-1",)
    value.resolved_resources = (
        SimpleNamespace(source_id="src_" + "1" * 32, revision_id="revision-1"),
    )
    conversations.repository.claim_queued = lambda **_: _async_value(original)
    assert await GenerationWorker(conversations, knowledge).run_once(limit=1) == 1
    assert knowledge.requests[0].resource_refs[0].source_id == "src_" + "1" * 32
    assert knowledge.requests[0].resource_refs[0].mode.value == "scope"
    assert conversations.events == [
        ("conversation-1", "turn-1", "answer.delta", {"text": "grounded"})
    ]
    assert conversations.completed[0][2].outcome == "completed"


@pytest.mark.asyncio
async def test_generation_worker_fences_delta_with_the_claimed_lease():
    frozen = SimpleNamespace(
        message="question",
        model_alias="tapper-chat",
        resolved_resources=(SimpleNamespace(source_id="src_" + "1" * 32),),
    )

    class Repository:
        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(value=frozen),
                    ),
                ),
            )

        async def resolve_citations(self, *_args):
            return ()

    class Conversations:
        repository = Repository()
        emitted = []

        async def emit(self, *args, **kwargs):
            self.emitted.append((args, kwargs))

        async def complete_evidence(self, *_args, **_kwargs):
            return None

    class Knowledge:
        async def answer(self, _request):
            return SimpleNamespace(answer="delta", citations=(), abstained=False, trace_id="t")

    conversations = Conversations()
    await GenerationWorker(conversations, Knowledge()).run_once(limit=1)
    assert conversations.emitted[0][1] == {"lease_token": "lease-1"}


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_default_worker_crosses_the_production_answer_service_with_frozen_selection():
    from apps.backend.tests.unit.knowledge import test_answer_service as fixtures

    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.application.assets import validation_asset_seed
    from tap.modules.chat.domain.conversations import FrozenResource, TurnInput, content_digest
    from tap.modules.knowledge.application.demo_policy import build_demo_policy_context

    answers, repository, gateway = fixtures.service()
    repository.scope = VALIDATION_SCOPE

    async def reject_current_reload(_ids):
        raise AssertionError("accepted Turn must not re-read current revisions")

    repository.load_revision_selection = reject_current_reload
    knowledge = KnowledgeHttpService(
        documents=SimpleNamespace(scope=VALIDATION_SCOPE),
        answers=answers,
        citations=SimpleNamespace(scope=VALIDATION_SCOPE),
    )
    frozen = fixtures.ready()
    policy = build_demo_policy_context((frozen,))
    assets = validation_asset_seed(VALIDATION_SCOPE)
    agent = assets.agents[0]
    skill = assets.skills[0]
    turn_input = TurnInput(
        message="What is the rule?",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="tapper-chat",
        source_revision_ids=(frozen.revision_id,),
        resolved_resources=(
            FrozenResource(
                frozen.source_id,
                frozen.document_id,
                frozen.revision_id,
                frozen.source_content_hash,
            ),
        ),
        agent_revision_id=agent.revision_id,
        agent_revision_digest=agent.content_digest,
        skill_revision_ids=(skill.revision_id,),
        skill_revision_digests=(skill.content_digest,),
        agent_system_instruction=agent.system_instruction,
        agent_system_instruction_digest=agent.system_instruction_digest,
        agent_tool_allowlist=tuple(sorted(agent.tool_allowlist)),
        agent_output_schema_json=agent.output_schema_json,
        agent_output_schema_digest=agent.output_schema_digest,
        skill_instruction_templates=(skill.instruction_template,),
        skill_instruction_template_digests=(skill.instruction_template_digest,),
        acl_digest=policy.acl_digest,
        retrieval_policy_digest=content_digest(
            {
                "decisionId": policy.decision_id,
                "policyVersion": policy.policy_version,
                "corpusVersion": policy.active_corpus_version,
            }
        ),
    )

    class Repository:
        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(value=turn_input),
                    ),
                ),
            )

        async def resolve_citations(self, trace_id, citation_ids):
            assert trace_id == "trace-a"
            return tuple(
                CitationEvidence(identity, "sha256:" + "d" * 64) for identity in citation_ids
            )

    class Conversations:
        repository = Repository()

        async def emit(self, *_args, **_kwargs):
            pass

        async def complete_evidence(self, *_args, **_kwargs):
            pass

    assert await GenerationWorker(Conversations(), knowledge).run_once(limit=1) == 1
    assert gateway.requests[0].resource_refs[0].source_id == frozen.source_id
    assert repository.snapshots[0].selected_revisions == (frozen,)
