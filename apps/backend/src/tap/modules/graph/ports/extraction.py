"""Provider-neutral graph extraction port."""

from typing import Protocol

from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import GraphSnapshotDraft


class GraphExtractionPort(Protocol):
    async def extract(self, request: GraphExtractionRequest) -> GraphSnapshotDraft: ...
