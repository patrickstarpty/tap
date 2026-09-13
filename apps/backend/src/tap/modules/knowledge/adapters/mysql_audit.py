"""Durable, scoped candidate-attempt Audit; provider strings never enter SQL."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, Integer, String, Table, insert
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.governance.ports.audit import require_audit_scope
from tap.modules.knowledge.adapters.milvus.audit import MilvusSearchAuditEvent
from tap.platform.db.project_scope import scope_values
from tap.platform.db.schema import augment_project_table, metadata

knowledge_search_audit = Table(
    "knowledge_search_audit",
    metadata,
    Column("attempt_id", String(64), primary_key=True),
    Column("query_hash", String(71)),
    Column("policy_digest", String(71)),
    Column("policy_version", String(128)),
    Column("redaction_version", String(128)),
    Column("family", String(16), nullable=False),
    Column("candidate_limit", Integer, nullable=False),
    Column("provider_candidate_count", Integer, nullable=False),
    Column("mapped_candidate_count", Integer, nullable=False),
    Column("rejected_candidate_count", Integer, nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("reason", String(32)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
)
augment_project_table(knowledge_search_audit)


class MysqlSearchAuditSink:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
        policy_version: str,
    ) -> None:
        self._scope = require_audit_scope(scope)
        self._sessions = sessions
        self._policy_version = policy_version

    async def emit(self, event: MilvusSearchAuditEvent) -> None:
        if type(event) is not MilvusSearchAuditEvent:
            raise TypeError("search audit requires closed attempt")
        if event.outcome not in {"success", "failure"} or event.error_code not in {
            None,
            "bounds",
            "unavailable",
        }:
            raise ValueError("invalid search audit outcome")
        if any(
            type(count) is not int or not 0 <= count <= 1_000_000
            for count in (event.provider_row_count, event.rejected_row_count)
        ):
            raise ValueError("invalid search audit counts")
        facts = event.metadata
        if facts is not None:
            facts.validate()
            if (facts.enterprise_id, facts.project_id, facts.policy_version) != (
                self._scope.enterprise_id,
                self._scope.project_id,
                self._policy_version,
            ):
                raise ValueError("search audit scope/policy mismatch")
        if event.outcome == "success" and facts is None:
            raise ValueError("successful audit requires bound metadata")
        async with self._sessions() as session, session.begin():
            await session.execute(
                insert(knowledge_search_audit).values(
                    **scope_values(self._scope),
                    attempt_id=uuid4().hex,
                    query_hash=facts.query_hash if facts else None,
                    policy_digest=facts.policy_digest if facts else None,
                    policy_version=facts.policy_version if facts else None,
                    redaction_version=facts.redaction_version if facts else None,
                    family="doc",
                    candidate_limit=facts.candidate_limit if facts else 0,
                    provider_candidate_count=event.provider_row_count,
                    mapped_candidate_count=event.provider_row_count
                    if event.outcome == "success"
                    else 0,
                    rejected_candidate_count=event.rejected_row_count,
                    outcome=event.outcome,
                    reason=event.error_code,
                    created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
