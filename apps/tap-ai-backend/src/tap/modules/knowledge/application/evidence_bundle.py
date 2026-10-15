"""Serializable snapshot of the local quantities `AuthorizedRetrieval.answer`
produces and reads while generating and verifying one answer. LangGraph nodes
pass this bundle through the MySQL checkpointer between retrieval,
generation and verification; `policy`/`current` (`RetrievalPolicyContext`) are
excluded -- the caller re-derives and re-verifies them, never trusting a
checkpointed copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, cast

from tap.modules.knowledge.application.answer_templates import AssembledAnswer
from tap.modules.knowledge.application.publication import PublicationBinding
from tap.modules.knowledge.application.relation_analysis import RelationContext
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    ContextSnapshot,
    QueryPlan,
    SearchResponse,
)
from tap.platform.serialization.dataclass_json import from_json, to_json

EVIDENCE_BUNDLE_MAX_BYTES = 524_288


class EvidenceBundleTooLarge(ValueError):
    """Raised when an encoded `EvidenceBundle` exceeds `EVIDENCE_BUNDLE_MAX_BYTES`."""


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    request: AnswerRequest
    generation_query: str
    response: SearchResponse
    plan: QueryPlan
    context_snapshot: ContextSnapshot
    publication: PublicationBinding | None
    relation: RelationContext | None
    answer_input: AssembledAnswer | None
    evidence_map: Mapping[str, tuple[str, ...]]
    graph_context: tuple[Mapping[str, object], ...]
    flow_context: Mapping[str, object]
    missing: bool
    relation_first: bool


def encode_bundle(bundle: EvidenceBundle) -> dict[str, object]:
    """Encode `bundle` to a JSON-compatible dict; raise `EvidenceBundleTooLarge`
    if the encoded form exceeds `EVIDENCE_BUNDLE_MAX_BYTES` UTF-8 bytes."""
    encoded = to_json(bundle)
    assert isinstance(encoded, dict)
    size = len(json.dumps(encoded, ensure_ascii=False).encode("utf-8"))
    if size > EVIDENCE_BUNDLE_MAX_BYTES:
        raise EvidenceBundleTooLarge(
            f"evidence bundle is {size} UTF-8 bytes, exceeding the "
            f"{EVIDENCE_BUNDLE_MAX_BYTES}-byte limit"
        )
    return cast(dict[str, object], encoded)


def decode_bundle(data: Mapping[str, object]) -> EvidenceBundle:
    return from_json(EvidenceBundle, data)
