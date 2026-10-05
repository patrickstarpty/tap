"""Freeze a conversation's selected knowledge revisions into turn resources and digests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tap.modules.chat.domain.conversations import FrozenResource, content_digest


@dataclass(frozen=True, slots=True)
class FrozenSelection:
    resolved_resources: tuple[FrozenResource, ...]
    acl_digest: str
    retrieval_policy_digest: str


async def freeze_conversation_selection(
    knowledge: Any,
    *,
    source_revision_ids: tuple[str, ...],
    document_revision_ids: tuple[str, ...] = (),
) -> FrozenSelection:
    """Resolve selected revisions under current authority, exactly as a chat turn does."""

    selected_revision_ids = (*source_revision_ids, *document_revision_ids)
    if not selected_revision_ids:
        return FrozenSelection(
            (),
            content_digest({"mode": "model-only", "resources": []}),
            content_digest({"mode": "model-only", "retrieval": "not-selected"}),
        )
    revisions, policy = await knowledge.resolve_conversation_selection(selected_revision_ids)
    return FrozenSelection(
        tuple(
            FrozenResource(
                source_id=item.source_id or item.document_id,
                document_id=item.document_id,
                revision_id=item.revision_id,
                source_content_hash=item.source_content_hash,
                source_revision_id=(
                    item.revision_id if item.revision_id in source_revision_ids else None
                ),
                document_revision_id=item.revision_id,
                label=(
                    getattr(item, "source_name", None)
                    or getattr(item, "filename", None)
                    or item.source_id
                    or item.document_id
                ),
            )
            for item in revisions
        ),
        policy.acl_digest,
        content_digest(
            {
                "decisionId": policy.decision_id,
                "policyVersion": policy.policy_version,
                "corpusVersion": policy.active_corpus_version,
            }
        ),
    )
