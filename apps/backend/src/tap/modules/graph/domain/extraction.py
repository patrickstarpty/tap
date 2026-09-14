"""Graph extraction request values bound to immutable document revisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.models import GraphSnapshot
from tap.platform.db.project_scope import require_project_scope


@dataclass(frozen=True, slots=True)
class GraphExtractionRequest:
    scope: ProjectScopeContext
    snapshot: GraphSnapshot
    chunks: tuple[Mapping[str, object], ...]
    model_alias: str
    idempotency_key: str

    def __post_init__(self) -> None:
        scope = require_project_scope(self.scope)
        if self.snapshot.project_id != scope.project_id:
            raise ValueError("graph extraction is outside Project scope")
        if not self.chunks:
            raise ValueError("graph extraction requires document chunks")
        if not self.model_alias.strip() or not self.idempotency_key.strip():
            raise ValueError("graph extraction model and idempotency key are required")
