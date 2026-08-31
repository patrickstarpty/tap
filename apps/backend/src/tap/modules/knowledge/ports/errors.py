"""Provider-neutral failures from the Knowledge search port."""

from __future__ import annotations


class SearchError(Exception):
    """A search provider could not complete a contract-valid execution."""


class SearchUnavailable(SearchError):
    """The selected provider is unavailable or returned invalid data."""


class SearchBoundsExceeded(SearchError):
    """A trusted execution exceeds provider-neutral safety bounds."""


class ModelUnavailable(Exception):
    """The selected model route is unavailable or returned invalid data."""


class AnswerUnavailable(ModelUnavailable):
    """The selected answer backend is unavailable or returned invalid grounded output."""


class KnowledgeRuntimeUnavailable(Exception):
    """A provider-neutral knowledge repository or artifact runtime is unavailable."""


class ArtifactError(Exception):
    """A provider-neutral document artifact read could not satisfy its contract."""


class ArtifactIntegrityFailure(ArtifactError):
    """An immutable artifact was missing, malformed, or failed identity verification."""


class ArtifactUnavailable(ArtifactError):
    """The artifact provider could not complete an otherwise valid operation."""


class IndexError(Exception):
    """A document index operation failed without exposing provider details."""


class IndexUnavailable(IndexError):
    """The document index is unavailable or has incompatible metadata."""


class IndexFenced(IndexError):
    """A durable deletion fence rejected publication for this revision."""


class IndexReconciliationFailed(IndexError):
    """Persisted index rows did not match the requested projection."""


SEARCH_UNAVAILABLE_TYPE = "https://tap.example/problems/search-unavailable"
SEARCH_EXECUTION_REJECTED_TYPE = "https://tap.example/problems/search-execution-rejected"
