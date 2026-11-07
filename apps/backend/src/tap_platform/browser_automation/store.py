"""Durable project-scoped browser intent and receipt ledger."""

from __future__ import annotations

import uuid
import threading
from contextlib import nullcontext
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
    Column("next_action_sequence", Integer, nullable=False, server_default="1"),
    Column("cancel_requested", Boolean, nullable=False, server_default="0"),
    Column("stop_receipt", JSON),
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
    Column("execution_id", String(36)),
    Column("session_id", String(36)),
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
        self._sqlite_action_lock = threading.RLock()

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

    def insert_action_idempotent(
        self,
        project_id: str,
        session_id: str,
        key: str,
        digest: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        guard = (
            self._sqlite_action_lock
            if self.engine.dialect.name == "sqlite"
            else nullcontext()
        )
        with guard:
            with self.engine.begin() as connection:
                session = (
                    connection.execute(
                        select(sessions)
                        .where(
                            sessions.c.id == session_id,
                            sessions.c.project_id == project_id,
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
                if (
                    session is None
                    or session["status"] not in {"pending", "running", "active"}
                    or session.get("cancel_requested", False)
                ):
                    raise ValueError("browser session unavailable")
                prior = (
                    connection.execute(
                        select(idempotency).where(
                            idempotency.c.project_id == project_id,
                            idempotency.c.kind == "action",
                            idempotency.c.key == key,
                        )
                    )
                    .mappings()
                    .first()
                )
                if prior is not None:
                    if prior["digest"] != digest:
                        raise ValueError("idempotency conflict")
                    existing = (
                        connection.execute(
                            select(actions).where(actions.c.id == prior["resource_id"])
                        )
                        .mappings()
                        .one()
                    )
                    return dict(existing)
                sequence = int(session["next_action_sequence"])
                action_id = uuid.uuid4().hex
                connection.execute(
                    update(sessions)
                    .where(
                        sessions.c.id == session_id, sessions.c.project_id == project_id
                    )
                    .values(next_action_sequence=sequence + 1)
                )
                connection.execute(
                    insert(idempotency).values(
                        project_id=project_id,
                        kind="action",
                        key=key,
                        digest=digest,
                        resource_id=action_id,
                    )
                )
                row = dict(
                    id=action_id,
                    project_id=project_id,
                    session_id=session_id,
                    sequence=sequence,
                    status="pending",
                    request=request,
                    observation=None,
                    owner=None,
                    lease_until=None,
                    created_at=now(),
                )
                connection.execute(insert(actions).values(**row))
                return row

    def publish_owned(
        self,
        kind: str,
        project_id: str,
        resource_id: str,
        owner: str,
        *,
        statuses: tuple[str, ...] = ("running",),
        cancel_requested: bool | None = None,
        **values: Any,
    ) -> bool:
        table = TABLES[kind]
        conditions = [
            table.c.id == resource_id,
            table.c.project_id == project_id,
            table.c.status.in_(statuses),
            table.c.owner == owner,
            table.c.lease_until >= now(),
        ]
        if cancel_requested is not None:
            conditions.append(table.c.cancel_requested == cancel_requested)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(table).where(*conditions).values(**values)
            )
            return bool(result.rowcount)

    def cancel_requests(
        self, kind: str, project_id: str, owner: str
    ) -> list[dict[str, Any]]:
        table = TABLES[kind]
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(table).where(
                        table.c.project_id == project_id,
                        table.c.owner == owner,
                        table.c.status.in_(("running", "active")),
                        table.c.cancel_requested.is_(True),
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    def settle_unstarted_execution(
        self, project_id: str, execution_id: str, owner: str
    ) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(executions)
                .where(
                    executions.c.id == execution_id,
                    executions.c.project_id == project_id,
                    executions.c.status == "running",
                    executions.c.owner == owner,
                    executions.c.lease_until >= now(),
                    executions.c.cancel_requested.is_(True),
                    executions.c.started_at.is_(None),
                )
                .values(
                    status="canceled",
                    finished_at=now(),
                    failure_code="canceled_before_start",
                )
            )
            return bool(result.rowcount)

    def request_session_cancel(
        self, project_id: str, session_id: str, owner: str | None = None
    ) -> None:
        with self.engine.begin() as connection:
            conditions = [
                sessions.c.id == session_id,
                sessions.c.project_id == project_id,
                sessions.c.status.in_(("pending", "running", "active")),
            ]
            if owner is not None:
                conditions.extend(
                    (sessions.c.owner == owner, sessions.c.lease_until >= now())
                )
            connection.execute(
                update(sessions).where(*conditions).values(cancel_requested=True)
            )

    def request_owned_shutdown(self, project_id: str, owner: str) -> None:
        with self.engine.begin() as connection:
            for table in (sessions, executions):
                connection.execute(
                    update(table)
                    .where(
                        table.c.project_id == project_id,
                        table.c.owner == owner,
                        table.c.status.in_(("running", "active")),
                        table.c.lease_until >= now(),
                    )
                    .values(cancel_requested=True)
                )

    def settle_session(
        self,
        project_id: str,
        session_id: str,
        owner: str,
        receipt: dict[str, Any],
        *,
        pending: bool = False,
    ) -> bool:
        stopped = receipt.get("stopped") is True and receipt.get("status") == "canceled"
        status = "canceled" if stopped else "unknown"
        with self.engine.begin() as connection:
            conditions = [
                sessions.c.id == session_id,
                sessions.c.project_id == project_id,
                sessions.c.owner == owner,
                sessions.c.lease_until >= now(),
                sessions.c.cancel_requested.is_(True),
                sessions.c.status.in_(
                    ("running",) if pending else ("running", "active")
                ),
            ]
            result = connection.execute(
                update(sessions)
                .where(*conditions)
                .values(status=status, stop_receipt=receipt)
            )
            if not result.rowcount:
                return False
            connection.execute(
                update(actions)
                .where(
                    actions.c.project_id == project_id,
                    actions.c.session_id == session_id,
                    actions.c.status.in_(("pending", "running")),
                )
                .values(status=status)
            )
            return True

    def publish_action(
        self, project_id: str, row: dict[str, Any], owner: str, **values: Any
    ) -> bool:
        stamp = now()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(actions)
                .where(
                    actions.c.id == row["id"],
                    actions.c.project_id == project_id,
                    actions.c.status == "running",
                    actions.c.owner == owner,
                    actions.c.lease_until >= stamp,
                    actions.c.session_id.in_(
                        select(sessions.c.id).where(
                            sessions.c.id == row["session_id"],
                            sessions.c.project_id == project_id,
                            sessions.c.status == "active",
                            sessions.c.owner == owner,
                            sessions.c.lease_until >= stamp,
                            sessions.c.cancel_requested.is_(False),
                        )
                    ),
                )
                .values(**values)
            )
            return bool(result.rowcount)

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
            if kind == "action":
                older = actions.alias("older_action")
                query = query.where(
                    ~select(older.c.id)
                    .where(
                        older.c.session_id == actions.c.session_id,
                        older.c.sequence < actions.c.sequence,
                        older.c.status != "completed",
                    )
                    .exists()
                )
            rows = connection.execute(query).mappings()
            for row in rows:
                if kind == "session" and row["cancel_requested"]:
                    receipt = {
                        "task_id": row["task_id"],
                        "stopped": True,
                        "status": "canceled",
                        "reason": "never_started",
                    }
                    connection.execute(
                        update(sessions)
                        .where(sessions.c.id == row["id"])
                        .values(status="canceled", stop_receipt=receipt)
                    )
                    connection.execute(
                        update(actions)
                        .where(
                            actions.c.session_id == row["id"],
                            actions.c.status == "pending",
                        )
                        .values(status="canceled")
                    )
                    continue
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
                        or session["cancel_requested"]
                        or session["lease_until"] < now()
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
