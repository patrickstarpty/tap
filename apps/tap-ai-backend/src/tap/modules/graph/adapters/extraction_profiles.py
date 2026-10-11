"""The extraction profile digest each graph extraction mode stamps on its jobs."""

from __future__ import annotations

from tap.modules.graph.adapters.fake_extraction import DETERMINISTIC_EXTRACTION_PROFILE_DIGEST
from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_PROFILE_DIGEST

_PROFILE_BY_MODE = {
    "model": GRAPH_EXTRACTION_PROFILE_DIGEST,
    "fake": DETERMINISTIC_EXTRACTION_PROFILE_DIGEST,
}


def extraction_profile_digest(mode: str) -> str:
    """The digest jobs are requested, backfilled and merged under for ``mode``.

    One process configuration uses one digest everywhere (READY projection,
    startup backfill, merge input filter, `graph rebuild`), so fragments extracted
    under the other mode are stale: backfill requeues them and merge ignores them.
    """

    try:
        return _PROFILE_BY_MODE[mode]
    except KeyError:
        raise ValueError("unknown graph extraction mode") from None
