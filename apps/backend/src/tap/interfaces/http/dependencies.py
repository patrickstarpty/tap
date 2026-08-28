"""HTTP-only service contracts and deferred runtime resolution."""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

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
)
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable


@dataclass(frozen=True, slots=True)
class UploadInput:
    """A bounded byte stream and safe public metadata, never a filesystem path."""

    filename: str
    media_type: str
    content: AsyncIterable[bytes]


class KnowledgeHttpService(Protocol):
    async def upload(self, upload: UploadInput) -> DocumentAccepted: ...

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage: ...

    async def get_document(self, document_id: str) -> DocumentDetail: ...

    async def retry_document(self, document_id: str) -> DocumentAccepted: ...

    async def delete_document(self, document_id: str) -> None: ...

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
