"""Provider-neutral failures from the Knowledge search port."""

from __future__ import annotations


class SearchError(Exception):
    """A search provider could not complete a contract-valid execution."""


class SearchUnavailable(SearchError):
    """The selected provider is unavailable or returned invalid data."""


class SearchBoundsExceeded(SearchError):
    """A trusted execution exceeds provider-neutral safety bounds."""


SEARCH_UNAVAILABLE_TYPE = "https://tap.example/problems/search-unavailable"
SEARCH_EXECUTION_REJECTED_TYPE = "https://tap.example/problems/search-execution-rejected"
