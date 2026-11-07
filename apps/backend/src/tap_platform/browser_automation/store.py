"""Durable project-scoped browser intent and receipt ledger."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Integer,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

metadata = MetaData()

sessions = Table(
    "tap_browser_sessions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("task_id", String(80), nullable=False),
    Column("status", String(20), nullable=False),
    Column("request", JSON, nullable=False),
    Column("observation", JSON),
    Column("owner", String(80)),
    Column("lease_until", String(40)),
    Column("created_at", String(40), nullable=False),
)
actions = Table(
    "tap_browser_actions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("session_id", String(36), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column("request", JSON, nullable=False),
    Column("observation", JSON),
    Column("owner", String(80)),
    Column("lease_until", String(40)),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("session_id", "sequence"),
)
bundles = Table(
    "tap_browser_bundles",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("task_id", String(80), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("script_ref", String(128), nullable=False),
    Column("input_sha256", String(64), nullable=False),
    Column("input_ref", String(128), nullable=False),
    Column("allowed_origins", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)
executions = Table(
    "tap_browser_executions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("task_id", String(80), nullable=False),
    Column("bundle_id", String(36), nullable=False),
    Column("input_sha256", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("request", JSON, nullable=False),
    Column("owner", String(80)),
    Column("lease_until", String(40)),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("started_at", String(40)),
    Column("finished_at", String(40)),
    Column("result", JSON),
    Column("steps", JSON),
    Column("evidence", JSON),
    Column("failure_code", String(80)),
    Column("created_at", String(40), nullable=False),
)
evidence = Table(
    "tap_browser_evidence",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("task_id", String(80), nullable=False),
    Column("execution_id", String(36), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("object_ref", String(128), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
)
idempotency = Table(
    "tap_browser_idempotency",
    metadata,
    Column("project_id", String(128), primary_key=True),
    Column("kind", String(20), primary_key=True),
    Column("key", String(128), primary_key=True),
    Column("digest", String(64), nullable=False),
    Column("resource_id", String(36), nullable=False),
)
for _table in (sessions, actions, executions):
    Index(
        f"ix_{_table.name}_pending",
        _table.c.project_id,
        _table.c.status,
        _table.c.created_at,
    )
Index("ix_tap_browser_evidence_project", evidence.c.project_id, evidence.c.id)

TABLES = {
    "session": sessions,
    "action": actions,
    "bundle": bundles,
    "execution": executions,
    "evidence": evidence,
}


def now() -> str:
    return datetime.now(UTC).isoformat()


class BrowserStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(
        self, kind: str, project_id: str, resource_id: str
    ) -> dict[str, Any] | None:
        table = TABLES[kind]
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(table).where(
                        table.c.id == resource_id, table.c.project_id == project_id
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def existing_idempotent(
        self, kind: str, project_id: str, key: str, digest: str
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            prior = (
                connection.execute(
                    select(idempotency).where(
                        idempotency.c.project_id == project_id,
                        idempotency.c.kind == kind,
                        idempotency.c.key == key,
                    )
                )
                .mappings()
                .first()
            )
        if prior is None:
            return None
        if prior["digest"] != digest:
            raise ValueError("idempotency conflict")
        found = self.get(kind, project_id, prior["resource_id"])
        if found is None:
            raise RuntimeError("idempotency record without resource")
        return found

    def insert_idempotent(
        self, kind: str, project_id: str, key: str, digest: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        table = TABLES[kind]
        resource_id = values.get("id") or uuid.uuid4().hex
        values = {
            **values,
            "id": resource_id,
            "project_id": project_id,
            "created_at": now(),
        }
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(idempotency).values(
                        project_id=project_id,
                        kind=kind,
                        key=key,
                        digest=digest,
                        resource_id=resource_id,
                    )
                )
                connection.execute(insert(table).values(**values))
            return values
        except IntegrityError:
            with self.engine.connect() as connection:
                prior = (
                    connection.execute(
                        select(idempotency).where(
                            idempotency.c.project_id == project_id,
                            idempotency.c.kind == kind,
                            idempotency.c.key == key,
                        )
                    )
                    .mappings()
                    .first()
                )
            if prior is None or prior["digest"] != digest:
                raise ValueError("idempotency conflict") from None
            found = self.get(kind, project_id, prior["resource_id"])
            if found is None:
                raise RuntimeError("idempotency record without resource")
            return found

    def update(
        self, kind: str, project_id: str, resource_id: str, **values: Any
    ) -> bool:
        table = TABLES[kind]
        with self.engine.begin() as connection:
            result = connection.execute(
                update(table)
                .where(table.c.id == resource_id, table.c.project_id == project_id)
                .values(**values)
            )
            return bool(result.rowcount)

    def append_evidence(self, values: dict[str, Any]) -> dict[str, Any]:
        row = {**values, "id": uuid.uuid4().hex, "created_at": now()}
        with self.engine.begin() as connection:
            connection.execute(insert(evidence).values(**row))
        return row

    def claim(
        self, kind: str, project_id: str, owner: str, lease_seconds: int
    ) -> dict[str, Any] | None:
        table = TABLES[kind]
        with self.engine.begin() as connection:
            query = (
                select(table)
                .where(table.c.project_id == project_id, table.c.status == "pending")
                .order_by(table.c.created_at)
                .with_for_update(skip_locked=True)
            )
            rows = connection.execute(query).mappings()
            for row in rows:
                if kind == "action":
                    session = (
                        connection.execute(
                            select(sessions).where(
                                sessions.c.id == row["session_id"],
                                sessions.c.project_id == project_id,
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if (
                        session is None
                        or session["owner"] != owner
                        or session["status"] != "active"
                    ):
                        continue
                if kind == "execution" and row["cancel_requested"]:
                    connection.execute(
                        update(table)
                        .where(table.c.id == row["id"])
                        .values(
                            status="canceled",
                            finished_at=now(),
                            failure_code="canceled_before_start",
                        )
                    )
                    continue
                lease = (
                    datetime.now(UTC) + timedelta(seconds=lease_seconds)
                ).isoformat()
                result = connection.execute(
                    update(table)
                    .where(table.c.id == row["id"], table.c.status == "pending")
                    .values(status="running", owner=owner, lease_until=lease)
                )
                if result.rowcount:
                    return {
                        **dict(row),
                        "status": "running",
                        "owner": owner,
                        "lease_until": lease,
                    }
        return None

    def expire_lost(self, project_id: str) -> None:
        stamp = now()
        with self.engine.begin() as connection:
            for table in (sessions, actions, executions):
                connection.execute(
                    update(table)
                    .where(
                        table.c.project_id == project_id,
                        table.c.status.in_(["running", "active"]),
                        table.c.lease_until < stamp,
                    )
                    .values(status="unknown")
                )
            connection.execute(
                update(actions)
                .where(
                    actions.c.project_id == project_id,
                    actions.c.status == "pending",
                    actions.c.session_id.in_(
                        select(sessions.c.id).where(
                            sessions.c.project_id == project_id,
                            sessions.c.status == "unknown",
                        )
                    ),
                )
                .values(status="unknown")
            )

    def next_sequence(self, project_id: str, session_id: str) -> int:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(actions.c.sequence)
                .where(
                    actions.c.project_id == project_id,
                    actions.c.session_id == session_id,
                )
                .order_by(actions.c.sequence.desc())
                .limit(1)
            ).first()
            return int(rows[0]) + 1 if rows else 1
