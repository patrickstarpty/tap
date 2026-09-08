"""Public projection of the server-governed model catalog."""

from __future__ import annotations

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import ModelDescriptor, ModelGatewayUnavailable
from tap.modules.ai.ports.gateway import ModelGateway


class ModelCatalog:
    def __init__(
        self,
        gateway: ModelGateway,
        *,
        scope: ProjectScopeContext | None = None,
        default_alias: str = "tapper-chat",
    ) -> None:
        self._gateway = gateway
        self._scope = scope
        self.default_alias = default_alias

    @property
    def scope(self) -> ProjectScopeContext:
        if self._scope is None:
            raise ModelGatewayUnavailable()
        return self._scope

    async def list_models(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]:
        items = tuple(item for item in await self._gateway.catalog(scope) if item.enabled)
        if not any(item.alias == self.default_alias for item in items):
            raise ModelGatewayUnavailable()
        return items
