"""Provider-neutral Knowledge search error contracts."""

from __future__ import annotations

from tap.modules.knowledge.adapters.azure_ai_search import (
    SearchBoundsExceeded as AzureSearchBoundsExceeded,
)
from tap.modules.knowledge.adapters.azure_ai_search import (
    SearchUnavailable as AzureSearchUnavailable,
)
from tap.modules.knowledge.ports.errors import SearchBoundsExceeded, SearchUnavailable


def test_azure_uses_provider_neutral_search_errors() -> None:
    """A provider-private error class would prevent provider-neutral handling."""
    assert AzureSearchUnavailable is SearchUnavailable
    assert AzureSearchBoundsExceeded is SearchBoundsExceeded
