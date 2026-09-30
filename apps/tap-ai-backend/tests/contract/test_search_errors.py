"""Provider-neutral Knowledge search error contracts."""

from __future__ import annotations

from tap.modules.knowledge.adapters.milvus import filter as milvus_filter
from tap.modules.knowledge.adapters.milvus import transport as milvus_transport
from tap.modules.knowledge.ports.errors import SearchBoundsExceeded, SearchUnavailable


def test_milvus_uses_provider_neutral_search_errors() -> None:
    """A provider-private error class would prevent provider-neutral handling."""
    assert milvus_transport.SearchUnavailable is SearchUnavailable
    assert milvus_filter.SearchBoundsExceeded is SearchBoundsExceeded
