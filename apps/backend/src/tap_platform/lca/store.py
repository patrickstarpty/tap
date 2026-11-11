"""Owner-scoped LCA persistence. No method here dispatches browser work.

Canonical bytes are validated by the domain boundary. SQL compare-and-swap and
unique constraints, rather than process locks, arbitrate concurrent writers.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
_CANONICAL_BYTES = LargeBinary(length=16777215).with_variant(MEDIUMBLOB(), "mysql")


def _identity() -> list[Column[Any]]:
    return [
        Column("id", String(36), primary_key=True),
        Column("project_id", String(128), nullable=False),
        Column("actor_id", String(80), nullable=False),
        Column("created_at", String(40), nullable=False),
    ]


flows = Table(
    "tap_lca_debug_flows",
    metadata,
    *_identity(),
    Column("title", String(256), nullable=False),
    Column("version", Integer, nullable=False),
    Column("canonical_bytes", _CANONICAL_BYTES, nullable=False),
    Column("provenance", JSON, nullable=False),
    Column("updated_at", String(40), nullable=False),
)
drafts = Table(
    "tap_lca_debug_drafts",
    metadata,
    Column("flow_id", String(36), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("canonical_bytes", _CANONICAL_BYTES, nullable=False),
    Column("provenance", JSON, nullable=False),
    Column("updated_at", String(40), nullable=False),
)
snapshots = Table(
    "tap_lca_debug_snapshots",
    metadata,
    *_identity(),
    Column("flow_id", String(36), nullable=False),
    Column("version", Integer, nullable=False),
    Column("canonical_bytes", _CANONICAL_BYTES, nullable=False),
    Column("definition_digest", String(64), nullable=False),
    Column("schema_version", String(80), nullable=False),
    Column("generator_version", String(80), nullable=False),
    Column("allowed_origins", JSON, nullable=False),
    Column("identity_digest", String(64), nullable=False),
    UniqueConstraint("flow_id", "identity_digest"),
)
runs = Table(
    "tap_lca_debug_runs",
    metadata,
    *_identity(),
    Column("flow_id", String(36), nullable=False),
    Column("snapshot_id", String(36), nullable=False),
    Column("definition_digest", String(64), nullable=False),
    Column("bundle_id", String(36), nullable=False),
    Column("input_digest", String(64), nullable=False),
    Column("classification", String(20), nullable=False),
    Column("execution_key", String(128), nullable=False, unique=True),
    Column("execution_request", JSON, nullable=False),
    Column("execution_id", String(36)),
)
idempotency = Table(
    "tap_lca_debug_idempotency",
    metadata,
    Column("project_id", String(128), primary_key=True),
    Column("kind", String(20), primary_key=True),
    Column("key", String(128), primary_key=True),
    Column("actor_id", String(80), nullable=False),
    Column("digest", String(64), nullable=False),
    Column("resource_id", String(36), nullable=False),
)
recordings = Table(
    "tap_lca_debug_recordings",
    metadata,
    Column("session_id", String(36), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("actor_id", String(80), nullable=False),
    Column("flow_id", String(36), nullable=False),
    Column("draft_version", Integer, nullable=False),
    Column("allowed_origins", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)


assets = Table(
    "tap_lca_assets",
    metadata,
    *_identity(),
    Column("filename", String(160), nullable=False),
    Column("content_type", String(80), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("object_ref", String(128), nullable=False),
)


releases = Table(
    "tap_lca_releases",
    metadata,
    *_identity(),
    Column("flow_id", String(36), nullable=False),
    Column("snapshot_id", String(36), nullable=False),
    Column("version", Integer, nullable=False),
    Column("title", String(256), nullable=False),
    Column("verified_run_id", String(36), nullable=False),
    Column("definition_digest", String(64), nullable=False),
    UniqueConstraint("flow_id", "version"),
)


class LcaConflict(ValueError):
    """The expected revision, immutable link or idempotency body changed."""


class LcaNotFound(ValueError):
    """The resource does not exist within this owner's project scope."""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _get(
    connection: Connection,
    table: Table,
    project_id: str,
    actor_id: str,
    resource_id: str,
) -> dict[str, Any]:
    row = (
        connection.execute(
            select(table).where(
                table.c.id == resource_id,
                table.c.project_id == project_id,
                table.c.actor_id == actor_id,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LcaNotFound("debug resource not found")
    return dict(row)


class LcaStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _get(
        self, table: Table, project_id: str, actor_id: str, resource_id: str
    ) -> dict[str, Any]:
        with self.engine.connect() as connection:
            return _get(connection, table, project_id, actor_id, resource_id)

    def create_asset(
        self, project_id: str, actor_id: str, key: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        return self._create(
            assets, "asset", project_id, actor_id, key, _digest(values), values
        )

    def get_asset(
        self, project_id: str, actor_id: str, asset_id: str
    ) -> dict[str, Any]:
        return self._get(assets, project_id, actor_id, asset_id)

    def list_assets(self, project_id: str, actor_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(assets)
                    .where(
                        assets.c.project_id == project_id, assets.c.actor_id == actor_id
                    )
                    .order_by(assets.c.created_at.desc(), assets.c.id)
                    .limit(100)
                ).mappings()
            ]

    def get_flow(self, project_id: str, actor_id: str, flow_id: str) -> dict[str, Any]:
        return self._get(flows, project_id, actor_id, flow_id)

    def get_snapshot(
        self, project_id: str, actor_id: str, snapshot_id: str
    ) -> dict[str, Any]:
        return self._get(snapshots, project_id, actor_id, snapshot_id)

    def get_run(self, project_id: str, actor_id: str, run_id: str) -> dict[str, Any]:
        return self._get(runs, project_id, actor_id, run_id)

    def list_flows(self, project_id: str, actor_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(flows)
                    .where(
                        flows.c.project_id == project_id, flows.c.actor_id == actor_id
                    )
                    .order_by(flows.c.created_at, flows.c.id)
                ).mappings()
            ]

    def list_runs(
        self, project_id: str, actor_id: str, flow_id: str
    ) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            _get(connection, flows, project_id, actor_id, flow_id)
            return [
                dict(row)
                for row in connection.execute(
                    select(runs)
                    .where(
                        runs.c.project_id == project_id,
                        runs.c.actor_id == actor_id,
                        runs.c.flow_id == flow_id,
                    )
                    .order_by(runs.c.created_at, runs.c.id)
                ).mappings()
            ]

    def _prior(
        self,
        table: Table,
        kind: str,
        project_id: str,
        actor_id: str,
        key: str,
        digest: str | None = None,
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
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
            if row is None:
                return None
            if row["actor_id"] != actor_id:
                raise LcaNotFound("debug resource not found")
            if digest is not None and row["digest"] != digest:
                raise LcaConflict("idempotency conflict")
            return _get(connection, table, project_id, actor_id, row["resource_id"])

    def get_run_by_key(
        self, project_id: str, actor_id: str, key: str
    ) -> dict[str, Any] | None:
        return self._prior(runs, "run", project_id, actor_id, key)

    def _create(
        self,
        table: Table,
        kind: str,
        project_id: str,
        actor_id: str,
        key: str,
        digest: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        if not key or len(key) > 128:
            raise ValueError("invalid idempotency key")
        prior = self._prior(table, kind, project_id, actor_id, key, digest)
        if prior is not None:
            return prior
        record = dict(
            values,
            id=uuid.uuid4().hex,
            project_id=project_id,
            actor_id=actor_id,
            created_at=_now(),
        )
        try:
            with self.engine.begin() as connection:
                if table is releases:
                    current = connection.execute(
                        select(flows.c.version)
                        .where(
                            flows.c.id == values["flow_id"],
                            flows.c.project_id == project_id,
                            flows.c.actor_id == actor_id,
                        )
                        .with_for_update()
                    ).scalar_one_or_none()
                    if current != values["version"]:
                        raise LcaConflict("draft version conflict")
                connection.execute(
                    insert(idempotency).values(
                        project_id=project_id,
                        kind=kind,
                        key=key,
                        actor_id=actor_id,
                        digest=digest,
                        resource_id=record["id"],
                    )
                )
                connection.execute(insert(table).values(**record))
                if table is flows:
                    connection.execute(
                        insert(drafts).values(
                            **{
                                "flow_id": record["id"],
                                **{
                                    name: record[name]
                                    for name in (
                                        "version",
                                        "canonical_bytes",
                                        "provenance",
                                        "updated_at",
                                    )
                                },
                            }
                        )
                    )
        except IntegrityError:
            prior = self._prior(table, kind, project_id, actor_id, key, digest)
            if prior is None:
                raise
            return prior
        return self._get(table, project_id, actor_id, record["id"])

    def create_flow(
        self,
        project_id: str,
        actor_id: str,
        key: str,
        title: str,
        canonical_bytes: bytes,
        provenance: Any = None,
    ) -> dict[str, Any]:
        provenance = {} if provenance is None else provenance
        digest = _digest([title, canonical_bytes.hex(), provenance])
        return self._create(
            flows,
            "flow",
            project_id,
            actor_id,
            key,
            digest,
            dict(
                title=title,
                version=1,
                canonical_bytes=canonical_bytes,
                provenance=provenance,
                updated_at=_now(),
            ),
        )

    def save_draft(
        self,
        project_id: str,
        actor_id: str,
        flow_id: str,
        expected_version: int,
        canonical_bytes: bytes,
        provenance: Any = None,
    ) -> dict[str, Any]:
        with self.engine.begin() as connection:
            values = dict(
                version=expected_version + 1,
                canonical_bytes=canonical_bytes,
                provenance={} if provenance is None else provenance,
                updated_at=_now(),
            )
            changed = connection.execute(
                update(flows)
                .where(
                    flows.c.id == flow_id,
                    flows.c.project_id == project_id,
                    flows.c.actor_id == actor_id,
                    flows.c.version == expected_version,
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                _get(connection, flows, project_id, actor_id, flow_id)
                raise LcaConflict("draft version conflict")
            connection.execute(insert(drafts).values(flow_id=flow_id, **values))
            return _get(connection, flows, project_id, actor_id, flow_id)

    def freeze_snapshot(
        self,
        project_id: str,
        actor_id: str,
        flow_id: str,
        expected_version: int,
        schema_version: str,
        generator_version: str,
        allowed_origins: list[str],
    ) -> dict[str, Any]:
        flow = self.get_flow(project_id, actor_id, flow_id)
        if flow["version"] != expected_version:
            raise LcaConflict("draft version conflict")
        definition_digest = hashlib.sha256(flow["canonical_bytes"]).hexdigest()
        identity = _digest(
            [
                expected_version,
                definition_digest,
                schema_version,
                generator_version,
                allowed_origins,
            ]
        )
        values = dict(
            id=uuid.uuid4().hex,
            project_id=project_id,
            actor_id=actor_id,
            created_at=_now(),
            flow_id=flow_id,
            version=expected_version,
            canonical_bytes=flow["canonical_bytes"],
            definition_digest=definition_digest,
            schema_version=schema_version,
            generator_version=generator_version,
            allowed_origins=allowed_origins,
            identity_digest=identity,
        )
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(snapshots).values(**values))
        except IntegrityError:
            with self.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(snapshots).where(
                            snapshots.c.flow_id == flow_id,
                            snapshots.c.identity_digest == identity,
                            snapshots.c.project_id == project_id,
                            snapshots.c.actor_id == actor_id,
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise
                return dict(row)
        return self.get_snapshot(project_id, actor_id, values["id"])

    def create_run(
        self,
        project_id: str,
        actor_id: str,
        key: str,
        snapshot_id: str,
        bundle_id: str,
        input_digest: str,
        execution_request: dict[str, Any],
        classification: str = "debug",
    ) -> dict[str, Any]:
        snapshot = self.get_snapshot(project_id, actor_id, snapshot_id)
        digest = _digest([snapshot_id, bundle_id, input_digest, execution_request])
        return self._create(
            runs,
            "run",
            project_id,
            actor_id,
            key,
            digest,
            dict(
                flow_id=snapshot["flow_id"],
                snapshot_id=snapshot_id,
                definition_digest=snapshot["definition_digest"],
                bundle_id=bundle_id,
                input_digest=input_digest,
                classification=classification,
                execution_key="lca-debug-" + uuid.uuid4().hex,
                execution_request=execution_request,
                execution_id=None,
            ),
        )

    def get_release(self, project: str, actor: str, release_id: str) -> dict[str, Any]:
        return self._get(releases, project, actor, release_id)

    def list_releases(
        self, project: str, actor: str, flow_id: str
    ) -> list[dict[str, Any]]:
        self.get_flow(project, actor, flow_id)
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(releases)
                    .where(
                        releases.c.project_id == project,
                        releases.c.actor_id == actor,
                        releases.c.flow_id == flow_id,
                    )
                    .order_by(releases.c.version.desc())
                ).mappings()
            ]

    def create_release(
        self,
        project: str,
        actor: str,
        key: str,
        flow: dict[str, Any],
        snapshot: dict[str, Any],
        run_id: str,
    ) -> dict[str, Any]:
        values = dict(
            flow_id=flow["id"],
            snapshot_id=snapshot["id"],
            version=snapshot["version"],
            title=flow["title"],
            verified_run_id=run_id,
            definition_digest=snapshot["definition_digest"],
        )
        prior = self._prior(releases, "release", project, actor, key, _digest(values))
        if prior is not None:
            return prior
        try:
            return self._create(
                releases, "release", project, actor, key, _digest(values), values
            )
        except IntegrityError:
            raise LcaConflict("This draft version is already published") from None

    def link_execution(
        self, project_id: str, actor_id: str, run_id: str, execution_id: str
    ) -> dict[str, Any]:
        if not execution_id:
            raise ValueError("execution ID is required")
        with self.engine.begin() as connection:
            connection.execute(
                update(runs)
                .where(
                    runs.c.id == run_id,
                    runs.c.project_id == project_id,
                    runs.c.actor_id == actor_id,
                    runs.c.execution_id.is_(None),
                )
                .values(execution_id=execution_id)
            )
            record = _get(connection, runs, project_id, actor_id, run_id)
            if record["execution_id"] != execution_id:
                raise LcaConflict("execution link is immutable")
            return record
