from __future__ import annotations

import pytest
from search_provider_conformance import azure_harness, milvus_harness

from tap.modules.knowledge.ports.errors import SearchUnavailable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "harness_factory", (azure_harness, milvus_harness), ids=("azure", "milvus")
)
@pytest.mark.parametrize(
    "case_id",
    (
        "denied-group",
        "wrong-tenant",
        "wrong-project",
        "over-classification",
        "wrong-environment",
        "wrong-corpus",
        "resource-scope",
    ),
)
async def test_search_provider_acl_conformance(harness_factory, case_id: str) -> None:
    """Removing or changing either channel's mandatory filter would expose denied rows."""
    result = await harness_factory().run_case(case_id)

    assert result.channels == ("bm25", "dense")
    assert result.outbound_filters
    assert set(result.outbound_filters) == {result.expected_filter}
    assert result.provider_rows == ()
    assert result.hits == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "harness_factory", (azure_harness, milvus_harness), ids=("azure", "milvus")
)
async def test_search_provider_allowed_case_maps_one_strict_hit(harness_factory) -> None:
    """Dropping valid results or diverging filters would break provider interchangeability."""
    result = await harness_factory().run_case("allowed")

    assert result.channels == ("bm25", "dense")
    assert set(result.outbound_filters) == {result.expected_filter}
    assert len(result.provider_rows) == 1
    assert len(result.hits) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "harness_factory", (azure_harness, milvus_harness), ids=("azure", "milvus")
)
async def test_search_provider_unavailable_case_is_provider_neutral(harness_factory) -> None:
    """Provider-specific failures would make callers branch on the selected backend."""
    with pytest.raises(SearchUnavailable):
        await harness_factory().run_case("unavailable")
