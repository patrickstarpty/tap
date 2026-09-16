"""Deterministic cross-language retrieval and citation contracts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace

import pytest

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import (
    Classification,
    ProjectPolicy,
    ResourceGrant,
    RetrievalPolicyContext,
    VerifiedSubjectFacts,
)
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    ContentRole,
    DocumentAnchor,
    Evidence,
    IndexRevision,
    ResourceMode,
    ResourceRef,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
    RedactionResult,
    SearchExecution,
    SearchHit,
)

_EMBEDDING_MODEL = "semantic-pair-v1"
_SELECTED_SOURCE = "document:selected"
_UNSELECTED_SOURCE = "document:unselected"
_SELECTED_REVISION = "rev-selected"
_UNSELECTED_REVISION = "rev-unselected"
_CORPUS = "corpus-bilingual-v1"


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class SemanticPairEmbeddings:
    embedding_model_id = _EMBEDDING_MODEL
    embedding_dimension = 3

    def __init__(self, vector: tuple[float, float, float]) -> None:
        self._vector = vector
        self.queries: list[str] = []

    async def embed(self, query: str) -> Embedding:
        self.queries.append(query)
        return Embedding(
            vector=self._vector,
            model_id=self.embedding_model_id,
            provider_request_id=None,
        )


class SelectedDocumentSearch:
    def __init__(
        self,
        *,
        source_text: str,
        source_vector: tuple[float, float, float],
        unselected_text: str,
        unselected_vector: tuple[float, float, float],
        max_hits: int | None = None,
    ) -> None:
        self._candidates = (
            (_hit(source_text, selected=True), source_vector),
            (_hit(unselected_text, selected=False), unselected_vector),
        )
        self._max_hits = max_hits
        self.returned_hits: tuple[SearchHit, ...] = ()
        self.eligible_source_ids: tuple[str, ...] = ()
        self.unselected_hits = 0

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        scoped_resources = tuple(
            resource for resource in execution.plan.resources if resource.mode is ResourceMode.SCOPE
        )
        eligible = tuple(
            candidate
            for candidate in self._candidates
            if not scoped_resources
            or any(
                candidate[0].family is resource.family
                and candidate[0].source.source_id == resource.source_id
                and candidate[0].source.revision_kind is resource.revision_kind
                and candidate[0].source.revision == resource.revision
                and candidate[0].source.source_content_hash == resource.source_content_hash
                for resource in scoped_resources
            )
        )
        self.eligible_source_ids = tuple(candidate[0].source.source_id for candidate in eligible)
        ranked = sorted(
            eligible,
            key=lambda candidate: -_cosine(execution.query_vector, candidate[1]),
        )
        if self._max_hits is not None:
            ranked = ranked[: self._max_hits]
        self.returned_hits = tuple(
            replace(candidate, local_rank=rank)
            for rank, (candidate, _vector) in enumerate(ranked, start=1)
        )
        self.unselected_hits = sum(
            hit.source.source_id == _UNSELECTED_SOURCE for hit in self.returned_hits
        )
        return self.returned_hits


class BilingualGroundedAnswers:
    def __init__(self, *, answer: str, selected_text: str) -> None:
        self._answer = answer
        self._selected_text = selected_text
        self.evidence: tuple[Evidence, ...] = ()

    async def answer(
        self,
        query: str,
        evidence: tuple[Evidence, ...],
        profile_id: str,
    ) -> AnswerGeneration:
        del query
        assert len(evidence) == 1
        assert evidence[0].content == self._selected_text
        self.evidence = evidence
        return AnswerGeneration(
            text=self._answer,
            claims=(GeneratedClaim(text=self._answer, evidence_labels=("S1",)),),
            model_id="bilingual-grounded-answer-v1",
            profile_id=profile_id,
            provider_request_id=None,
        )


class CurrentPolicyVerifier:
    async def verify_current(
        self,
        expected: RetrievalPolicyContext,
    ) -> RetrievalPolicyContext:
        return expected


class PassthroughRedactor:
    async def redact(self, text: str) -> RedactionResult:
        return RedactionResult(sanitized_text=text, redaction_version="passthrough-v1")


def _hit(content: str, *, selected: bool) -> SearchHit:
    source_id = _SELECTED_SOURCE if selected else _UNSELECTED_SOURCE
    revision = _SELECTED_REVISION if selected else _UNSELECTED_REVISION
    suffix = "1" if selected else "2"
    return SearchHit(
        family=SourceFamily.DOC,
        chunk_id="h_" + suffix * 64,
        logical_chunk_id="h_" + suffix * 64,
        title="Selected source" if selected else "Unselected source",
        content=content,
        source=SourceRevisionRef(
            source_id=source_id,
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision=revision,
            source_content_hash=_digest(content),
            anchor=DocumentAnchor(page=1),
        ),
        chunk_content_hash=_digest(content),
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(
            physical_index="kb-doc-v1-bilingual",
            schema_version="search-schema-v1",
            corpus_version=_CORPUS,
        ),
        embedding_model_version=_EMBEDDING_MODEL,
        score=1.0,
    )


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    numerator = sum(x * y for x, y in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm)


def _policy(
    source_text: str,
    *,
    unselected_text: str | None = None,
) -> RetrievalPolicyContext:
    subject = VerifiedSubjectFacts(
        tenant_id="tenant-bilingual",
        user_id="user-bilingual",
        group_ids=frozenset({"readers"}),
        roles=frozenset({"reader"}),
        token_verified=True,
    )
    project = ProjectPolicy(
        tenant_id="tenant-bilingual",
        project_id="project-bilingual",
        permission_granted=True,
        allowed_group_ids=frozenset({"readers"}),
        classification_ceiling=Classification.CONFIDENTIAL,
        allowed_environments=frozenset({"production"}),
        allowed_source_families=frozenset({"doc"}),
        active_corpus_version=_CORPUS,
        acl_digest="sha256:acl-bilingual",
        policy_version="policy-bilingual-v1",
        decision_id="decision-bilingual-v1",
        resource_grants=tuple(
            [
                ResourceGrant(
                    family="doc",
                    source_id=_SELECTED_SOURCE,
                    revision_kind="blob_version",
                    revision=_SELECTED_REVISION,
                    source_content_hash=_digest(source_text),
                    allow_all_anchors=True,
                )
            ]
            + (
                [
                    ResourceGrant(
                        family="doc",
                        source_id=_UNSELECTED_SOURCE,
                        revision_kind="blob_version",
                        revision=_UNSELECTED_REVISION,
                        source_content_hash=_digest(unselected_text),
                        allow_all_anchors=True,
                    )
                ]
                if unselected_text is not None
                else []
            )
        ),
    )
    return build_retrieval_policy_context(
        subject,
        project,
        requested_tenant_id="tenant-bilingual",
        requested_project_id="project-bilingual",
    )


def _request(query: str, *, include_unselected: bool = False) -> AnswerRequest:
    resources = [
        ResourceRef(
            family=SourceFamily.DOC,
            source_id=_SELECTED_SOURCE,
            mode=ResourceMode.SCOPE,
            requested_revision=_SELECTED_REVISION,
        )
    ]
    if include_unselected:
        resources.append(
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=_UNSELECTED_SOURCE,
                mode=ResourceMode.SCOPE,
                requested_revision=_UNSELECTED_REVISION,
            )
        )
    return AnswerRequest(
        query=query,
        source_families=(SourceFamily.DOC,),
        resource_refs=tuple(resources),
    )


def _api(
    *,
    search: SelectedDocumentSearch,
    embeddings: SemanticPairEmbeddings,
    answers: BilingualGroundedAnswers,
) -> KnowledgeAPI:
    identifiers = iter(f"bilingual-{index}" for index in range(32))
    return KnowledgeAPI(
        search=search,
        embeddings=embeddings,
        answers=answers,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=identifiers.__next__,
    )


@pytest.mark.parametrize(
    ("query", "source_text", "unselected_text", "answer", "query_vector", "other_vector"),
    (
        (
            "退款审批需要什么条件？",
            "Refunds require two approvers.",
            "未选择文档称退款只需一名审批人。",
            "退款需要两名审批人。",
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        ),
        (
            "What is the rollback SLA?",
            "回滚 SLA 为 30 分钟。",
            "The unselected document claims rollback takes ninety minutes.",
            "The rollback SLA is 30 minutes.",
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        ),
        (
            "Explain 发布 freeze window",
            "The 发布 freeze window starts Friday.",
            "未选 mixed 文档 claims freeze starts Monday.",
            "The freeze starts Friday.",
            (0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0),
        ),
    ),
    ids=("zh-to-en", "en-to-zh", "mixed"),
)
@pytest.mark.asyncio
async def test_cross_language_query_keeps_selected_source_and_citation(
    query: str,
    source_text: str,
    unselected_text: str,
    answer: str,
    query_vector: tuple[float, float, float],
    other_vector: tuple[float, float, float],
) -> None:
    embeddings = SemanticPairEmbeddings(query_vector)
    search = SelectedDocumentSearch(
        source_text=source_text,
        source_vector=query_vector,
        unselected_text=unselected_text,
        unselected_vector=other_vector,
    )
    answers = BilingualGroundedAnswers(answer=answer, selected_text=source_text)

    response = await _api(search=search, embeddings=embeddings, answers=answers).answer(
        _request(query),
        _policy(source_text),
    )

    assert response.abstained is False
    assert response.answer == answer
    assert response.claims and response.claims[0].citation_ids
    assert {item.source.revision for item in response.citations} == {_SELECTED_REVISION}
    assert {item.source.source_id for item in search.returned_hits} == {_SELECTED_SOURCE}
    assert {item.source.source_id for item in answers.evidence} == {_SELECTED_SOURCE}
    assert search.unselected_hits == 0
    assert _UNSELECTED_REVISION not in {citation.source.revision for citation in response.citations}
    assert all(unselected_text not in claim.text for claim in response.claims)
    assert embeddings.queries == [query]


@pytest.mark.asyncio
async def test_cross_language_vector_selects_match_from_two_authorized_candidates() -> None:
    query = "退款审批需要几名审批人？"
    source_text = "Refunds require two approvers."
    unselected_text = "获准的另一份文档只讨论办公区清洁安排。"
    answer = "退款需要两名审批人。"
    query_vector = (1.0, 0.0, 0.0)
    embeddings = SemanticPairEmbeddings(query_vector)
    search = SelectedDocumentSearch(
        source_text=source_text,
        source_vector=query_vector,
        unselected_text=unselected_text,
        unselected_vector=(0.0, 1.0, 0.0),
        max_hits=1,
    )
    answers = BilingualGroundedAnswers(answer=answer, selected_text=source_text)

    response = await _api(search=search, embeddings=embeddings, answers=answers).answer(
        _request(query, include_unselected=True),
        _policy(source_text, unselected_text=unselected_text),
    )

    assert response.answer == answer
    assert set(search.eligible_source_ids) == {_SELECTED_SOURCE, _UNSELECTED_SOURCE}
    assert {item.source.source_id for item in search.returned_hits} == {_SELECTED_SOURCE}
    assert {item.source.source_id for item in answers.evidence} == {_SELECTED_SOURCE}
    assert {item.source.revision for item in response.citations} == {_SELECTED_REVISION}
    assert _UNSELECTED_REVISION not in {citation.source.revision for citation in response.citations}
