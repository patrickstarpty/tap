"""Real v1/v2 Source projection proof on the approved disposable Task6A instance.

Committed vectors are replayed as numeric test inputs; no model or semantic
quality claim is made. SQL READY facts are explicitly seeded for this gate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import re
import subprocess
import sys
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import insert, update
from test_source_command_idempotency import repository, upload  # noqa: F401

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters import mysql_documents as ledger
from tap.modules.knowledge.adapters.milvus.config import MilvusIndexTarget, MilvusSearchConfig
from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
from tap.modules.knowledge.adapters.milvus.transport import (
    PyMilvusReader,
    create_operations_milvus_sdk,
)
from tap.modules.knowledge.adapters.milvus_documents import (
    MilvusDocumentIndex,
    ReadyRevisionArtifacts,
    TapperMilvusConfig,
)
from tap.modules.knowledge.adapters.mysql_documents import knowledge_document
from tap.modules.knowledge.adapters.mysql_operations import MysqlOperationRepository
from tap.modules.knowledge.adapters.mysql_projection import MysqlProjectionCoordinator
from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
from tap.modules.knowledge.domain.documents import (
    ChunkDraft,
    DocumentId,
    RevisionId,
    chunk_id_for,
    logical_chunk_id_for,
)
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    QueryPlan,
    ResolvedResourceRef,
    ResourceMode,
    RetrievalProfileId,
    RevisionKind,
    SourceFamily,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DeletionTarget,
    EmbeddingArtifact,
    IngestionWork,
    JobKind,
    JobStage,
)
from tap.modules.knowledge.ports.errors import SearchUnavailable
from tap.modules.knowledge.ports.models import SearchExecution
from tap.operations.milvus.bootstrap import bootstrap_local_rbac
from tap.operations.milvus.client import (
    connect_local_admin,
    create_tapper_document_clients,
    local_role_credentials,
    suppress_pymilvus_rpc_logging,
)
from tap.operations.milvus.doc_schema import doc_schema_sha256
from tap.operations.milvus.embeddings import load_vector_snapshot
from tap.operations.milvus.fixtures import content_hash, load_doc_fixture, load_query_cases


class Audit:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


def _owned_cli_runner(connection):
    """Spawned before gRPC initialization; never opens a Milvus client itself."""
    try:
        while (request := connection.recv()) is not None:
            command, environment = request
            assert command[1] == "scripts/tapper_collection.py"
            assert command[2] in {"migrate-v1-to-v2", "rollback-v2-to-v1"}
            result = subprocess.run(
                command, env=environment, capture_output=True, text=True, timeout=120
            )
            connection.send((result.returncode, result.stdout, result.stderr))
    finally:
        connection.close()


def execution(owner, version, vector):
    policy = build_demo_policy_context(
        (owner,),
        corpus_version="tapper-demo-v2" if version == "doc-schema-v2" else "tapper-demo-v1",
    )
    query = "policy"
    digest = "sha256:" + hashlib.sha256(query.encode()).hexdigest()
    plan = QueryPlan(
        query_plan_id="owned-plan",
        operation_id="owned-operation",
        tenant_id=policy.tenant_id,
        project_id=policy.project_id,
        policy_decision_id=policy.decision_id,
        policy_version=policy.policy_version,
        acl_digest=policy.acl_digest,
        answer_mode=AnswerMode.QUICK,
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        source_families=(SourceFamily.DOC,),
        resources=(
            ResolvedResourceRef(
                family=SourceFamily.DOC,
                source_id=owner.source_id,
                mode=ResourceMode.SCOPE,
                revision_kind=RevisionKind.BLOB_VERSION,
                revision=owner.revision_id,
                source_content_hash=owner.source_content_hash,
                anchor=None,
            ),
        ),
        effective_environment="global",
        corpus_version=policy.active_corpus_version,
        candidate_limit=10,
        raw_request_hash=digest,
        sanitized_query=query,
        sanitized_query_hash=digest,
        redaction_version="owned-redaction",
        embedding_model_id="tapper-embedding",
        embedding_dimension=1536,
    )
    snapshot = ContextSnapshot(
        context_snapshot_id="owned-snapshot",
        operation_id=plan.operation_id,
        tenant_id=policy.tenant_id,
        project_id=policy.project_id,
        policy_decision_id=policy.decision_id,
        policy_version=policy.policy_version,
        acl_digest=policy.acl_digest,
        layers=(
            ContextLayer(
                kind=ContextLayerKind.CURRENT_TURN, ref_ids=(), content_hash=digest, token_count=1
            ),
        ),
    )
    return SearchExecution(policy=policy, plan=plan, context_snapshot=snapshot, query_vector=vector)


@pytest.mark.asyncio
async def test_real_owned_source_selection_cutover_and_explicit_rollback(repository):  # noqa: F811
    assert os.environ["TAP_TASK6A_OWNED_PROJECT"].startswith("tap-task6a-")
    assert os.environ["MILVUS_URI"] == "http://127.0.0.1:40530"
    repo, engine = repository
    settings = {
        "MILVUS_URI": os.environ["MILVUS_URI"],
        "MILVUS_DATABASE": "default",
        "MILVUS_ROOT_PASSWORD": "Task6a-OwnedRoot1!",
        "MILVUS_INITIAL_ROOT_PASSWORD": "Milvus",
        "TAP_ALLOW_INITIAL_MILVUS_ROOT": "1",
        "MILVUS_READER_USERNAME": "tap_reader",
        "MILVUS_READER_PASSWORD": "Task6a-OwnedReader1!",
        "MILVUS_WRITER_USERNAME": "tap_writer",
        "MILVUS_WRITER_PASSWORD": "Task6a-OwnedWriter1!",
        "MILVUS_PROVISIONER_USERNAME": "tap_provisioner",
        "MILVUS_PROVISIONER_PASSWORD": "Task6a-OwnedProvisioner1!",
    }
    process_context = multiprocessing.get_context("spawn")
    parent_pipe, child_pipe = process_context.Pipe()
    cli_runner = process_context.Process(target=_owned_cli_runner, args=(child_pipe,))
    cli_runner.start()
    child_pipe.close()
    sdk = create_operations_milvus_sdk()
    admin = None
    clients = None
    coordinator = None
    search_readers = []
    artifacts = None
    artifact_target = None
    source_id = None
    with suppress_pymilvus_rpc_logging():
        try:
            admin = await connect_local_admin(settings, client_factory=sdk.client_factory)
            await bootstrap_local_rbac(admin, local_role_credentials(settings))
            clients = await create_tapper_document_clients(
                uri=settings["MILVUS_URI"],
                database="default",
                provisioner_username="tap_provisioner",
                provisioner_password=SecretStr(settings["MILVUS_PROVISIONER_PASSWORD"]),
                writer_username="tap_writer",
                writer_password=SecretStr(settings["MILVUS_WRITER_PASSWORD"]),
                reader_username="tap_reader",
                reader_password=SecretStr(settings["MILVUS_READER_PASSWORD"]),
                sdk=sdk,
            )
            coordinator = MysqlProjectionCoordinator(
                engine,
                scope=VALIDATION_SCOPE,
                authority_namespace=os.environ["TAP_TASK6A_OWNED_PROJECT"],
            )
            old = MilvusDocumentIndex(
                config=TapperMilvusConfig.for_schema("doc-schema-v1"),
                provisioner=clients.provisioner,
                writer=clients.writer,
                reader=clients.reader,
                coordinator=coordinator,
            )
            new = MilvusDocumentIndex(
                config=TapperMilvusConfig.for_schema("doc-schema-v2"),
                provisioner=clients.provisioner,
                writer=clients.writer,
                reader=clients.reader,
                coordinator=coordinator,
            )
            await old.ensure_target()
            request = upload()
            reservation = await repo.reserve_upload(request)
            accepted = await repo.activate_upload(
                reservation, ArtifactLocator("blob:" + reservation.document_id)
            )
            source_id = (await repo.source_command_result(request.command.key)).body["source"][
                "sourceId"
            ]
            async with engine.begin() as connection:
                await connection.execute(
                    update(knowledge_document)
                    .where(knowledge_document.c.document_id == accepted.document_id)
                    .values(status="ready")
                )
            (owner,) = await repo.load_source_revisions((source_id,))
            text = "The policy requires recorded approval."
            anchor = '{"headingPath":[],"type":"document"}'
            digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
            chunk = ChunkDraft(
                chunk_id=chunk_id_for(RevisionId(owner.revision_id), anchor, digest),
                logical_chunk_id=logical_chunk_id_for(DocumentId(owner.document_id), anchor),
                root_id=DocumentId(owner.document_id),
                parent_id=None,
                content=text,
                anchor_json=anchor,
                source_content_hash=owner.source_content_hash,
                chunk_content_hash=digest,
            )
            fixture_root = Path("apps/backend/tests/fixtures/milvus")
            manifest = load_doc_fixture(fixture_root / "doc-fixture-v1.json")
            cases = load_query_cases(fixture_root / "query-cases-v1.json")
            vectors = load_vector_snapshot(
                fixture_root / "vectors-research-embedding-v1.json",
                chunk_hashes={item.chunk_id: item.chunk_content_hash for item in manifest.chunks},
                query_hashes={case.case_id: content_hash(case.query) for case in cases},
            )
            vector = next(iter(vectors.chunks.values())).vector
            work = IngestionWork(
                job_id=accepted.job_id,
                lease_token="owned-proof",
                kind=JobKind.INGESTION,
                stage=JobStage.PUBLISHING,
                document_id=owner.document_id,
                revision_id=owner.revision_id,
                filename="note.txt",
                media_type="text/plain",
                source_content_hash=owner.source_content_hash,
                original_locator=ArtifactLocator("blob:" + owner.document_id),
                normalized_locator=None,
                chunks_locator=None,
                embeddings_locator=None,
                parser_version="tapper-parser-v1",
                chunker_version="tapper-structure-512-v1",
                pipeline_version="tapper-ingestion-v1",
                manifest=(),
                enterprise_id="local",
                project_id="tapper-demo",
                source_id=source_id,
            )
            embeddings = EmbeddingArtifact(
                "tapper-embedding", 1536, (vector,), (str(chunk.chunk_id),)
            )
            await old.upsert_revision(work, (chunk,), embeddings, index_version="tapper-index-v1")

            async def snapshot():
                assert await repo.load_current_source_revisions(
                    ((source_id, owner.revision_id, owner.source_content_hash),)
                ) == (owner,)
                operations = MysqlOperationRepository(engine, scope=VALIDATION_SCOPE)
                (stored,) = await operations.ready_work(20)
                assert stored.document_id == owner.document_id
                assert artifacts is not None
                assert stored.chunks_locator is not None and stored.embeddings_locator is not None
                return (
                    ReadyRevisionArtifacts(
                        work=stored,
                        chunks=await artifacts.read_chunks(stored.chunks_locator),
                        embeddings=await artifacts.read_embeddings(stored.embeddings_locator),
                        index_version="tapper-index-v1",
                    ),
                )

            async def search(version):
                profile = TapperMilvusConfig.for_schema(version)
                target = MilvusIndexTarget(
                    family=SourceFamily.DOC,
                    alias=profile.alias,
                    physical_name_prefix=profile.physical_collection,
                    schema_version=version,
                    schema_sha256=doc_schema_sha256(version),
                    corpus_version=profile.corpus_version,
                    embedding_model_version="tapper-embedding",
                    vector_dimension=1536,
                    exact_generation_names=True,
                )
                config = MilvusSearchConfig(
                    uri=settings["MILVUS_URI"],
                    database="default",
                    username="tap_reader",
                    password=SecretStr(settings["MILVUS_READER_PASSWORD"]),
                    targets={SourceFamily.DOC: target},
                )
                reader = PyMilvusReader(config)
                search_readers.append(reader)
                audit = Audit()
                hits = await MilvusSearchAdapter(config, reader, audit, owners=repo).search(
                    execution(owner, version, vector)
                )
                assert len(hits) == 1 and hits[0].source.source_id == source_id
                assert (
                    hits[0].chunk_id == str(chunk.chunk_id)
                    and audit.events[-1].outcome == "success"
                )
                for changed in (
                    replace(owner, source_id="src_" + "f" * 32),
                    replace(owner, revision_id="rev_" + "f" * 64),
                    replace(owner, source_content_hash="sha256:" + "f" * 64),
                ):
                    with pytest.raises(SearchUnavailable):
                        await MilvusSearchAdapter(config, reader, audit, owners=repo).search(
                            execution(changed, version, vector)
                        )
                    assert audit.events[-1].outcome == "failure"
                return hits

            # Seed SQL/artifacts once before direct migration and reuse them for the CLI.
            from tap.entrypoints.tapper_runtime import TapperSettings, _create_blob
            from tap.platform.db.project_scope import scope_values

            cli_env = {
                key: os.environ[key]
                for key in (
                    "PATH",
                    "HOME",
                    "TMPDIR",
                    "LANG",
                    "TAP_DATABASE_URL",
                    "TAP_ALEMBIC_DATABASE_URL",
                )
                if key in os.environ
            }
            cli_env.update(settings)
            cli_env.update(
                {
                    "TAP_TAPPER_COMPOSE_PROJECT": os.environ["TAP_TASK6A_OWNED_PROJECT"],
                    "TAPPER_SCHEMA_VERSION": "doc-schema-v2",
                    "TAPPER_OBJECT_STORE_PROVIDER": "minio",
                    "TAPPER_LEGACY_AZURE_ENABLED": "0",
                    "TAPPER_S3_ENDPOINT": "http://127.0.0.1:41000",
                    "TAPPER_S3_BUCKET": "task6a-owned-artifacts",
                    "TAPPER_S3_REGION": "us-east-1",
                    "TAPPER_S3_ACCESS_KEY": "task6a-object-user",
                    "TAPPER_S3_SECRET_KEY": "task6a-owned-object-password",
                    "TAPPER_S3_STORE_ID": "task6a-objects",
                    "LITELLM_MODEL": "openai/unused-owned-test",
                    "LITELLM_TAPPER_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
                }
            )
            artifacts = _create_blob(TapperSettings.from_mapping(cli_env))
            await artifacts.ensure_containers()
            chunks_locator = await artifacts.write_chunks(owner.revision_id, (chunk,))
            embeddings_locator = await artifacts.write_embeddings(
                owner.revision_id, embeddings, source_content_hash=owner.source_content_hash
            )
            artifact_target = DeletionTarget(
                owner.document_id,
                owner.revision_id,
                (str(chunk.chunk_id),),
                (chunks_locator, embeddings_locator),
                source_id,
                "local",
                "tapper-demo",
            )
            async with engine.begin() as connection:
                await connection.execute(
                    update(ledger.knowledge_document_revision)
                    .where(ledger.knowledge_document_revision.c.revision_id == owner.revision_id)
                    .values(
                        chunks_blob_locator=chunks_locator,
                        embeddings_blob_locator=embeddings_locator,
                    )
                )
                await connection.execute(
                    insert(ledger.knowledge_chunk_manifest).values(
                        **scope_values(VALIDATION_SCOPE),
                        chunk_id=str(chunk.chunk_id),
                        logical_chunk_id=str(chunk.logical_chunk_id),
                        revision_id=owner.revision_id,
                        ordinal=0,
                        root_id=owner.document_id,
                        parent_id=None,
                        anchor_json=json.loads(anchor),
                        chunk_content_hash=digest,
                        embedding_model_version="tapper-embedding",
                        index_version="tapper-index-v1",
                        created_at=request.now,
                    )
                )
            await search("doc-schema-v1")
            migrated = await new.migrate_from_snapshot(old, snapshot)
            assert migrated.row_count == 1
            await search("doc-schema-v2")
            assert await clients.reader.collection_exists("kb_doc_v1_tapper_demo")
            await new.rollback_to(old, "kb_doc_v1_tapper_demo", snapshot)
            await search("doc-schema-v1")
            assert await clients.reader.collection_exists(migrated.physical_collection)
            cli_receipts = []
            for action, version in (
                ("migrate-v1-to-v2", "doc-schema-v2"),
                ("rollback-v2-to-v1", "doc-schema-v1"),
            ):
                cli_env["TAPPER_SCHEMA_VERSION"] = version
                command = [
                    sys.executable,
                    "scripts/tapper_collection.py",
                    action,
                    "--owned-state",
                    os.environ["TAP_TASK6A_OWNED_STATE"],
                    "--limit",
                    "20",
                ]
                if action == "rollback-v2-to-v1":
                    command.extend(["--retained-collection", "kb_doc_v1_tapper_demo"])
                parent_pipe.send((command, cli_env))
                assert await asyncio.to_thread(parent_pipe.poll, 125), "CLI runner timed out"
                returncode, stdout, stderr = parent_pipe.recv()
                print("Owned migration CLI:", action, returncode, stdout, stderr)
                assert returncode == 0
                assert stderr == ""
                cli_receipts.append(json.loads(stdout))
                await search(version)
            assert cli_receipts[0]["physicalCollection"].startswith("kb_doc_v2_tapper_demo_")
            assert cli_receipts[1]["physicalCollection"] == "kb_doc_v1_tapper_demo"
        finally:
            with suppress(BrokenPipeError, EOFError):
                parent_pipe.send(None)
            await asyncio.to_thread(cli_runner.join, 5)
            if cli_runner.is_alive():
                cli_runner.terminate()
                await asyncio.to_thread(cli_runner.join, 5)
            parent_pipe.close()
            assert not cli_runner.is_alive()
            if source_id is not None:
                await repo.delete_source(source_id)
            if artifacts is not None:
                if artifact_target is not None:
                    await artifacts.delete_revision_artifacts(artifact_target)
                await artifacts.aclose()
            for reader in search_readers:
                await reader.close()
            if clients is not None:
                assert admin is not None
                names = await asyncio.to_thread(admin._client.list_collections)
                assert all(
                    re.fullmatch(r"kb_doc_v[12]_tapper_demo(?:_[0-9a-f]{12})?", name)
                    for name in names
                )
                target = await clients.reader.describe_alias("kb_doc_tapper_demo_active")
                if target is not None:
                    assert target in names
                    await clients.provisioner.drop_alias("kb_doc_tapper_demo_active")
                for name in names:
                    await clients.provisioner.revoke_collection(name, "tap_reader")
                    await clients.provisioner.revoke_collection(name, "tap_writer")
                    await clients.provisioner.drop_collection(name)
                assert await asyncio.to_thread(admin._client.list_collections) == []
                print("Owned Milvus proof collections removed:", names)
                for client in (clients.reader, clients.writer, clients.provisioner):
                    await client.close()
            if coordinator is not None:
                await coordinator.close()
            if admin is not None:
                await admin.close()
