"""Explicit migration composition root, independent of runtime import order."""

from sqlalchemy import MetaData

from tap.modules.access.adapters.mysql import actor_principal, enterprise, project
from tap.modules.agent_team.adapters.mysql import agent_maintenance_event
from tap.modules.ai.adapters.mysql import (
    ai_agent,
    ai_agent_revision,
    skill,
    skill_import,
    skill_revision,
    skill_revision_file,
)
from tap.modules.ai.adapters.mysql_checkpointer import AI_GRAPH_TABLES
from tap.modules.chat.adapters.mysql import chat_event, chat_turn, turn_snapshot
from tap.modules.chat.adapters.mysql_conversations import (
    conversation,
    turn_answer_evidence_snapshot,
    turn_artifact_link,
    turn_input_snapshot,
)
from tap.modules.chat.adapters.mysql_feedback import answer_feedback
from tap.modules.chat.adapters.mysql_suggestions import prompt_suggestion, prompt_suggestion_refresh
from tap.modules.governance.adapters.schema import project_audit
from tap.modules.graph.adapters.mysql import GRAPH_TABLES
from tap.modules.graph.adapters.mysql_project import PROJECT_GRAPH_TABLES
from tap.modules.knowledge.adapters.mysql_audit import knowledge_search_audit
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_answer_snapshot,
    knowledge_answer_source,
    knowledge_chunk_manifest,
    knowledge_citation_snapshot,
    knowledge_document,
    knowledge_document_revision,
    knowledge_ingestion_job,
    knowledge_parse_inventory,
    knowledge_source,
    knowledge_source_command,
    knowledge_source_legacy_map,
)
from tap.modules.knowledge.adapters.mysql_managed_chunks import managed_documents, managed_history
from tap.modules.knowledge.adapters.mysql_operations import knowledge_operator_operation
from tap.modules.knowledge.adapters.mysql_projection import (
    knowledge_projection_cleanup,
    knowledge_projection_fence,
    knowledge_projection_lineage,
    knowledge_projection_state,
)
from tap.modules.knowledge.adapters.mysql_review import KNOWLEDGE_REVIEW_TABLES
from tap.modules.test_management.adapters.mysql import TEST_MANAGEMENT_TABLES
from tap.platform.db.schema import outbox, outbox_archive, outbox_dead_letter
from tap.platform.telemetry.schema import (
    langfuse_sync_cursor,
    langfuse_sync_failure,
    model_call,
    model_call_content,
    trace_span,
)

# Unscoped system tables owned by the Langfuse sync worker: it reads across every
# Project, so these never carry the enterprise/project/actor scope columns that
# `test_all_business_metadata_requires_project_scope_and_parent_consistency` requires
# of BUSINESS_TABLES below.
UNSCOPED_SYSTEM_TABLES = (langfuse_sync_cursor, langfuse_sync_failure)

# Explicit Project business inventory, separate from identity-registry ownership.
# Adapter-local declarations carry the scope contract before they are copied.
BUSINESS_TABLES = (
    managed_documents,
    managed_history,
    knowledge_search_audit,
    project_audit,
    outbox_archive,
    outbox_dead_letter,
    knowledge_operator_operation,
    outbox,
    chat_turn,
    chat_event,
    turn_snapshot,
    conversation,
    turn_input_snapshot,
    turn_answer_evidence_snapshot,
    turn_artifact_link,
    answer_feedback,
    prompt_suggestion_refresh,
    prompt_suggestion,
    knowledge_document,
    knowledge_document_revision,
    knowledge_ingestion_job,
    knowledge_parse_inventory,
    knowledge_chunk_manifest,
    knowledge_source,
    knowledge_source_command,
    knowledge_source_legacy_map,
    knowledge_answer_source,
    knowledge_answer_snapshot,
    knowledge_citation_snapshot,
    knowledge_projection_state,
    knowledge_projection_fence,
    knowledge_projection_cleanup,
    knowledge_projection_lineage,
    *KNOWLEDGE_REVIEW_TABLES,
    ai_agent,
    ai_agent_revision,
    skill,
    skill_revision,
    skill_revision_file,
    skill_import,
    agent_maintenance_event,
    *AI_GRAPH_TABLES,
    *GRAPH_TABLES,
    *PROJECT_GRAPH_TABLES,
    *TEST_MANAGEMENT_TABLES,
    trace_span,
    model_call,
    model_call_content,
)


def load_authoritative_metadata() -> MetaData:
    """Return registered identities and scoped business tables in a fresh registry."""
    authoritative = MetaData()
    for table in (
        enterprise,
        project,
        actor_principal,
        *UNSCOPED_SYSTEM_TABLES,
        *BUSINESS_TABLES,
    ):
        table.to_metadata(authoritative)
    return authoritative
