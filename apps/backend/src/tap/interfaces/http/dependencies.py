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
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable


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

    async def citation(self, citation_id: str) -> CitationPreview: ...


class ReadinessHttpService(Protocol):
    async def check(self) -> ReadyHealth: ...


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


def knowledge_service(request: Request) -> KnowledgeHttpService:
    services = getattr(request.app.state, "http_services", None)
    service = services.knowledge if isinstance(services, HttpServices) else None
    if service is None:
        raise KnowledgeRuntimeUnavailable
    return service


def readiness_service(request: Request) -> ReadinessHttpService:
    services = getattr(request.app.state, "http_services", None)
    service = services.readiness if isinstance(services, HttpServices) else None
    return service or _UNCONFIGURED_READINESS


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
