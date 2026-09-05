"""Runtime table metadata and transactional Outbox; migrations use db.registry."""

from sqlalchemy import BigInteger, Column, Index, Integer, MetaData, String, Table, Text
from sqlalchemy.dialects.mysql import DATETIME

metadata = MetaData()

outbox = Table(
    "outbox",
    metadata,
    Column("outbox_id", String(128), primary_key=True),
    Column("command_id", String(128), nullable=False, unique=True),
    Column("aggregate_type", String(64), nullable=False),
    Column("aggregate_id", String(64), nullable=False),
    Column("sequence", BigInteger),
    Column("message_type", String(64), nullable=False),
    Column("status", String(24), nullable=False, server_default="pending"),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("next_attempt_at", DATETIME(fsp=6), nullable=False),
    Column("claimed_by", String(128)),
    Column("claim_token", String(64)),
    Column("lease_until", DATETIME(fsp=6)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("published_at", DATETIME(fsp=6)),
    Column("last_error", Text),
)
Index("ix_outbox_claim", outbox.c.status, outbox.c.next_attempt_at, outbox.c.created_at)
Index("ix_outbox_expired_lease", outbox.c.status, outbox.c.lease_until)

# Explicit table-local augmentation is invoked after each adapter's table definitions.
# Migration 0007 carries its own frozen copy; it never imports live metadata.
PROJECT_PARENT_LINKS: dict[str, tuple[tuple[str, str, str, str | None], ...]] = {
    "chat_event": (("turn_id", "chat_turn", "turn_id", None),),
    "turn_snapshot": (("turn_id", "chat_turn", "turn_id", None),),
    "knowledge_document": (
        ("current_revision_id", "knowledge_document_revision", "revision_id", None),
    ),
    "knowledge_document_revision": (("document_id", "knowledge_document", "document_id", None),),
    "knowledge_ingestion_job": (
        ("revision_id", "knowledge_document_revision", "revision_id", None),
    ),
    "knowledge_chunk_manifest": (
        ("revision_id", "knowledge_document_revision", "revision_id", None),
    ),
    "knowledge_citation_snapshot": (
        ("trace_id", "knowledge_answer_snapshot", "trace_id", "CASCADE"),
    ),
    "knowledge_projection_fence": (
        ("alias_name", "knowledge_projection_state", "alias_name", None),
        ("revision_id", "knowledge_document_revision", "revision_id", None),
        ("document_id", "knowledge_document", "document_id", None),
    ),
    "knowledge_projection_cleanup": (
        ("alias_name", "knowledge_projection_state", "alias_name", None),
    ),
    "knowledge_projection_lineage": (
        ("alias_name", "knowledge_projection_state", "alias_name", None),
    ),
}


def augment_project_table(table: Table) -> None:
    """Declare explicit Project columns and constraints on a business table once."""
    from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
    from sqlalchemy.dialects.mysql import JSON

    if "project_id" in table.c:
        return
    for field, width in (
        ("enterprise_id", 128),
        ("project_id", 128),
        ("actor_id", 128),
        ("identity_mode", 16),
        ("identity_origin", 16),
    ):
        table.append_column(Column(field, String(width), nullable=False))
    for foreign_constraint in tuple(table.foreign_key_constraints):
        table.constraints.remove(foreign_constraint)
        for element in foreign_constraint.elements:
            element.parent.foreign_keys.discard(element)
            table.foreign_keys.discard(element)
    # Preserve business primary keys; scope only application uniqueness and indexes.
    for constraint in tuple(table.constraints):
        if isinstance(constraint, UniqueConstraint):
            table.constraints.remove(constraint)
            for column in constraint.columns:
                column.unique = False
            name = constraint.name or f"uq_{table.name}_project_command"
            table.append_constraint(
                UniqueConstraint("project_id", *constraint.columns.keys(), name=name)
            )
    for index in tuple(table.indexes):
        table.indexes.remove(index)
        Index(
            index.name,
            table.c.project_id,
            *[table.c[column.name] for column in index.columns],
            unique=index.unique,
        )
    table.append_constraint(
        UniqueConstraint(
            "project_id", *table.primary_key.columns.keys(), name=f"uq_{table.name}_project_pk"
        )
    )
    table.append_constraint(
        ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{table.name}_scope_project",
        )
    )
    table.append_constraint(
        ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{table.name}_scope_actor",
        )
    )
    for number, (child_column, parent, target, ondelete) in enumerate(
        PROJECT_PARENT_LINKS.get(table.name, ())
    ):
        table.append_constraint(
            ForeignKeyConstraint(
                ["project_id", child_column],
                [f"{parent}.project_id", f"{parent}.{target}"],
                name=f"fk_{table.name}_project_parent_{number}",
                ondelete=ondelete,
                use_alter=table.name == "knowledge_document",
            )
        )
    if table.name == "knowledge_answer_snapshot":
        Index(
            "ix_knowledge_answer_project_retention",
            table.c.project_id,
            table.c.created_at,
            table.c.trace_id,
        )
    if table.name == "outbox":
        table.append_column(Column("envelope", JSON, nullable=False))
        table.append_column(Column("event_content_digest", String(71), nullable=False))
        table.append_constraint(
            CheckConstraint("json_type(envelope) = 'OBJECT'", name="ck_outbox_envelope_object")
        )


augment_project_table(outbox)
