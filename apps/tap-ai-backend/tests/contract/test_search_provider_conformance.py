from __future__ import annotations

import pytest
from search_provider_conformance import (
    azure_filter_allows,
    azure_harness,
    conformance_case,
    conformance_guard_clause,
    expected_azure_filter,
    expected_milvus_filter,
    milvus_filter_allows,
    milvus_harness,
    observed_azure_channels,
)

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


@pytest.mark.parametrize(
    ("case_id", "mismatch"),
    (
        ("denied-group", "groups"),
        ("wrong-tenant", "tenant"),
        ("wrong-project", "project"),
        ("over-classification", "classification"),
        ("wrong-environment", "environment"),
        ("wrong-corpus", "corpus"),
        ("resource-scope", "scope"),
    ),
)
def test_each_negative_case_contains_one_real_mismatched_provider_document(
    case_id: str,
    mismatch: str,
) -> None:
    """Returning no fixture rows by case label would make ACL conformance tautological."""
    case = conformance_case(case_id)

    assert len(case.documents) == 1
    assert case.mismatch == mismatch
    assert case.documents[0].row


@pytest.mark.parametrize("provider", ("azure", "milvus"))
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
def test_controlled_provider_exposes_the_negative_row_if_its_guard_is_omitted(
    provider: str,
    case_id: str,
) -> None:
    """A fake that ignores outbound filters would survive production filter omissions."""
    case = conformance_case(case_id)
    document = case.documents[0]
    if provider == "azure":
        expected = expected_azure_filter(case.execution)
        allows = azure_filter_allows
    else:
        expected = expected_milvus_filter(case.execution)
        allows = milvus_filter_allows
    guard = conformance_guard_clause(provider, case)
    weakened = expected.replace(guard, "true", 1)

    assert guard in expected
    assert allows(expected, document, case.execution) is False
    assert allows(weakened, document, case.execution) is True


def test_azure_channels_are_observed_from_the_recorded_request_shape() -> None:
    """Hard-coded channel labels would not detect a missing keyword or dense request."""
    both = {"search_text": "query", "vector_queries": [{"kind": "vector"}]}

    assert observed_azure_channels(both) == ("bm25", "dense")
    assert observed_azure_channels({**both, "search_text": ""}) == ("dense",)
    assert observed_azure_channels({**both, "vector_queries": []}) == ("bm25",)


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
