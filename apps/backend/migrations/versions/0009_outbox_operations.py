"""Add immutable Outbox recovery evidence and fenced Knowledge operation receipts.

Frozen DDL: this revision never imports runtime metadata.
"""

from collections.abc import Sequence
from alembic import op

revision: str = "0009_outbox_operations"
down_revision: str | None = "0008_project_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE outbox_archive (
	outbox_id VARCHAR(128) NOT NULL,
	command_id VARCHAR(128) NOT NULL,
	aggregate_type VARCHAR(64) NOT NULL,
	aggregate_id VARCHAR(64) NOT NULL,
	sequence BIGINT,
	message_type VARCHAR(64) NOT NULL,
	status VARCHAR(24) NOT NULL DEFAULT 'pending',
	attempt_count INTEGER NOT NULL DEFAULT '0',
	next_attempt_at DATETIME(6) NOT NULL,
	claimed_by VARCHAR(128),
	claim_token VARCHAR(64),
	lease_until DATETIME(6),
	created_at DATETIME(6) NOT NULL,
	published_at DATETIME(6),
	last_error TEXT,
	enterprise_id VARCHAR(128) NOT NULL,
	project_id VARCHAR(128) NOT NULL,
	actor_id VARCHAR(128) NOT NULL,
	identity_mode VARCHAR(16) NOT NULL,
	identity_origin VARCHAR(16) NOT NULL,
	envelope JSON NOT NULL,
	event_content_digest VARCHAR(71) NOT NULL,
	archived_at DATETIME(6) NOT NULL,
	PRIMARY KEY (outbox_id),
	CONSTRAINT fk_outbox_archive_scope_project FOREIGN KEY(enterprise_id, project_id) REFERENCES project (enterprise_id, project_id),
	CONSTRAINT uq_outbox_archive_project_pk UNIQUE (project_id, outbox_id),
	CONSTRAINT fk_outbox_archive_scope_actor FOREIGN KEY(enterprise_id, actor_id) REFERENCES actor_principal (enterprise_id, actor_id),
	CONSTRAINT ck_outbox_archive_envelope_object CHECK (json_type(envelope) = 'OBJECT')
)
""")
    op.execute("""
CREATE INDEX ix_outbox_archive_retention ON outbox_archive (project_id, archived_at, outbox_id)
""")
    op.execute("""
CREATE INDEX ix_outbox_archive_command ON outbox_archive (enterprise_id, project_id, command_id)
""")
    op.execute("""
CREATE TABLE outbox_dead_letter (
	outbox_id VARCHAR(128) NOT NULL,
	command_id VARCHAR(128) NOT NULL,
	aggregate_type VARCHAR(64) NOT NULL,
	aggregate_id VARCHAR(64) NOT NULL,
	sequence BIGINT,
	message_type VARCHAR(64) NOT NULL,
	status VARCHAR(24) NOT NULL DEFAULT 'pending',
	attempt_count INTEGER NOT NULL DEFAULT '0',
	next_attempt_at DATETIME(6) NOT NULL,
	claimed_by VARCHAR(128),
	claim_token VARCHAR(64),
	lease_until DATETIME(6),
	created_at DATETIME(6) NOT NULL,
	published_at DATETIME(6),
	last_error TEXT,
	enterprise_id VARCHAR(128) NOT NULL,
	project_id VARCHAR(128) NOT NULL,
	actor_id VARCHAR(128) NOT NULL,
	identity_mode VARCHAR(16) NOT NULL,
	identity_origin VARCHAR(16) NOT NULL,
	envelope JSON NOT NULL,
	event_content_digest VARCHAR(71) NOT NULL,
	failed_at DATETIME(6) NOT NULL,
	reason VARCHAR(32) NOT NULL,
	redriven_at DATETIME(6),
	PRIMARY KEY (outbox_id),
	CONSTRAINT fk_outbox_dead_letter_scope_project FOREIGN KEY(enterprise_id, project_id) REFERENCES project (enterprise_id, project_id),
	CONSTRAINT uq_outbox_dead_letter_project_pk UNIQUE (project_id, outbox_id),
	CONSTRAINT ck_outbox_dead_letter_envelope_object CHECK (json_type(envelope) = 'OBJECT'),
	CONSTRAINT fk_outbox_dead_letter_scope_actor FOREIGN KEY(enterprise_id, actor_id) REFERENCES actor_principal (enterprise_id, actor_id)
)
""")
    op.execute("""
CREATE INDEX ix_outbox_dead_letter_redrive ON outbox_dead_letter (project_id, redriven_at, failed_at, outbox_id)
""")
    op.execute("""
CREATE TABLE knowledge_operator_operation (
	operation_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	enterprise_id VARCHAR(128) NOT NULL,
	project_id VARCHAR(128) NOT NULL,
	actor_id VARCHAR(128) NOT NULL,
	identity_mode VARCHAR(16) NOT NULL,
	identity_origin VARCHAR(16) NOT NULL,
	idempotency_key VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
	command VARCHAR(32) NOT NULL,
	parameters JSON NOT NULL,
	parameters_digest VARCHAR(64) NOT NULL,
	correlation_id VARCHAR(128) NOT NULL,
	fence BIGINT NOT NULL,
	claim_token VARCHAR(64) NOT NULL,
	lease_until DATETIME(6) NOT NULL,
	created_at DATETIME(6) NOT NULL,
	completed_at DATETIME(6),
	result JSON,
	PRIMARY KEY (operation_id),
	CONSTRAINT ck_knowledge_operation_parameters CHECK (json_type(parameters) = 'OBJECT'),
	CONSTRAINT uq_knowledge_operation_project_pk UNIQUE (project_id, operation_id),
	CONSTRAINT fk_knowledge_operation_scope_actor FOREIGN KEY(enterprise_id, actor_id) REFERENCES actor_principal (enterprise_id, actor_id),
	CONSTRAINT uq_knowledge_operation_replay UNIQUE (enterprise_id, project_id, idempotency_key),
	CONSTRAINT ck_knowledge_operation_fence CHECK (fence > 0),
	CONSTRAINT ck_knowledge_operation_completion CHECK ((completed_at IS NULL AND result IS NULL) OR (completed_at IS NOT NULL AND result IS NOT NULL AND json_type(result) = 'OBJECT')),
	CONSTRAINT fk_knowledge_operation_scope_project FOREIGN KEY(enterprise_id, project_id) REFERENCES project (enterprise_id, project_id)
)
""")
    op.execute("""
CREATE INDEX ix_knowledge_operation_lease ON knowledge_operator_operation (enterprise_id, project_id, lease_until)
""")


def downgrade() -> None:
    op.drop_table("knowledge_operator_operation")
    op.drop_table("outbox_dead_letter")
    op.drop_table("outbox_archive")
