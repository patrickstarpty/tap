"""Provider-neutral graph extraction port."""

from typing import Protocol

from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import GraphSnapshotDraft


class GraphExtractionPort(Protocol):
    async def extract(self, request: GraphExtractionRequest) -> GraphSnapshotDraft | None:
        """Extract one batch's facts, or ``None`` if nothing was grounded.

        ``None`` signals a successful batch that simply has no facts to contribute
        (all-boilerplate chunks, or a relation-less span of the document) --
        distinct from raising, which signals a batch failure to retry.
        """
        ...
