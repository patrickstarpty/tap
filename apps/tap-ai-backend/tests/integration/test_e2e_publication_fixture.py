from __future__ import annotations

import asyncio
import hashlib
import json
import os
import runpy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, update
from sqlalchemy.engine import make_url

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_chunk_manifest,
    knowledge_document,
    knowledge_document_revision,
    knowledge_parse_inventory,
    knowledge_source,
)
from tap.modules.knowledge.adapters.mysql_projection import knowledge_projection_state
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.domain.parse_inventory import (
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    parse_inventory_digest,
)
from tap.modules.knowledge.domain.sources import chunk_manifest_digest, projection_digest
from tap.modules.knowledge.ports.documents import ManifestChunk
from tap.platform.db.project_scope import scope_values
from tap.platform.db.session import create_engine_and_session_factory
from tests.owned_mysql import owned_project_database_url

SCRIPT = Path(__file__).resolve().parents[4] / "scripts/prepare-tapper-e2e-publication.py"
PREPARE = runpy.run_path(str(SCRIPT))["prepare"]
ALIAS = "kb_doc_tapper_demo_active"
GENERATION = "kb_doc_v1_tapper_demo_000000000001"
SCHEMA = "doc-schema-v1"
INDEX = "tapper-index-v1"
NOW = datetime(2026, 9, 25, 9)
MYSQL_CONTAINER_ID = "a" * 64


def _owned_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str) -> None:
    state_dir = tmp_path / "tap-tapper-e2e.fixture"
    state_dir.mkdir(mode=0o700)
    marker = state_dir / "database-owner.json"
    marker.write_text(
        json.dumps(
            {
                "project": "tap-tapper-e2e",
                "databaseUrlSha256": hashlib.sha256(url.encode()).hexdigest(),
                "hostPort": make_url(url).port,
                "runnerPid": os.getpid(),
                "mysqlContainerId": MYSQL_CONTAINER_ID,
                "dockerContext": "default",
            }
        )
    )
    marker.chmod(0o600)
    monkeypatch.setenv("TAP_DEMO_MODE", "e2e")
    monkeypatch.setenv("TAP_TAPPER_COMPOSE_PROJECT", "tap-tapper-e2e")
    monkeypatch.setenv("TAPPER_E2E_OWNERSHIP_FILE", str(marker))
    monkeypatch.setitem(
        PREPARE.__globals__,
        "TapperSettings",
        SimpleNamespace(
            from_mapping=lambda _values: SimpleNamespace(
                database_url=url,
                project_id=VALIDATION_SCOPE.project_id,
                alias=ALIAS,
                compose_project="tap-tapper-e2e",
                schema_version=SCHEMA,
                index_version=INDEX,
            )
        ),
    )


def test_fixture_rejects_shared_loopback_database_before_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ConnectionAttempt(Exception):
        pass

    shared = "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4"
    _owned_environment(monkeypatch, tmp_path, shared)
    monkeypatch.setitem(
        PREPARE.__globals__,
        "create_engine_and_session_factory",
        lambda _url: (_ for _ in ()).throw(ConnectionAttempt()),
    )
    with pytest.raises(ValueError, match="owned E2E database"):
        asyncio.run(PREPARE(("rev_shared",)))

    owned = "mysql+asyncmy://tap:tap-e2e@127.0.0.1:13306/tap?charset=utf8mb4"
    marker = Path(os.environ["TAPPER_E2E_OWNERSHIP_FILE"])
    marker.write_text(
        json.dumps(
            {
                "project": "tap-tapper-e2e",
                "databaseUrlSha256": hashlib.sha256(owned.encode()).hexdigest(),
                "hostPort": 13306,
                "runnerPid": os.getpid(),
                "mysqlContainerId": MYSQL_CONTAINER_ID,
                "dockerContext": "default",
            }
        )
    )
    monkeypatch.setitem(
        PREPARE.__globals__,
        "TapperSettings",
        SimpleNamespace(
            from_mapping=lambda _values: SimpleNamespace(
                database_url=owned,
                project_id=VALIDATION_SCOPE.project_id,
                alias=ALIAS,
                compose_project="tap-tapper-e2e",
                schema_version=SCHEMA,
                index_version=INDEX,
            )
        ),
    )
    with pytest.raises(ValueError, match="owned Compose MySQL"):
        asyncio.run(PREPARE(("rev_owned",)))

    def owned_docker(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if command == ["docker", "context", "show"]:
            return SimpleNamespace(returncode=0, stdout="default\n")
        if command == ["docker", "context", "inspect", "default"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]
                ),
            )
        assert command == [
            "docker",
            "--context",
            "default",
            "container",
            "inspect",
            MYSQL_CONTAINER_ID,
        ]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": MYSQL_CONTAINER_ID,
                        "State": {"Running": True},
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": "tap-tapper-e2e",
                                "com.docker.compose.service": "mysql",
                            }
                        },
                        "NetworkSettings": {
                            "Ports": {"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": "13306"}]}
                        },
                    }
                ]
            ),
        )

    monkeypatch.setitem(PREPARE.__globals__, "subprocess", SimpleNamespace(run=owned_docker))
    with pytest.raises(ConnectionAttempt):
        asyncio.run(PREPARE(("rev_owned",)))


def test_forged_receipt_for_disposable_non_compose_mysql_never_opens_engine(
    owned_project_mysql, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = owned_project_database_url(owned_project_mysql)
    _owned_environment(monkeypatch, tmp_path, url)

    class ConnectionAttempt(Exception):
        pass

    monkeypatch.setitem(
        PREPARE.__globals__,
        "create_engine_and_session_factory",
        lambda _url: (_ for _ in ()).throw(ConnectionAttempt()),
    )
    monkeypatch.setitem(
        PREPARE.__globals__,
        "subprocess",
        SimpleNamespace(run=lambda _command, **_kwargs: SimpleNamespace(returncode=1, stdout="")),
    )
    with pytest.raises(ValueError, match="owned Compose MySQL"):
        asyncio.run(PREPARE(("rev_disposable",)))


async def _seed(engine, *revision_ids: str) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as connection:
        await connection.execute(
            insert(knowledge_projection_state).values(
                **scope_values(VALIDATION_SCOPE),
                alias_name=f"tap-tapper-e2e:{ALIAS}",
                generation=1,
                physical_collection=GENERATION,
            )
        )
        for revision_id in revision_ids:
            source_id = f"src_{revision_id}"
            document_id = f"doc_{revision_id}"
            source_digest = "sha256:" + "a" * 64
            item = ParseInventoryItem.create(
                source_revision_id=revision_id,
                kind=ParseInventoryKind.DOCUMENT,
                locator="document:whole",
                status=ParseInventoryStatus.PARSED,
                artifact_digest=source_digest,
            )
            manifest = (
                ManifestChunk(
                    chunk_id=f"chk_{revision_id}",
                    logical_chunk_id=f"logical_{revision_id}",
                    ordinal=0,
                    root_id=document_id,
                    parent_id=None,
                    anchor_json="{}",
                    chunk_content_hash="sha256:" + "b" * 64,
                    embedding_model_version="text-embedding-v4",
                    index_version=INDEX,
                ),
            )
            await connection.execute(
                insert(knowledge_source).values(
                    **scope_values(VALIDATION_SCOPE),
                    source_id=source_id,
                    name="fixture",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await connection.execute(
                insert(knowledge_document).values(
                    **scope_values(VALIDATION_SCOPE),
                    document_id=document_id,
                    source_id=source_id,
                    filename="fixture.md",
                    media_type="text/markdown",
                    source_content_hash=source_digest,
                    reservation_parser_version="test",
                    reservation_chunker_version="test",
                    reservation_pipeline_version="test",
                    status="ready",
                    stage="ready",
                    chunk_count=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await connection.execute(
                insert(knowledge_document_revision).values(
                    **scope_values(VALIDATION_SCOPE),
                    revision_id=revision_id,
                    document_id=document_id,
                    source_id=source_id,
                    source_content_hash=source_digest,
                    original_blob_locator="fixture/original",
                    parser_version="test",
                    chunker_version="test",
                    pipeline_version="test",
                    parse_inventory_attempt=1,
                    parse_inventory_digest=parse_inventory_digest((item,)),
                    chunk_manifest_digest=chunk_manifest_digest(manifest),
                    projection_digest=projection_digest(revision_id, SCHEMA, INDEX, manifest),
                    created_at=NOW,
                )
            )
            await connection.execute(
                update(knowledge_document)
                .where(knowledge_document.c.document_id == document_id)
                .values(current_revision_id=revision_id)
            )
            await connection.execute(
                insert(knowledge_parse_inventory).values(
                    **scope_values(VALIDATION_SCOPE),
                    inventory_row_id=f"row_{revision_id}",
                    source_revision_id=revision_id,
                    attempt=1,
                    item_id=item.item_id,
                    ordinal=0,
                    item_kind=item.kind.value,
                    locator=item.locator,
                    status=item.status.value,
                    reason=item.reason,
                    artifact_digest=item.artifact_digest,
                    decision_actor_id=item.decision_actor_id,
                    created_at=NOW,
                )
            )
            await connection.execute(
                insert(knowledge_chunk_manifest).values(
                    **scope_values(VALIDATION_SCOPE),
                    chunk_id=manifest[0].chunk_id,
                    logical_chunk_id=manifest[0].logical_chunk_id,
                    revision_id=revision_id,
                    ordinal=0,
                    root_id=document_id,
                    parent_id=None,
                    anchor_json={},
                    chunk_content_hash=manifest[0].chunk_content_hash,
                    embedding_model_version=manifest[0].embedding_model_version,
                    index_version=INDEX,
                    created_at=NOW,
                )
            )


@pytest.mark.asyncio
async def test_fixture_rejects_drifted_manifest_and_projection_facts(
    owned_project_mysql, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = owned_project_database_url(owned_project_mysql)
    _owned_environment(monkeypatch, tmp_path, url)
    # Exercise durable publication behavior on the disposable integration DB;
    # Docker ownership is covered separately at the public entrypoint above.
    monkeypatch.setitem(PREPARE.__globals__, "_require_owned_database", lambda _url: None)
    engine, _ = create_engine_and_session_factory(url)
    try:
        await _seed(engine, "rev_drift")
        async with engine.begin() as connection:
            await connection.execute(
                update(knowledge_chunk_manifest)
                .where(knowledge_chunk_manifest.c.revision_id == "rev_drift")
                .values(chunk_content_hash="sha256:" + "c" * 64)
            )
        with pytest.raises(ValueError, match="manifest"):
            await PREPARE(("rev_drift",))

        revised_manifest = (
            ManifestChunk(
                chunk_id="chk_rev_drift",
                logical_chunk_id="logical_rev_drift",
                ordinal=0,
                root_id="doc_rev_drift",
                parent_id=None,
                anchor_json="{}",
                chunk_content_hash="sha256:" + "b" * 64,
                embedding_model_version="text-embedding-v4",
                index_version="changed-index",
            ),
        )
        async with engine.begin() as connection:
            await connection.execute(
                update(knowledge_chunk_manifest)
                .where(knowledge_chunk_manifest.c.revision_id == "rev_drift")
                .values(
                    chunk_content_hash="sha256:" + "b" * 64,
                    index_version="changed-index",
                )
            )
            await connection.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == "rev_drift")
                .values(chunk_manifest_digest=chunk_manifest_digest(revised_manifest))
            )
        with pytest.raises(ValueError, match="projection"):
            await PREPARE(("rev_drift",))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fixture_retry_is_idempotent_and_concurrent_selections_accumulate(
    owned_project_mysql, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = owned_project_database_url(owned_project_mysql)
    _owned_environment(monkeypatch, tmp_path, url)
    monkeypatch.setitem(PREPARE.__globals__, "_require_owned_database", lambda _url: None)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await _seed(engine, "rev_one", "rev_two")
        first = await PREPARE(("rev_one",))
        retry = await PREPARE(("rev_one",))
        assert retry["publicationId"] == first["publicationId"]
        await asyncio.gather(PREPARE(("rev_one",)), PREPARE(("rev_two",)))
        current = await MysqlKnowledgeReviewRepository(
            sessions, scope=VALIDATION_SCOPE
        ).current_publication()
        assert current is not None
        assert set(current.source_revision_ids) == {"rev_one", "rev_two"}
    finally:
        await engine.dispose()
