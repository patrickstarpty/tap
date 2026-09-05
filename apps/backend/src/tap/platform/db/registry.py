"""Explicit migration composition root, independent of runtime import order."""

from sqlalchemy import MetaData

from tap.modules.access.adapters.mysql import actor_principal, enterprise, project
from tap.modules.chat.adapters.mysql import chat_event, chat_turn, turn_snapshot
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_answer_snapshot,
    knowledge_chunk_manifest,
    knowledge_citation_snapshot,
    knowledge_document,
    knowledge_document_revision,
    knowledge_ingestion_job,
)
from tap.modules.knowledge.adapters.mysql_projection import (
    knowledge_projection_cleanup,
    knowledge_projection_fence,
    knowledge_projection_lineage,
    knowledge_projection_state,
)
from tap.platform.db.schema import outbox


def load_authoritative_metadata() -> MetaData:
    """Return only explicitly owned tables, copied into a fresh migration registry."""
    authoritative = MetaData()
    for table in (
        enterprise,
        project,
        actor_principal,
        outbox,
        chat_turn,
        chat_event,
        turn_snapshot,
        knowledge_document,
        knowledge_document_revision,
        knowledge_ingestion_job,
        knowledge_chunk_manifest,
        knowledge_answer_snapshot,
        knowledge_citation_snapshot,
        knowledge_projection_state,
        knowledge_projection_fence,
        knowledge_projection_cleanup,
        knowledge_projection_lineage,
    ):
        table.to_metadata(authoritative)
    return authoritative
