"""Frozen Source ledger expansion with pre-DDL validation and guarded downgrade.

No runtime metadata imports. Interrupted DDL requires explicit owned restoration.
"""

import hashlib
import json

from alembic import op
from sqlalchemy import inspect, text

revision = "0010_knowledge_sources"
down_revision = "0009_outbox_operations"
branch_labels = None
depends_on = None

NEW_TABLES = (
    "knowledge_source",
    "knowledge_source_legacy_map",
    "knowledge_answer_source",
    "knowledge_search_audit",
)
CREATE_SQL = (
    (
        "\nCREATE TABLE knowledge_source (\n\tsource_id VARCHAR(64) NOT NULL, \n\tname"
        " VARCHAR(255) NOT NULL, \n\tcreated_at DATETIME(6) NOT NULL, \n\tupdated_at "
        "DATETIME(6) NOT NULL, \n\tdeleted_at DATETIME(6), \n\tenterprise_id VARCHAR("
        "128) NOT NULL, \n\tproject_id VARCHAR(128) NOT NULL, \n\tactor_id VARCHAR(12"
        "8) NOT NULL, \n\tidentity_mode VARCHAR(16) NOT NULL, \n\tidentity_origin VAR"
        "CHAR(16) NOT NULL, \n\tPRIMARY KEY (source_id), \n\tCONSTRAINT uq_knowledge_"
        "source_project_pk UNIQUE (project_id, source_id)\n)\n\n"
    ),
    (
        "\nCREATE TABLE knowledge_source_legacy_map (\n\tlegacy_source_id VARCHAR(64"
        ") NOT NULL, \n\tsource_id VARCHAR(64) NOT NULL, \n\tdocument_id VARCHAR(64) "
        "NOT NULL, \n\tenterprise_id VARCHAR(128) NOT NULL, \n\tproject_id VARCHAR(12"
        "8) NOT NULL, \n\tactor_id VARCHAR(128) NOT NULL, \n\tidentity_mode VARCHAR(1"
        "6) NOT NULL, \n\tidentity_origin VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (lega"
        "cy_source_id), \n\tCONSTRAINT uq_knowledge_source_legacy_map_project_pk UN"
        "IQUE (project_id, legacy_source_id), \n\tCONSTRAINT uq_source_legacy_sourc"
        "e UNIQUE (project_id, source_id)\n)\n\n"
    ),
    (
        "\nCREATE TABLE knowledge_answer_source (\n\ttrace_id VARCHAR(64) NOT NULL, "
        "\n\trevision_id VARCHAR(128) NOT NULL, \n\tsource_id VARCHAR(64) NOT NULL, \n"
        "\tdocument_id VARCHAR(64) NOT NULL, \n\tsource_content_hash VARCHAR(71) NOT"
        " NULL, \n\tordinal INTEGER NOT NULL, \n\tenterprise_id VARCHAR(128) NOT NULL"
        ", \n\tproject_id VARCHAR(128) NOT NULL, \n\tactor_id VARCHAR(128) NOT NULL, "
        "\n\tidentity_mode VARCHAR(16) NOT NULL, \n\tidentity_origin VARCHAR(16) NOT "
        "NULL, \n\tPRIMARY KEY (trace_id, revision_id), \n\tCONSTRAINT uq_answer_sour"
        "ce_ordinal UNIQUE (project_id, trace_id, ordinal), \n\tCONSTRAINT uq_knowl"
        "edge_answer_source_project_pk UNIQUE (project_id, trace_id, revision_id)"
        ", \n\tCONSTRAINT uq_answer_source_owner UNIQUE (project_id, trace_id, revi"
        "sion_id, document_id, source_id)\n)\n\n"
    ),
    (
        "\nCREATE TABLE knowledge_search_audit (\n\tattempt_id VARCHAR(64) NOT NULL,"
        " \n\tquery_hash VARCHAR(71), \n\tpolicy_digest VARCHAR(71), \n\tpolicy_version"
        " VARCHAR(128), \n\tredaction_version VARCHAR(128), \n\tfamily VARCHAR(16) NO"
        "T NULL, \n\tcandidate_limit INTEGER NOT NULL, \n\tprovider_candidate_count I"
        "NTEGER NOT NULL, \n\tmapped_candidate_count INTEGER NOT NULL, \n\trejected_c"
        "andidate_count INTEGER NOT NULL, \n\toutcome VARCHAR(16) NOT NULL, \n\treaso"
        "n VARCHAR(32), \n\tcreated_at DATETIME(6) NOT NULL, \n\tenterprise_id VARCHA"
        "R(128) NOT NULL, \n\tproject_id VARCHAR(128) NOT NULL, \n\tactor_id VARCHAR("
        "128) NOT NULL, \n\tidentity_mode VARCHAR(16) NOT NULL, \n\tidentity_origin V"
        "ARCHAR(16) NOT NULL, \n\tPRIMARY KEY (attempt_id), \n\tCONSTRAINT uq_knowled"
        "ge_search_audit_project_pk UNIQUE (project_id, attempt_id)\n)\n\n"
    ),
)
CONSTRAINT_SQL = (
    (
        "ALTER TABLE knowledge_document ADD CONSTRAINT uq_document_source_owner U"
        "NIQUE (project_id, document_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_document_revision ADD CONSTRAINT uq_revision_sourc"
        "e_owner UNIQUE (project_id, revision_id, document_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_source ADD CONSTRAINT fk_knowledge_source_scope_ac"
        "tor FOREIGN KEY(enterprise_id, actor_id) REFERENCES actor_principal (ent"
        "erprise_id, actor_id)"
    ),
    (
        "ALTER TABLE knowledge_source ADD CONSTRAINT fk_knowledge_source_scope_pr"
        "oject FOREIGN KEY(enterprise_id, project_id) REFERENCES project (enterpr"
        "ise_id, project_id)"
    ),
    (
        "ALTER TABLE knowledge_source_legacy_map ADD CONSTRAINT fk_knowledge_sour"
        "ce_legacy_map_project_parent_0 FOREIGN KEY(project_id, source_id) REFERE"
        "NCES knowledge_source (project_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_source_legacy_map ADD CONSTRAINT fk_knowledge_sour"
        "ce_legacy_map_scope_actor FOREIGN KEY(enterprise_id, actor_id) REFERENCE"
        "S actor_principal (enterprise_id, actor_id)"
    ),
    (
        "ALTER TABLE knowledge_source_legacy_map ADD CONSTRAINT fk_knowledge_sour"
        "ce_legacy_map_scope_project FOREIGN KEY(enterprise_id, project_id) REFER"
        "ENCES project (enterprise_id, project_id)"
    ),
    (
        "ALTER TABLE knowledge_source_legacy_map ADD CONSTRAINT fk_knowledge_sour"
        "ce_legacy_map_source_owner FOREIGN KEY(project_id, document_id, source_i"
        "d) REFERENCES knowledge_document (project_id, document_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_answer_source ADD CONSTRAINT fk_knowledge_answer_s"
        "ource_project_parent_0 FOREIGN KEY(project_id, trace_id) REFERENCES know"
        "ledge_answer_snapshot (project_id, trace_id) ON DELETE CASCADE"
    ),
    (
        "ALTER TABLE knowledge_answer_source ADD CONSTRAINT fk_knowledge_answer_s"
        "ource_project_parent_1 FOREIGN KEY(project_id, source_id) REFERENCES kno"
        "wledge_source (project_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_answer_source ADD CONSTRAINT fk_knowledge_answer_s"
        "ource_revision_owner FOREIGN KEY(project_id, revision_id, document_id, s"
        "ource_id) REFERENCES knowledge_document_revision (project_id, revision_i"
        "d, document_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_answer_source ADD CONSTRAINT fk_knowledge_answer_s"
        "ource_scope_actor FOREIGN KEY(enterprise_id, actor_id) REFERENCES actor_"
        "principal (enterprise_id, actor_id)"
    ),
    (
        "ALTER TABLE knowledge_answer_source ADD CONSTRAINT fk_knowledge_answer_s"
        "ource_scope_project FOREIGN KEY(enterprise_id, project_id) REFERENCES pr"
        "oject (enterprise_id, project_id)"
    ),
    (
        "ALTER TABLE knowledge_search_audit ADD CONSTRAINT fk_knowledge_search_au"
        "dit_scope_actor FOREIGN KEY(enterprise_id, actor_id) REFERENCES actor_pr"
        "incipal (enterprise_id, actor_id)"
    ),
    (
        "ALTER TABLE knowledge_search_audit ADD CONSTRAINT fk_knowledge_search_au"
        "dit_scope_project FOREIGN KEY(enterprise_id, project_id) REFERENCES proj"
        "ect (enterprise_id, project_id)"
    ),
    (
        "ALTER TABLE knowledge_document ADD CONSTRAINT fk_knowledge_document_proj"
        "ect_parent_1 FOREIGN KEY(project_id, source_id) REFERENCES knowledge_sou"
        "rce (project_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_document_revision ADD CONSTRAINT fk_knowledge_docu"
        "ment_revision_project_parent_1 FOREIGN KEY(project_id, source_id) REFERE"
        "NCES knowledge_source (project_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_document_revision ADD CONSTRAINT fk_knowledge_docu"
        "ment_revision_source_owner FOREIGN KEY(project_id, document_id, source_i"
        "d) REFERENCES knowledge_document (project_id, document_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_citation_snapshot ADD CONSTRAINT fk_citation_answe"
        "r_selection FOREIGN KEY(project_id, trace_id, revision_id, document_id, "
        "source_id) REFERENCES knowledge_answer_source (project_id, trace_id, rev"
        "ision_id, document_id, source_id) ON DELETE CASCADE"
    ),
    (
        "ALTER TABLE knowledge_citation_snapshot ADD CONSTRAINT fk_knowledge_cita"
        "tion_snapshot_project_parent_1 FOREIGN KEY(project_id, source_id) REFERE"
        "NCES knowledge_source (project_id, source_id)"
    ),
    (
        "ALTER TABLE knowledge_citation_snapshot ADD CONSTRAINT fk_knowledge_cita"
        "tion_snapshot_revision_owner FOREIGN KEY(project_id, revision_id, docume"
        "nt_id, source_id) REFERENCES knowledge_document_revision (project_id, re"
        "vision_id, document_id, source_id)"
    ),
)

SCOPE_COLUMNS = ("enterprise_id", "project_id", "actor_id", "identity_mode", "identity_origin")


def _source_id(project_id, document_id):
    return (
        "src_"
        + hashlib.sha256(f"legacy-source-v1\0{project_id}\0{document_id}".encode()).hexdigest()[:32]
    )


def _fail(category, count=1):
    raise ValueError(
        f"knowledge-source-migration:{category}:count={count}; restore "
        f"owned pre-migration snapshot before retry"
    )


def _rows(connection, name):
    return [dict(row) for row in connection.execute(text(f"SELECT * FROM {name}")).mappings()]


def validate_legacy(documents, revisions, answers, citations):
    """Accept only the two historically supported selection shapes without rewriting them."""
    docs = {(row["project_id"], row["document_id"]): row for row in documents}
    revs = {(row["project_id"], row["revision_id"]): row for row in revisions}
    if len(docs) != len(documents) or len(revs) != len(revisions):
        _fail("duplicate-identity")
    sources = {}
    for row in documents:
        source_id = _source_id(row["project_id"], row["document_id"])
        if source_id in sources:
            _fail("duplicate-mapping")
        sources[source_id] = row
        current = row["current_revision_id"]
        if current is not None and (
            (row["project_id"], current) not in revs
            or revs[(row["project_id"], current)]["document_id"] != row["document_id"]
        ):
            _fail("orphan-current-revision")
    for row in revisions:
        document = docs.get((row["project_id"], row["document_id"]))
        if document is None or row["enterprise_id"] != document["enterprise_id"]:
            _fail("orphan-revision")
    associations = []
    selected = set()
    for answer in answers:
        values = answer["selected_revisions_json"]
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except ValueError:
                _fail("selection-shape")
        if not isinstance(values, list) or not 1 <= len(values) <= 20:
            _fail("selection-shape")
        seen_documents = set()
        shape = type(values[0])
        for ordinal, item in enumerate(values):
            if type(item) is not shape:
                _fail("selection-shape")
            if isinstance(item, str):
                revision_id = item
            elif (
                isinstance(item, dict)
                and set(item) == {"documentId", "revisionId", "sourceContentHash"}
                and all(type(v) is str for v in item.values())
            ):
                revision_id = item["revisionId"]
            else:
                _fail("selection-shape")
            revision_row = revs.get((answer["project_id"], revision_id))
            if revision_row is None or answer["enterprise_id"] != revision_row["enterprise_id"]:
                _fail("orphan-selection")
            if isinstance(item, dict) and (item["documentId"], item["sourceContentHash"]) != (
                revision_row["document_id"],
                revision_row["source_content_hash"],
            ):
                _fail("contradictory-selection")
            if revision_row["document_id"] in seen_documents:
                _fail("duplicate-selection")
            seen_documents.add(revision_row["document_id"])
            association = {
                **{key: answer[key] for key in SCOPE_COLUMNS},
                "trace_id": answer["trace_id"],
                "revision_id": revision_id,
                "document_id": revision_row["document_id"],
                "source_id": _source_id(answer["project_id"], revision_row["document_id"]),
                "source_content_hash": revision_row["source_content_hash"],
                "ordinal": ordinal,
            }
            associations.append(association)
            selected.add(
                (
                    answer["project_id"],
                    answer["trace_id"],
                    revision_id,
                    revision_row["document_id"],
                    revision_row["source_content_hash"],
                )
            )
    for citation in citations:
        if (
            citation["project_id"],
            citation["trace_id"],
            citation["revision_id"],
            citation["document_id"],
            citation["source_content_hash"],
        ) not in selected:
            _fail("orphan-citation-selection")
    return sources, associations


def _insert(connection, name, values):
    columns = ", ".join(values)
    parameters = ", ".join(":" + key for key in values)
    connection.execute(text(f"INSERT INTO {name} ({columns}) VALUES ({parameters})"), values)


def _expected_source(source_id, document):
    return {
        **{key: document[key] for key in SCOPE_COLUMNS},
        "source_id": source_id,
        "name": document["filename"],
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
        "deleted_at": document["deleted_at"],
    }


def upgrade():
    connection = op.get_bind()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if (
        tables.intersection(NEW_TABLES)
        or any(
            "source_id" in {column["name"] for column in inspector.get_columns(name)}
            for name in (
                "knowledge_document",
                "knowledge_document_revision",
                "knowledge_citation_snapshot",
            )
        )
        or "resource_id" in {column["name"] for column in inspector.get_columns("project_audit")}
        or {"chunk_manifest_digest", "projection_digest"}.intersection(
            column["name"] for column in inspector.get_columns("knowledge_document_revision")
        )
        or any(
            column["type"].length != 64
            for name in ("outbox", "outbox_archive", "outbox_dead_letter")
            for column in inspector.get_columns(name)
            if column["name"] == "aggregate_id"
        )
    ):
        _fail("partial-schema")
    documents = _rows(connection, "knowledge_document")
    revisions = _rows(connection, "knowledge_document_revision")
    answers = _rows(connection, "knowledge_answer_snapshot")
    citations = _rows(connection, "knowledge_citation_snapshot")
    sources, associations = validate_legacy(documents, revisions, answers, citations)
    for statement in CREATE_SQL:
        op.execute(statement)
    for name in (
        "knowledge_document",
        "knowledge_document_revision",
        "knowledge_citation_snapshot",
    ):
        op.execute(f"ALTER TABLE {name} ADD COLUMN source_id VARCHAR(64) NULL")
    op.execute(
        "ALTER TABLE knowledge_document_revision ADD COLUMN "
        "chunk_manifest_digest VARCHAR(71) NULL, ADD COLUMN "
        "projection_digest VARCHAR(71) NULL"
    )
    op.execute("ALTER TABLE project_audit ADD COLUMN resource_id VARCHAR(128) NULL")
    for name in ("outbox", "outbox_archive", "outbox_dead_letter"):
        op.execute(f"ALTER TABLE {name} MODIFY COLUMN aggregate_id VARCHAR(128) NOT NULL")
    for source_id, document in sources.items():
        _insert(connection, "knowledge_source", _expected_source(source_id, document))
        _insert(
            connection,
            "knowledge_source_legacy_map",
            {
                **{key: document[key] for key in SCOPE_COLUMNS},
                "legacy_source_id": document["document_id"],
                "document_id": document["document_id"],
                "source_id": source_id,
            },
        )
        connection.execute(
            text(
                "UPDATE knowledge_document SET source_id=:source WHERE "
                "project_id=:project AND document_id=:document"
            ),
            {
                "source": source_id,
                "project": document["project_id"],
                "document": document["document_id"],
            },
        )
        for table in ("knowledge_document_revision", "knowledge_citation_snapshot"):
            connection.execute(
                text(
                    f"UPDATE {table} SET source_id=:source WHERE project_id=:project "
                    f"AND document_id=:document"
                ),
                {
                    "source": source_id,
                    "project": document["project_id"],
                    "document": document["document_id"],
                },
            )
    for association in associations:
        _insert(connection, "knowledge_answer_source", association)
    for name in (
        "knowledge_document",
        "knowledge_document_revision",
        "knowledge_citation_snapshot",
    ):
        count = connection.scalar(text(f"SELECT count(*) FROM {name} WHERE source_id IS NULL"))
        if count:
            _fail("incomplete-backfill", count)
    for statement in CONSTRAINT_SQL:
        op.execute(statement)
    for name in (
        "knowledge_document",
        "knowledge_document_revision",
        "knowledge_citation_snapshot",
    ):
        op.execute(f"ALTER TABLE {name} MODIFY COLUMN source_id VARCHAR(64) NOT NULL")


def downgrade():
    connection = op.get_bind()
    for table in ("outbox", "outbox_archive", "outbox_dead_letter"):
        if connection.scalar(
            text(
                f"SELECT count(*) FROM {table} WHERE CHAR_LENGTH(aggregate_id)>64 "
                f"OR message_type IN "
                f"('knowledge.document-revision.accepted','knowledge.document-revis"
                f"ion.ready')"
            )
        ):
            _fail("new-event-facts")
    if (
        connection.scalar(text("SELECT count(*) FROM project_audit WHERE resource_id IS NOT NULL"))
        or connection.scalar(text("SELECT count(*) FROM knowledge_search_audit"))
        or connection.scalar(
            text(
                "SELECT count(*) FROM knowledge_document_revision WHERE "
                "projection_digest IS NOT NULL OR chunk_manifest_digest IS NOT "
                "NULL"
            )
        )
    ):
        _fail("new-audit-or-receipt-facts")
    documents = _rows(connection, "knowledge_document")
    revisions = _rows(connection, "knowledge_document_revision")
    sources, associations = validate_legacy(
        documents,
        revisions,
        _rows(connection, "knowledge_answer_snapshot"),
        _rows(connection, "knowledge_citation_snapshot"),
    )
    actual_sources = {row["source_id"]: row for row in _rows(connection, "knowledge_source")}
    if actual_sources != {
        key: _expected_source(key, document) for key, document in sources.items()
    }:
        _fail("changed-source-facts")
    mappings = _rows(connection, "knowledge_source_legacy_map")
    expected_mappings = [
        {
            **{key: document[key] for key in SCOPE_COLUMNS},
            "legacy_source_id": document["document_id"],
            "document_id": document["document_id"],
            "source_id": _source_id(document["project_id"], document["document_id"]),
        }
        for document in documents
    ]
    if sorted(mappings, key=lambda row: row["legacy_source_id"]) != sorted(
        expected_mappings, key=lambda row: row["legacy_source_id"]
    ):
        _fail("changed-source-map")
    actual = _rows(connection, "knowledge_answer_source")

    def order(row):
        return (row["project_id"], row["trace_id"], row["ordinal"])

    if sorted(actual, key=order) != sorted(associations, key=order):
        _fail("changed-answer-facts")
    inspector = inspect(connection)
    for name in (
        "knowledge_citation_snapshot",
        "knowledge_document_revision",
        "knowledge_document",
        *NEW_TABLES,
    ):
        for fk in inspector.get_foreign_keys(name):
            if "source_id" in fk["constrained_columns"] or name in NEW_TABLES:
                op.execute(f"ALTER TABLE {name} DROP FOREIGN KEY {fk['name']}")
    # MySQL may replace an old implicit FK index with a wider new FK index.
    # Restore surviving FK supports before removing new implicit index names.
    for name in (
        "knowledge_document",
        "knowledge_document_revision",
        "knowledge_citation_snapshot",
    ):
        current = inspect(connection)
        indexes = {item["name"]: item for item in current.get_indexes(name)}
        new_indexes = {
            fk["name"]
            for fk in inspector.get_foreign_keys(name)
            if "source_id" in fk["constrained_columns"]
        }
        for fk in current.get_foreign_keys(name):
            columns = fk["constrained_columns"]
            supported = any(
                key not in new_indexes and index["column_names"][: len(columns)] == columns
                for key, index in indexes.items()
            )
            if not supported:
                column_sql = ", ".join(columns)
                op.execute(f"CREATE INDEX {fk['name']} ON {name} ({column_sql})")
        for index_name in new_indexes.intersection(indexes):
            op.execute(f"ALTER TABLE {name} DROP INDEX {index_name}")
    for name in reversed(NEW_TABLES):
        op.execute(f"DROP TABLE {name}")
    for name, index in (
        ("knowledge_document", "uq_document_source_owner"),
        ("knowledge_document_revision", "uq_revision_source_owner"),
    ):
        op.execute(f"ALTER TABLE {name} DROP INDEX {index}")
    for name in (
        "knowledge_document",
        "knowledge_document_revision",
        "knowledge_citation_snapshot",
    ):
        op.execute(f"ALTER TABLE {name} DROP COLUMN source_id")
    op.execute(
        "ALTER TABLE knowledge_document_revision DROP COLUMN "
        "chunk_manifest_digest, DROP COLUMN projection_digest"
    )
    op.execute("ALTER TABLE project_audit DROP COLUMN resource_id")
    for name in ("outbox", "outbox_archive", "outbox_dead_letter"):
        op.execute(f"ALTER TABLE {name} MODIFY COLUMN aggregate_id VARCHAR(64) NOT NULL")
