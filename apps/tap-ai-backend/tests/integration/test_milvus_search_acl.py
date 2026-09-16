"""Opt-in correctness gate against the pinned real Milvus server."""

from __future__ import annotations

import os

import pytest

RUN_REAL_MILVUS = os.getenv("TAP_RUN_MILVUS_INTEGRATION") == "1"
if not RUN_REAL_MILVUS:
    pytest.skip(
        "real Milvus suite is run by make test-milvus",
        allow_module_level=True,
    )

REQUIRED_ENV = (
    "MILVUS_URI",
    "MILVUS_DATABASE",
    "MILVUS_READER_USERNAME",
    "MILVUS_READER_PASSWORD",
    "MILVUS_WRITER_USERNAME",
    "MILVUS_WRITER_PASSWORD",
    "MILVUS_PROVISIONER_USERNAME",
    "MILVUS_PROVISIONER_PASSWORD",
)
missing = tuple(name for name in REQUIRED_ENV if not os.getenv(name))
if missing:
    pytest.fail(
        "missing required real Milvus settings: " + ", ".join(missing),
        pytrace=False,
    )

from milvus_runtime import PublishedFixture  # noqa: E402


@pytest.fixture
def published_fixture() -> PublishedFixture:
    return PublishedFixture.from_environment()


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", ("refund-allowed", "payment-global-allowed"))
async def test_real_milvus_allowed_cases_reach_all_three_surfaces_with_top_ten_bound(
    published_fixture: PublishedFixture,
    case_id: str,
) -> None:
    result = await published_fixture.run_case(case_id)
    expected = set(published_fixture.expected_source_ids(case_id))
    provider_sources = {row["source_id"] for row in result.provider_rows}
    hit_sources = {hit.source.source_id for hit in result.search_hits}
    citation_sources = {citation.source.source_id for citation in result.citations}

    assert expected <= provider_sources == hit_sources == citation_sources
    assert 1 <= len(result.provider_rows) <= 10
    assert len(result.search_hits) == len(result.citations)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id",
    (
        "payment-wrong-group",
        "payment-wrong-project",
        "payment-wrong-tenant",
        "security-over-classification",
        "release-wrong-environment",
        "wrong-corpus",
    ),
)
async def test_real_milvus_denied_cases_return_no_rows_hits_or_citations(
    published_fixture: PublishedFixture,
    case_id: str,
) -> None:
    result = await published_fixture.run_case(case_id)

    assert result.provider_rows == ()
    assert result.search_hits == ()
    assert result.citations == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ("bm25", "dense"))
async def test_real_milvus_each_search_channel_observes_the_compiled_acl_filter(
    published_fixture: PublishedFixture,
    channel: str,
) -> None:
    result = await published_fixture.run_channel("refund-allowed", channel)

    assert "blob:fixture/payment/refund" in result.source_ids
    assert result.filter_expression == published_fixture.last_filter_expression


@pytest.mark.asyncio
async def test_real_milvus_hybrid_and_direct_physical_read_share_one_subtree_filter(
    published_fixture: PublishedFixture,
) -> None:
    result = await published_fixture.run_case("payment-subtree-card-only")

    assert {row["source_id"] for row in result.provider_rows} == {"blob:fixture/payment/card"}
    assert {hit.source.source_id for hit in result.search_hits} == {"blob:fixture/payment/card"}
    assert {citation.source.source_id for citation in result.citations} == {
        "blob:fixture/payment/card"
    }
    assert published_fixture.last_channel_filters == (
        published_fixture.last_filter_expression,
        published_fixture.last_filter_expression,
    )
    assert published_fixture.last_direct_filter == published_fixture.last_filter_expression
    assert published_fixture.last_physical_collection in {
        hit.index_revision.physical_index for hit in result.search_hits
    }


@pytest.mark.asyncio
async def test_real_milvus_deleted_rows_are_absent_from_every_surface(
    published_fixture: PublishedFixture,
) -> None:
    result = await published_fixture.run_case("deleted-archive")

    assert result.provider_rows == ()
    assert result.search_hits == ()
    assert result.citations == ()


@pytest.mark.asyncio
async def test_real_milvus_acl_tightening_is_visible_before_physical_delete(
    published_fixture: PublishedFixture,
) -> None:
    result = await published_fixture.run_temporary_revocation("refund-allowed")

    assert result.provider_rows == ()
    assert result.search_hits == ()
    assert result.citations == ()


@pytest.mark.asyncio
async def test_real_milvus_hits_and_citations_preserve_manifest_provenance(
    published_fixture: PublishedFixture,
) -> None:
    result = await published_fixture.run_case("refund-allowed")
    manifest_by_source = {chunk.source_id: chunk for chunk in published_fixture.manifest.chunks}

    for hit, citation in zip(result.search_hits, result.citations, strict=True):
        expected = manifest_by_source[hit.source.source_id]
        assert hit.chunk_id == citation.chunk_id == expected.chunk_id
        assert hit.logical_chunk_id == citation.logical_chunk_id == expected.logical_chunk_id
        assert hit.chunk_content_hash == citation.chunk_content_hash == expected.chunk_content_hash
        assert hit.source.revision == expected.source_revision
        assert hit.source.source_content_hash == expected.source_content_hash
