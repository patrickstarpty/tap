"""Idempotent extraction and atomic graph publication."""

from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import GraphSnapshot
from tap.modules.graph.ports.extraction import GraphExtractionPort
from tap.modules.graph.ports.store import GraphStorePort


class GraphExtractionService:
    def __init__(self, *, store: GraphStorePort, extractor: GraphExtractionPort) -> None:
        self._store = store
        self._extractor = extractor
        self._completed: dict[tuple[str, str], GraphSnapshot] = {}

    async def execute(self, request: GraphExtractionRequest) -> GraphSnapshot:
        key = (request.scope.project_id, request.idempotency_key)
        completed = self._completed.get(key)
        if completed is not None:
            return completed
        existing = await self._store.get_snapshot(request.scope, request.snapshot.snapshot_id)
        if existing is not None:
            self._completed[key] = existing
            return existing
        draft = await self._extractor.extract(request)
        published = await self._store.publish(request.scope, draft)
        self._completed[key] = published
        return published
