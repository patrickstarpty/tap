"""HTTP-only service contracts and deferred runtime resolution."""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Protocol

from fastapi import Header, Request
from fastapi.exceptions import RequestValidationError

from tap.contracts.http import (
    CitationPreview,
    DocumentAccepted,
    DocumentDetail,
    DocumentPage,
    HealthComponent,
    HealthComponentName,
    HealthComponentState,
    HealthRemediationCode,
    KnowledgeFlowchart,
    KnowledgeFlowchartCorrection,
    KnowledgePublicationDetail,
    KnowledgePublicationPage,
    KnowledgeReviewDecisionPage,
    KnowledgeReviewDetail,
    KnowledgeReviewHistoryPage,
    KnowledgeReviewInventory,
    KnowledgeReviewItemComparison,
    KnowledgeReviewPage,
    KnowledgeReviewSummary,
    PublishedKnowledgeSourcePage,
    ReadyHealth,
    RetrievalAnswerRequest,
    RetrievalAnswerResponse,
    SourceAccepted,
    SourceDetail,
    SourcePage,
    SourceRetryRequest,
)
from tap.modules.access.application.ports import AuthorizationPolicy, ScopeProvider
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import RetrievalPolicyContext
from tap.modules.ai.domain.assets import AiAgentRevision, SkillRevision
from tap.modules.ai.domain.models import ModelDescriptor
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.graph.ports.store import GraphStorePort
from tap.modules.knowledge.ports.answers import ReadyDocumentRevision
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable
from tap.modules.test_management.application.plans import TestPlanApplication


@dataclass(frozen=True, slots=True)
class UploadInput:
    """A bounded byte stream and safe public metadata, never a filesystem path."""

    filename: str
    media_type: str
    content: AsyncIterable[bytes]


class KnowledgeHttpService(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def upload_source(
        self, upload: UploadInput, key: str, correlation: str
    ) -> SourceAccepted: ...
    async def list_sources(self, cursor: str | None, limit: int) -> SourcePage: ...
    async def get_source(self, source_id: str, cursor: str | None, limit: int) -> SourceDetail: ...
    async def retry_source(
        self, source_id: str, body: SourceRetryRequest, key: str, correlation: str
    ) -> SourceAccepted: ...
    async def delete_source(self, source_id: str, key: str, correlation: str) -> None: ...

    async def upload(
        self, upload: UploadInput, key: str | None = None, correlation: str | None = None
    ) -> DocumentAccepted: ...

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage: ...

    async def get_document(self, document_id: str) -> DocumentDetail: ...

    async def retry_document(
        self, document_id: str, key: str | None = None, correlation: str | None = None
    ) -> DocumentAccepted: ...

    async def delete_document(
        self, document_id: str, key: str | None = None, correlation: str | None = None
    ) -> None: ...

    async def answer(self, request: RetrievalAnswerRequest) -> RetrievalAnswerResponse: ...
    async def resolve_conversation_selection(
        self, revision_ids: tuple[str, ...]
    ) -> tuple[tuple[ReadyDocumentRevision, ...], RetrievalPolicyContext]: ...

    async def citation(self, citation_id: str) -> CitationPreview: ...
    async def historical_citation(self, citation_id: str) -> CitationPreview: ...


class ReadinessHttpService(Protocol):
    async def check(self) -> ReadyHealth: ...


class KnowledgeReviewHttpService(Protocol):
    async def get_flowchart(self, review_id: str) -> KnowledgeFlowchart: ...
    async def correct_flowchart(
        self, review_id: str, graph: dict[str, object], expected_version: int
    ) -> KnowledgeFlowchartCorrection: ...

    @property
    def scope(self) -> ProjectScopeContext: ...

    async def resolve_open_review(self, document_id: str, source_revision_id: str) -> str: ...
    async def list_reviews(
        self,
        source_revision_id: str | None,
        limit: int = 50,
        after_review_id: str | None = None,
    ) -> KnowledgeReviewPage: ...
    async def open_review(
        self,
        document_id: str,
        source_revision_id: str,
        authorized_review_id: str,
        key: str,
    ) -> KnowledgeReviewDetail: ...
    async def get_review(self, review_id: str) -> KnowledgeReviewDetail: ...
    async def get_review_inventory(
        self, review_id: str, limit: int = 100, after_item_id: str | None = None
    ) -> KnowledgeReviewInventory: ...
    async def list_review_decision_history(
        self, review_id: str, limit: int = 100, after_version: int | None = None
    ) -> KnowledgeReviewDecisionPage: ...
    async def list_review_history(
        self, review_id: str, limit: int = 100, after_version: int | None = None
    ) -> KnowledgeReviewHistoryPage: ...
    async def list_review_publications(
        self,
        review_id: str,
        limit: int = 100,
        after_publication_id: str | None = None,
    ) -> KnowledgePublicationPage: ...
    async def get_publication(self, publication_id: str) -> KnowledgePublicationDetail: ...
    async def get_current_publication(self) -> KnowledgePublicationDetail: ...
    async def list_published_sources(self) -> PublishedKnowledgeSourcePage: ...
    async def compare_review_item(
        self, review_id: str, item_id: str
    ) -> KnowledgeReviewItemComparison: ...
    async def read_original_image(self, review_id: str, item_id: str) -> tuple[bytes, str]: ...
    async def update_item_decision(
        self, review_id: str, item_id: str, body: dict[str, object], expected_version: int
    ) -> KnowledgeReviewDetail: ...
    async def return_review(
        self, review_id: str, expected_version: int
    ) -> KnowledgeReviewDetail: ...
    async def submit_review(
        self, review_id: str, expected_version: int
    ) -> KnowledgeReviewDetail: ...

    async def approve_review(
        self, review_id: str, expected_version: int
    ) -> KnowledgeReviewSummary: ...
    async def publish_review(
        self, review_id: str, generation: str, expected_version: int, key: str
    ) -> KnowledgePublicationDetail: ...
    async def withdraw_publication(
        self, publication_id: str, expected_version: int, key: str
    ) -> KnowledgePublicationDetail: ...


class ModelCatalogHttpService(Protocol):
    @property
    def default_alias(self) -> str: ...

    @property
    def scope(self) -> ProjectScopeContext: ...

    async def list_models(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]: ...


class AssetCatalogHttpService(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def list_agents(self, scope: ProjectScopeContext) -> tuple[AiAgentRevision, ...]: ...
    async def get_agent(self, scope: ProjectScopeContext, revision_id: str) -> AiAgentRevision: ...
    async def list_skills(self, scope: ProjectScopeContext) -> tuple[SkillRevision, ...]: ...
    async def get_skill(self, scope: ProjectScopeContext, revision_id: str) -> SkillRevision: ...


class _UnconfiguredReadiness:
    async def check(self) -> ReadyHealth:
        return ReadyHealth(
            status="unready",
            components=[
                HealthComponent(name=name, state=HealthComponentState.FAILED, remediation_code=code)
                for name, code in (
                    (HealthComponentName.MYSQL, HealthRemediationCode.START_MYSQL),
                    (HealthComponentName.REDIS, HealthRemediationCode.START_REDIS),
                    (HealthComponentName.BLOB, HealthRemediationCode.START_BLOB),
                    (HealthComponentName.MILVUS, HealthRemediationCode.START_MILVUS),
                    (HealthComponentName.MODELS, HealthRemediationCode.CONFIGURE_MODELS),
                )
            ],
        )


_UNCONFIGURED_READINESS = _UnconfiguredReadiness()


@dataclass(frozen=True, slots=True)
class HttpServices:
    """Optional service assembly used by routes without eager infrastructure startup."""

    knowledge: KnowledgeHttpService | None = None
    readiness: ReadinessHttpService | None = None
    scope_provider: ScopeProvider | None = None
    authorization_policy: AuthorizationPolicy | None = None
    scope: ProjectScopeContext | None = None
    model_catalog: ModelCatalogHttpService | None = None
    asset_catalog: AssetCatalogHttpService | None = None
    conversations: ConversationService | None = None
    graph: GraphStorePort | None = None
    test_plans: TestPlanApplication | None = None
    knowledge_reviews: KnowledgeReviewHttpService | None = None
    insights_explanation: object | None = None
    insights_knowledge_search: object | None = None
    insights_publication_authority: object | None = None


class GraphUnavailable(Exception):
    """The dedicated Graph runtime is unavailable; never represent this as an empty graph."""


def graph_service(request: Request) -> GraphStorePort:
    services = getattr(request.app.state, "http_services", None)
    service = services.graph if isinstance(services, HttpServices) else None
    if service is None:
        raise GraphUnavailable
    return service


def test_plan_service(request: Request) -> TestPlanApplication:
    services = getattr(request.app.state, "http_services", None)
    service = services.test_plans if isinstance(services, HttpServices) else None
    if service is None:
        raise KnowledgeRuntimeUnavailable
    return service


def knowledge_service(request: Request) -> KnowledgeHttpService:
    services = getattr(request.app.state, "http_services", None)
    service = services.knowledge if isinstance(services, HttpServices) else None
    if service is None:
        raise KnowledgeRuntimeUnavailable
    return service


def knowledge_review_service(request: Request) -> KnowledgeReviewHttpService:
    services = getattr(request.app.state, "http_services", None)
    service = services.knowledge_reviews if isinstance(services, HttpServices) else None
    if service is None:
        raise KnowledgeRuntimeUnavailable
    return service


def readiness_service(request: Request) -> ReadinessHttpService:
    services = getattr(request.app.state, "http_services", None)
    service = services.readiness if isinstance(services, HttpServices) else None
    return service or _UNCONFIGURED_READINESS


def model_catalog_service(request: Request) -> ModelCatalogHttpService:
    services = getattr(request.app.state, "http_services", None)
    service = services.model_catalog if isinstance(services, HttpServices) else None
    if service is None:
        raise KnowledgeRuntimeUnavailable
    return service


def asset_catalog_service(request: Request) -> AssetCatalogHttpService:
    services = getattr(request.app.state, "http_services", None)
    service = services.asset_catalog if isinstance(services, HttpServices) else None
    if service is None:
        raise KnowledgeRuntimeUnavailable
    return service


def conversation_service(request: Request) -> ConversationService:
    services = getattr(request.app.state, "http_services", None)
    service = services.conversations if isinstance(services, HttpServices) else None
    if service is None:
        raise KnowledgeRuntimeUnavailable
    return service


def source_command_key(
    request: Request, idempotency_key: str = Header(min_length=1, max_length=128)
) -> str:
    if len(request.headers.getlist("idempotency-key")) != 1 or not idempotency_key.strip():
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("header", "idempotency-key"),
                    "msg": "One nonblank Idempotency-Key is required",
                    "input": None,
                }
            ]
        )
    return idempotency_key


def review_expected_version(request: Request, if_match: str = Header()) -> int:
    values = request.headers.getlist("if-match")
    if (
        len(values) != 1
        or len(if_match) > 12
        or not if_match.startswith('"')
        or not if_match.endswith('"')
    ):
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("header", "if-match"),
                    "msg": "If-Match must contain one quoted positive review version",
                    "input": if_match,
                }
            ]
        )
    raw = if_match[1:-1]
    if not raw.isascii() or not raw.isdigit() or raw.startswith("0"):
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("header", "if-match"),
                    "msg": "If-Match must contain one quoted positive review version",
                    "input": if_match,
                }
            ]
        )
    return int(raw)
