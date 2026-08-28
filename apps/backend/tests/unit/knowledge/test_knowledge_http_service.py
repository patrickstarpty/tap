from __future__ import annotations

import pytest

from tap.contracts.http import RetrievalSearchRequest
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.knowledge.domain.models import (
    ModelCallProvenance,
    RetrievalProfileId,
    SearchRequest,
    SearchResponse,
)


class Searches:
    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        return SearchResponse(
            trace_id="trace-a",
            query_plan_id="plan-a",
            context_snapshot_id="context-a",
            corpus_version="athena-demo-v1",
            retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
            evidence=(),
            embedding_provenance=ModelCallProvenance("athena-embedding", None),
        )


@pytest.mark.asyncio
async def test_internal_search_maps_through_the_composed_service_without_an_http_route() -> None:
    searches = Searches()
    service = KnowledgeHttpService(
        documents=object(),  # type: ignore[arg-type]
        answers=object(),  # type: ignore[arg-type]
        citations=object(),  # type: ignore[arg-type]
        searches=searches,
    )

    response = await service.search(
        RetrievalSearchRequest.model_validate(
            {
                "query": "What is the rule?",
                "resourceRefs": [{"family": "doc", "sourceId": "doc-a", "mode": "scope"}],
            }
        )
    )

    assert response.hits == []
    assert len(searches.requests) == 1
    assert searches.requests[0].resource_refs[0].source_id == "doc-a"
