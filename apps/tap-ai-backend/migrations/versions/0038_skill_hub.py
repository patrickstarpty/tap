"""Skill Hub schema (spec 1.1/1.2/4): `skill` gains a closed-set `category`
and a maintenance owner (`owner_actor_id`/`owner_assigned_at`);
`skill_revision` gains `version_label` and its origin (`origin_kind`/
`origin_ref`/`origin_imported_at`); `ai_agent_revision` gains
`pinned_skill_ids`/`excluded_skill_ids` (`skill_revision_ids` stays, read
only). New tables `turn_skill_activation` (per-Turn Skill usage) and
`skill_maintenance_event`.

Backfill reads rows in batches and applies the pure rules in
`tap.modules.ai.domain.skill_metadata` (no JSON-path SQL):
- category from the latest revision's frontmatter `metadata.category`
  (closed set only), else `other`; owner = the first revision's author,
  assigned at the Skill's `created_at`;
- `version_label` only for a SemVer `metadata.version`; imported revisions
  read `origin_kind = "unknown"` with `origin_imported_at = created_at`
  (imports never recorded how they were uploaded), authored/built-in keep
  their `source_kind`;
- each Agent revision's bound Skill revisions map to their Skill ids in
  `pinned_skill_ids`; `excluded_skill_ids` starts empty;
- `turn_skill_activation` from each historical `turn_input_snapshot`
  (manual/pinned split per `frozen_activations`), at the Turn's
  `created_at`, one row per Turn and Skill (manual over pinned).

Finally deletes the `langfuse_sync_cursor` row named `trace` so the sync
worker re-walks history and attaches `skill:` tags; `feedback` is untouched.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

from tap.modules.ai.domain.skill_metadata import (
    category_from_frontmatter,
    frozen_activations,
    pinned_from_bound,
    version_label_from_frontmatter,
)
from tap.modules.chat.adapters.mysql_skill_activations import activation_id_for

revision: str = "0038_skill_hub"
down_revision: str | None = "0037_agent_team_fk_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 500
_SCOPE_FIELDS = ("enterprise_id", "project_id", "actor_id", "identity_mode", "identity_origin")
_MODE_RANK = {"manual": 0, "pinned": 1}


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    ]


def _scope_constraints(name: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{name}_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{name}_scope_actor",
        ),
    ]


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _batches(connection: sa.Connection, sql: str, key: str) -> Iterator[Sequence[sa.Row]]:
    """Keyset-paged rows of `sql` (which must filter `{key} > :after`, order by
    `key` and end in `LIMIT :limit`)."""
    after = ""
    while True:
        rows = connection.execute(sa.text(sql), {"after": after, "limit": _BATCH}).all()
        if not rows:
            return
        yield rows
        after = getattr(rows[-1], key)


def _backfill_skills(connection: sa.Connection) -> None:
    for rows in _batches(
        connection,
        "SELECT project_id, skill_id, actor_id, created_at FROM skill "
        "WHERE skill_id > :after ORDER BY skill_id LIMIT :limit",
        "skill_id",
    ):
        updates = []
        for row in rows:
            revisions = connection.execute(
                sa.text(
                    "SELECT actor_id, frontmatter_json FROM skill_revision "
                    "WHERE project_id = :project_id AND skill_id = :skill_id "
                    "ORDER BY revision_number"
                ),
                {"project_id": row.project_id, "skill_id": row.skill_id},
            ).all()
            latest = _json(revisions[-1].frontmatter_json) if revisions else None
            updates.append(
                {
                    "project_id": row.project_id,
                    "skill_id": row.skill_id,
                    "category": category_from_frontmatter(
                        latest if isinstance(latest, dict) else None
                    )
                    or "other",
                    "owner": revisions[0].actor_id if revisions else row.actor_id,
                    "assigned": row.created_at,
                }
            )
        connection.execute(
            sa.text(
                "UPDATE skill SET category = :category, owner_actor_id = :owner, "
                "owner_assigned_at = :assigned "
                "WHERE project_id = :project_id AND skill_id = :skill_id"
            ),
            updates,
        )


def _backfill_skill_revisions(connection: sa.Connection) -> None:
    for rows in _batches(
        connection,
        "SELECT revision_id, source_kind, frontmatter_json, created_at FROM skill_revision "
        "WHERE revision_id > :after ORDER BY revision_id LIMIT :limit",
        "revision_id",
    ):
        updates = []
        for row in rows:
            frontmatter = _json(row.frontmatter_json)
            imported = row.source_kind == "imported"
            updates.append(
                {
                    "revision_id": row.revision_id,
                    "version_label": version_label_from_frontmatter(
                        frontmatter if isinstance(frontmatter, dict) else None
                    ),
                    "origin_kind": "unknown" if imported else row.source_kind,
                    "imported_at": row.created_at if imported else None,
                }
            )
        connection.execute(
            sa.text(
                "UPDATE skill_revision SET version_label = :version_label, "
                "origin_kind = :origin_kind, origin_imported_at = :imported_at "
                "WHERE revision_id = :revision_id"
            ),
            updates,
        )


def _skill_of(connection: sa.Connection) -> dict[tuple[str, str], str]:
    """(project_id, skill revision_id) -> skill_id."""
    return {
        (row.project_id, row.revision_id): row.skill_id
        for row in connection.execute(
            sa.text("SELECT project_id, revision_id, skill_id FROM skill_revision")
        )
    }


def _strings(value: object) -> list[str]:
    value = _json(value)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _backfill_agent_revisions(
    connection: sa.Connection, skill_of: dict[tuple[str, str], str]
) -> dict[tuple[str, str], frozenset[str]]:
    """Fill `pinned_skill_ids`/`excluded_skill_ids`; return each Agent
    revision's bound Skill revision ids for the activation backfill."""
    bound_of: dict[tuple[str, str], frozenset[str]] = {}
    for rows in _batches(
        connection,
        "SELECT project_id, revision_id, skill_revision_ids FROM ai_agent_revision "
        "WHERE revision_id > :after ORDER BY revision_id LIMIT :limit",
        "revision_id",
    ):
        updates = []
        for row in rows:
            bound = _strings(row.skill_revision_ids)
            bound_of[(row.project_id, row.revision_id)] = frozenset(bound)
            project_skills = {
                revision_id: skill_of[(row.project_id, revision_id)]
                for revision_id in bound
                if (row.project_id, revision_id) in skill_of
            }
            updates.append(
                {
                    "revision_id": row.revision_id,
                    "pinned": json.dumps(list(pinned_from_bound(bound, project_skills))),
                    "excluded": json.dumps([]),
                }
            )
        connection.execute(
            sa.text(
                "UPDATE ai_agent_revision SET pinned_skill_ids = :pinned, "
                "excluded_skill_ids = :excluded WHERE revision_id = :revision_id"
            ),
            updates,
        )
    return bound_of


def _backfill_activations(
    connection: sa.Connection,
    skill_of: dict[tuple[str, str], str],
    bound_of: dict[tuple[str, str], frozenset[str]],
) -> None:
    for rows in _batches(
        connection,
        "SELECT s.snapshot_id, s.snapshot, t.turn_id, t.created_at, "
        "t.enterprise_id, t.project_id, t.actor_id, t.identity_mode, t.identity_origin "
        "FROM turn_input_snapshot s JOIN chat_turn t "
        "ON t.project_id = s.project_id AND t.turn_id = s.turn_id "
        "WHERE s.snapshot_id > :after ORDER BY s.snapshot_id LIMIT :limit",
        "snapshot_id",
    ):
        inserts = []
        for row in rows:
            snapshot = _json(row.snapshot)
            if not isinstance(snapshot, dict):
                continue
            agent_revision_id = snapshot.get("agent_revision_id")
            bound = (
                bound_of.get((row.project_id, agent_revision_id), frozenset())
                if isinstance(agent_revision_id, str)
                else frozenset()
            )
            chosen: dict[str, dict[str, object]] = {}
            for activation in frozen_activations(snapshot, bound):
                skill_id = skill_of.get((row.project_id, activation.skill_revision_id))
                if skill_id is None:
                    continue
                # One row per Turn and Skill: manual outranks pinned; a tie
                # keeps the first frozen revision.
                current = chosen.get(skill_id)
                if current is not None and (
                    _MODE_RANK[str(current["mode"])] <= _MODE_RANK[activation.mode]
                ):
                    continue
                chosen[skill_id] = {
                    "activation_id": activation_id_for(row.turn_id, skill_id),
                    "turn_id": row.turn_id,
                    "skill_id": skill_id,
                    "skill_revision_id": activation.skill_revision_id,
                    "mode": activation.mode,
                    "activated_at": row.created_at,
                    "bytes": activation.bytes,
                    **{field: getattr(row, field) for field in _SCOPE_FIELDS},
                }
            inserts.extend(chosen.values())
        if inserts:
            connection.execute(
                sa.text(
                    "INSERT INTO turn_skill_activation (activation_id, turn_id, skill_id, "
                    "skill_revision_id, mode, activated_at, bytes, enterprise_id, project_id, "
                    "actor_id, identity_mode, identity_origin) VALUES (:activation_id, "
                    ":turn_id, :skill_id, :skill_revision_id, :mode, :activated_at, :bytes, "
                    ":enterprise_id, :project_id, :actor_id, :identity_mode, :identity_origin)"
                ),
                inserts,
            )


def upgrade() -> None:
    # Nullable first so the backfill below can populate every pre-existing
    # row before the NOT NULL columns are tightened.
    op.add_column("skill", sa.Column("category", sa.String(16), nullable=True))
    op.add_column("skill", sa.Column("owner_actor_id", sa.String(128), nullable=True))
    op.add_column("skill", sa.Column("owner_assigned_at", DATETIME(fsp=6), nullable=True))
    op.add_column("skill_revision", sa.Column("version_label", sa.String(32), nullable=True))
    op.add_column("skill_revision", sa.Column("origin_kind", sa.String(16), nullable=True))
    op.add_column("skill_revision", sa.Column("origin_ref", sa.String(512), nullable=True))
    op.add_column("skill_revision", sa.Column("origin_imported_at", DATETIME(fsp=6), nullable=True))
    op.add_column("ai_agent_revision", sa.Column("pinned_skill_ids", JSON, nullable=True))
    op.add_column("ai_agent_revision", sa.Column("excluded_skill_ids", JSON, nullable=True))

    connection = op.get_bind()
    _backfill_skills(connection)
    _backfill_skill_revisions(connection)
    skill_of = _skill_of(connection)
    bound_of = _backfill_agent_revisions(connection, skill_of)

    op.alter_column("skill", "category", existing_type=sa.String(16), nullable=False)
    op.alter_column("skill", "owner_actor_id", existing_type=sa.String(128), nullable=False)
    op.alter_column("skill", "owner_assigned_at", existing_type=DATETIME(fsp=6), nullable=False)
    op.alter_column("skill_revision", "origin_kind", existing_type=sa.String(16), nullable=False)
    op.create_foreign_key(
        "fk_skill_owner_actor",
        "skill",
        "actor_principal",
        ["enterprise_id", "owner_actor_id"],
        ["enterprise_id", "actor_id"],
    )

    # FK names follow `augment_project_table`: `PROJECT_PARENT_LINKS` makes
    # `skill_id -> skill.skill_id` each table's `project_parent_0`.
    op.create_table(
        "turn_skill_activation",
        sa.Column("activation_id", sa.String(64), primary_key=True),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("skill_id", sa.String(64), nullable=False),
        sa.Column("skill_revision_id", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("activated_at", DATETIME(fsp=6), nullable=False),
        sa.Column("bytes", sa.Integer, nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "turn_id", "skill_id", name="uq_turn_skill_activation_turn_skill"
        ),
        sa.UniqueConstraint(
            "project_id", "activation_id", name="uq_turn_skill_activation_project_pk"
        ),
        *_scope_constraints("turn_skill_activation"),
        sa.ForeignKeyConstraint(
            ["project_id", "skill_id"],
            ["skill.project_id", "skill.skill_id"],
            name="fk_turn_skill_activation_project_parent_0",
        ),
    )
    op.create_index(
        "ix_turn_skill_activation_project_skill_activated",
        "turn_skill_activation",
        ["project_id", "skill_id", "activated_at"],
    )

    op.create_table(
        "skill_maintenance_event",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("skill_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("revision_id", sa.String(64), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("occurred_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "event_id", name="uq_skill_maintenance_event_project_pk"),
        *_scope_constraints("skill_maintenance_event"),
        sa.ForeignKeyConstraint(
            ["project_id", "skill_id"],
            ["skill.project_id", "skill.skill_id"],
            name="fk_skill_maintenance_event_project_parent_0",
        ),
    )
    op.create_index(
        "ix_skill_maintenance_event_project_skill_occurred",
        "skill_maintenance_event",
        ["project_id", "skill_id", "occurred_at"],
    )

    _backfill_activations(connection, skill_of, bound_of)

    # The sync worker resumes from `position`, so deleting the cursor row makes
    # it re-walk every historical trace and attach the new `skill:` tags. The
    # `feedback` cursor is unrelated and stays put.
    op.execute(sa.text("DELETE FROM langfuse_sync_cursor WHERE name = 'trace'"))


def downgrade() -> None:
    # Dropping a table removes its indexes and FKs together (dropping the
    # index first fails: the parent FK depends on it).
    op.drop_table("skill_maintenance_event")
    op.drop_table("turn_skill_activation")

    op.drop_column("ai_agent_revision", "excluded_skill_ids")
    op.drop_column("ai_agent_revision", "pinned_skill_ids")
    op.drop_column("skill_revision", "origin_imported_at")
    op.drop_column("skill_revision", "origin_ref")
    op.drop_column("skill_revision", "origin_kind")
    op.drop_column("skill_revision", "version_label")

    op.drop_constraint("fk_skill_owner_actor", "skill", type_="foreignkey")
    # MySQL auto-created a backing index with the FK's name and keeps it after
    # the FK is dropped; left behind, re-upgrading fails on "Duplicate key name".
    if "fk_skill_owner_actor" in {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("skill")
    }:
        op.drop_index("fk_skill_owner_actor", table_name="skill")
    op.drop_column("skill", "owner_assigned_at")
    op.drop_column("skill", "owner_actor_id")
    op.drop_column("skill", "category")

    # The deleted `trace` cursor row is never recreated: a downgrade does not
    # restore sync worker progress, only the schema it depended on.
