"""Real-Milvus integration helpers backed only by committed sanitized fixtures."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import SecretStr
from pymilvus import MilvusClient  # type: ignore[import-untyped]

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import (
    ProjectPolicy,
    ResourceGrant,
    ResourceSubtreeGrant,
    RetrievalPolicyContext,
    VerifiedSubjectFacts,
)
from tap.modules.knowledge.adapters.milvus.audit import (
    MilvusSearchAuditEvent,
    SearchAuditSink,
)
from tap.modules.knowledge.adapters.milvus.config import (
    MilvusIndexTarget,
    MilvusSearchConfig,
)
from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusCollectionDescriptor,
    MilvusHybridRequest,
    MilvusQueryRequest,
    PyMilvusReader,
)
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    Citation,
    DocumentAnchor,
    Evidence,
    ResourceMode,
    ResourceRef,
    SourceFamily,
    anchor_authorization_key,
)
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
    RedactionResult,
    SearchExecution,
    SearchHit,
)
from tap.operations.milvus.client import PyMilvusWriter
from tap.operations.milvus.contracts import MilvusPublishClients
from tap.operations.milvus.embeddings import (
    EMBEDDING_ALIAS,
    EMBEDDING_DIMENSION,
    VectorSnapshot,
    load_vector_snapshot,
)
from tap.operations.milvus.fixtures import (
    DocFixtureChunk,
    DocFixtureManifest,
    QueryCase,
    build_collection_schema,
    content_hash,
    fixture_rows,
    load_doc_fixture,
    load_query_cases,
)
from tap.operations.milvus.publish import tighten_fixture_acl

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "milvus"
_DOC_FIXTURE = _FIXTURES / "doc-fixture-v1.json"
_QUERY_FIXTURE = _FIXTURES / "query-cases-v1.json"
_VECTOR_SNAPSHOT = _FIXTURES / "vectors-research-embedding-v1.json"
_SAFE_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{2,62}\Z")
_REBUILD_FIELDS = (
    "chunk_id",
    "logical_chunk_id",
    "root_id",
    "parent_id",
    "tenant_id",
    "project_id",
    "allowed_group_ids",
    "classification_rank",
    "environment",
    "deleted",
    "index_family",
    "physical_collection",
    "corpus_version",
    "schema_version",
    "embedding_model_version",
    "source_id",
    "source_type",
    "revision_kind",
    "source_revision",
    "source_content_hash",
    "chunk_content_hash",
    "anchor_json",
    "derived_from_chunk_ids",
)
_REBUILD_SOURCE_ONLY_FIELDS = frozenset({"title", "content", "content_role", "dense_vector"})
_CASE_SCOPE = {
    "payment-wrong-group": "blob:fixture/payment/refund",
    "payment-wrong-project": "blob:fixture/payment/refund",
    "payment-wrong-tenant": "blob:fixture/payment/refund",
    "security-over-classification": "blob:fixture/security/keys",
    "release-wrong-environment": "blob:fixture/release/rollback",
    "payment-subtree-card-only": "blob:fixture/payment/card",
    "wrong-corpus": "blob:fixture/payment/refund",
    "deleted-archive": "blob:fixture/payment/archive",
}


def _load_fixture_cli() -> object:
    path = _REPOSITORY_ROOT / "scripts" / "milvus_fixture.py"
    spec = importlib.util.spec_from_file_location("_tap_milvus_fixture_cli", path)
    if spec is None or spec.loader is None:
        raise ImportError("repository Milvus fixture CLI is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_FIXTURE_CLI = _load_fixture_cli()
_FixtureProvisioner = getattr(_FIXTURE_CLI, "_FixtureProvisioner")
_FixtureReader = getattr(_FIXTURE_CLI, "_FixtureReader")


@dataclass(frozen=True, slots=True)
class MilvusRuntimeSettings:
    uri: str
    database: str
    reader_username: str
    reader_password: SecretStr = field(repr=False)
    writer_username: str
    writer_password: SecretStr = field(repr=False)
    provisioner_username: str
    provisioner_password: SecretStr = field(repr=False)
    compose_project: str


@dataclass(frozen=True, slots=True)
class MilvusCaseResult:
    provider_rows: tuple[Mapping[str, object], ...]
    search_hits: tuple[SearchHit, ...]
    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class ChannelCaseResult:
    kind: Literal["bm25", "dense"]
    filter_expression: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AliasObservation:
    physical_collections: frozenset[str]
    corpus_versions: frozenset[str]


@dataclass(frozen=True, slots=True)
class _CaseSpec:
    case: QueryCase
    corpus_version: str
    scope_source_id: str | None
    subtree: bool = False


class _AuditSink(SearchAuditSink):
    async def emit(self, event: MilvusSearchAuditEvent) -> None:
        del event


class _PolicyVerifier:
    async def verify_current(
        self,
        expected: RetrievalPolicyContext,
    ) -> RetrievalPolicyContext:
        return expected


class _Redactor:
    async def redact(self, text: str) -> RedactionResult:
        return RedactionResult(sanitized_text=text, redaction_version="fixture-v1")


class _SnapshotModel:
    embedding_model_id = EMBEDDING_ALIAS
    embedding_dimension = EMBEDDING_DIMENSION

    def __init__(
        self,
        cases: tuple[QueryCase, ...],
        snapshot: VectorSnapshot,
    ) -> None:
        self._by_query = {case.query: snapshot.queries[case.case_id] for case in cases}

    async def embed(self, query: str) -> Embedding:
        record = self._by_query.get(query)
        if record is None or record.input_hash != content_hash(query):
            raise ValueError("query is absent from the committed vector snapshot")
        return Embedding(
            vector=record.vector,
            model_id=self.embedding_model_id,
            provider_request_id=None,
        )

    async def answer(
        self,
        query: str,
        evidence: tuple[Evidence, ...],
        profile_id: str,
    ) -> AnswerGeneration:
        del query, profile_id
        labels = tuple(item.evidence_label for item in evidence)
        return AnswerGeneration(
            text="Sanitized fixture evidence.",
            claims=(GeneratedClaim(text="Sanitized fixture evidence.", evidence_labels=labels),),
            model_id="fixture-answer-v1",
            profile_id="fixture-grounded-v1",
            provider_request_id=None,
        )


class _RecordingReader:
    def __init__(self, reader: PyMilvusReader) -> None:
        self._reader = reader
        self.hybrid_calls: list[tuple[MilvusHybridRequest, tuple[Mapping[str, object], ...]]] = []
        self.query_calls: list[tuple[MilvusQueryRequest, tuple[Mapping[str, object], ...]]] = []

    async def describe_alias(self, alias: str) -> str:
        return await self._reader.describe_alias(alias)

    async def describe_collection(
        self,
        collection_name: str,
    ) -> MilvusCollectionDescriptor:
        return await self._reader.describe_collection(collection_name)

    async def hybrid_search(
        self,
        request: MilvusHybridRequest,
    ) -> tuple[Mapping[str, object], ...]:
        rows = await self._reader.hybrid_search(request)
        self.hybrid_calls.append((request, rows))
        return rows

    async def query(
        self,
        request: MilvusQueryRequest,
    ) -> tuple[Mapping[str, object], ...]:
        rows = await self._reader.query(request)
        self.query_calls.append((request, rows))
        return rows

    async def close(self) -> None:
        await self._reader.close()


class _RecordingSearch:
    def __init__(self, search: MilvusSearchAdapter) -> None:
        self._search = search
        self.execution: SearchExecution | None = None
        self.hits: tuple[SearchHit, ...] = ()

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        self.execution = execution
        self.hits = await self._search.search(execution)
        return self.hits


class PublishedFixture:
    """Run versioned cases through real provider, adapter, and Knowledge surfaces."""

    def __init__(
        self,
        settings: MilvusRuntimeSettings,
        manifest: DocFixtureManifest,
        cases: tuple[QueryCase, ...],
        snapshot: VectorSnapshot,
    ) -> None:
        self.settings = settings
        self.manifest = manifest
        self.cases = cases
        self.snapshot = snapshot
        self._cases_by_id = {case.case_id: case for case in cases}
        self.last_filter_expression = ""
        self.last_channel_filters: tuple[str, str] = ("", "")
        self.last_direct_filter = ""
        self.last_physical_collection = ""
        self.last_execution: SearchExecution | None = None

    @classmethod
    def from_environment(cls) -> PublishedFixture:
        settings = _runtime_settings(dict(os.environ))
        manifest = load_doc_fixture(_DOC_FIXTURE)
        cases = load_query_cases(_QUERY_FIXTURE)
        snapshot = _load_snapshot(_VECTOR_SNAPSHOT, manifest, cases)
        return cls(settings, manifest, cases, snapshot)

    def expected_source_ids(self, case_id: str) -> tuple[str, ...]:
        return self._case_spec(case_id).case.expected_source_ids

    async def run_case(self, case_id: str) -> MilvusCaseResult:
        spec = self._case_spec(case_id)
        policy, request = self._policy_and_request(spec)
        reader = _RecordingReader(PyMilvusReader(self._search_config()))
        adapter = MilvusSearchAdapter(self._search_config(), reader, _AuditSink())
        search = _RecordingSearch(adapter)
        identifiers = itertools.count(1)
        knowledge = KnowledgeAPI(
            search=search,
            model=_SnapshotModel(self.cases, self.snapshot),
            policy_verifier=_PolicyVerifier(),
            redactor=_Redactor(),
            id_factory=lambda: f"milvus-gate-{next(identifiers)}",
        )
        try:
            response = await knowledge.answer(request, policy)
            if len(reader.hybrid_calls) != 1 or search.execution is None:
                raise AssertionError("real case did not issue exactly one hybrid request")
            hybrid_request, provider_rows = reader.hybrid_calls[0]
            channel_filters = tuple(
                channel.filter_expression for channel in hybrid_request.channels
            )
            direct_request = MilvusQueryRequest(
                collection_name=hybrid_request.collection_name,
                filter_expression=channel_filters[0],
                output_fields=(
                    "chunk_id",
                    "source_id",
                    "physical_collection",
                    "corpus_version",
                ),
                limit=10,
            )
            await reader.query(direct_request)
            self.last_filter_expression = channel_filters[0]
            self.last_channel_filters = cast(tuple[str, str], channel_filters)
            self.last_direct_filter = direct_request.filter_expression
            self.last_physical_collection = str(hybrid_request.collection_name)
            self.last_execution = search.execution
            return MilvusCaseResult(
                provider_rows=provider_rows,
                search_hits=search.hits,
                citations=response.citations,
            )
        finally:
            await adapter.close()

    async def run_channel(
        self,
        case_id: str,
        channel: str,
    ) -> ChannelCaseResult:
        if channel not in {"bm25", "dense"}:
            raise ValueError("channel must be bm25 or dense")
        await self.run_case(case_id)
        execution = self.last_execution
        if execution is None:
            raise AssertionError("case execution was not recorded")
        client = self._raw_client(
            self.settings.reader_username,
            self.settings.reader_password,
        )
        try:
            if channel == "bm25":
                data: list[object] = [execution.plan.sanitized_query]
                anns_field = "bm25_sparse"
                metric = "BM25"
            else:
                data = [list(execution.query_vector)]
                anns_field = "dense_vector"
                metric = "COSINE"
            raw = await asyncio.to_thread(
                client.search,
                collection_name=self.last_physical_collection,
                data=data,
                filter=self.last_filter_expression,
                limit=10,
                output_fields=["source_id"],
                search_params={"metric_type": metric, "params": {}},
                anns_field=anns_field,
                consistency_level="Strong",
                timeout=10.0,
            )
            source_ids = _search_source_ids(raw)
            return ChannelCaseResult(
                kind=cast(Literal["bm25", "dense"], channel),
                filter_expression=self.last_filter_expression,
                source_ids=source_ids,
            )
        finally:
            await asyncio.to_thread(client.close)

    async def run_temporary_revocation(self, case_id: str) -> MilvusCaseResult:
        source_id = "blob:fixture/payment/refund"
        original = self._chunk(source_id)
        row = self._fixture_row(source_id)
        provisioner_client = self._raw_client(
            self.settings.provisioner_username,
            self.settings.provisioner_password,
        )
        writer_client = self._raw_client(
            self.settings.writer_username,
            self.settings.writer_password,
        )
        reader_client = self._raw_client(
            self.settings.reader_username,
            self.settings.reader_password,
        )
        provisioner = _FixtureProvisioner(provisioner_client, self.settings.database)
        writer = PyMilvusWriter(writer_client)
        reader = _FixtureReader(reader_client)
        clients = MilvusPublishClients(
            provisioner=provisioner,
            writer=writer,
            reader=reader,
        )
        await provisioner.grant_collection(self.manifest.physical_collection, "tap_writer")
        try:
            tightened = replace(original, classification_rank=original.classification_rank + 1)
            await tighten_fixture_acl(
                clients,
                self.manifest,
                tightened,
                self.snapshot.chunks[original.chunk_id].vector,
            )
            return await self.run_case(case_id)
        finally:
            await writer.upsert(self.manifest.physical_collection, (row,))
            await writer.flush(self.manifest.physical_collection)
            await provisioner.revoke_collection(self.manifest.physical_collection, "tap_writer")
            for client in (reader_client, writer_client, provisioner_client):
                await asyncio.to_thread(client.close)

    def expected_rebuild_digest(self) -> str:
        rows = fixture_rows(
            self.manifest,
            {item_id: record.vector for item_id, record in self.snapshot.chunks.items()},
        )
        return _rows_digest(tuple(_expected_rebuild_row(row) for row in rows))

    async def live_rebuild_digest(self) -> str:
        client = self._raw_client(
            self.settings.reader_username,
            self.settings.reader_password,
        )
        try:
            corpus = json.dumps(self.manifest.corpus_version)
            physical = json.dumps(self.manifest.physical_collection)
            raw = await asyncio.to_thread(
                client.query,
                self.manifest.physical_collection,
                filter=(f"corpus_version == {corpus} and physical_collection == {physical}"),
                output_fields=list(_REBUILD_FIELDS),
                limit=50,
                consistency_level="Strong",
                timeout=10.0,
            )
            if not isinstance(raw, list) or len(raw) != len(self.manifest.chunks):
                raise AssertionError("real rebuild row inventory is incomplete")
            return _rows_digest(tuple(_rebuild_row(row) for row in raw))
        finally:
            await asyncio.to_thread(client.close)

    async def restart_standalone(self) -> None:
        project = _validated_project(self.settings.compose_project)
        await _run_command(
            (
                "docker",
                "compose",
                "-p",
                project,
                "--profile",
                "milvus",
                "restart",
                "milvus",
            )
        )
        for _attempt in range(90):
            client = self._raw_client(
                self.settings.reader_username,
                self.settings.reader_password,
            )
            try:
                await asyncio.to_thread(client.list_collections, timeout=3.0)
                return
            except Exception:
                await asyncio.sleep(1)
            finally:
                try:
                    await asyncio.to_thread(client.close)
                except Exception:
                    pass
        raise RuntimeError("Milvus did not become ready after scoped restart")

    async def run_alias_switch_race(
        self,
        case_id: str,
    ) -> tuple[AliasObservation, ...]:
        original = self.manifest.physical_collection
        alternate = f"kb_doc_v1_alias_gate_{uuid4().hex[:12]}"
        provisioner_client = self._raw_client(
            self.settings.provisioner_username,
            self.settings.provisioner_password,
        )
        writer_client = self._raw_client(
            self.settings.writer_username,
            self.settings.writer_password,
        )
        provisioner = _FixtureProvisioner(provisioner_client, self.settings.database)
        writer = PyMilvusWriter(writer_client)
        writer_granted = False
        reader_granted = False
        created = False
        try:
            await provisioner.create_collection(alternate, build_collection_schema(self.manifest))
            created = True
            await provisioner.create_indexes(alternate)
            await provisioner.grant_collection(alternate, "tap_writer")
            writer_granted = True
            await provisioner.grant_collection(alternate, "tap_reader")
            reader_granted = True
            rows = tuple(
                {**row, "physical_collection": alternate}
                for row in fixture_rows(
                    self.manifest,
                    {item_id: record.vector for item_id, record in self.snapshot.chunks.items()},
                )
            )
            await writer.insert(alternate, rows)
            await writer.flush(alternate)
            await provisioner.revoke_collection(alternate, "tap_writer")
            writer_granted = False

            await provisioner.alter_alias(self.manifest.alias, original)
            observations = [await self._observe_case(case_id)]
            await provisioner.alter_alias(self.manifest.alias, alternate)
            observations.append(await self._observe_case(case_id))

            async def switch_alias() -> None:
                for target in (original, alternate, original, alternate):
                    await provisioner.alter_alias(self.manifest.alias, target)
                    await asyncio.sleep(0)

            async def observe() -> AliasObservation:
                return await PublishedFixture(
                    self.settings,
                    self.manifest,
                    self.cases,
                    self.snapshot,
                )._observe_case(case_id)

            raced = await asyncio.gather(*(observe() for _ in range(4)), switch_alias())
            observations.extend(cast(tuple[AliasObservation, ...], tuple(raced[:-1])))
            return tuple(observations)
        finally:
            try:
                await provisioner.alter_alias(self.manifest.alias, original)
            finally:
                if writer_granted:
                    await provisioner.revoke_collection(alternate, "tap_writer")
                if reader_granted:
                    await provisioner.revoke_collection(alternate, "tap_reader")
                if created:
                    await provisioner.drop_collection(alternate)
                await asyncio.to_thread(writer_client.close)
                await asyncio.to_thread(provisioner_client.close)

    async def _observe_case(self, case_id: str) -> AliasObservation:
        result = await self.run_case(case_id)
        if not result.search_hits:
            raise AssertionError("alias race positive control returned no hits")
        return AliasObservation(
            physical_collections=frozenset(
                hit.index_revision.physical_index for hit in result.search_hits
            ),
            corpus_versions=frozenset(
                hit.index_revision.corpus_version for hit in result.search_hits
            ),
        )

    def _case_spec(self, case_id: str) -> _CaseSpec:
        case = self._cases_by_id.get(case_id)
        corpus = self.manifest.corpus_version
        if case_id == "wrong-corpus":
            case = replace(
                self._cases_by_id["refund-allowed"],
                case_id=case_id,
                expected_source_ids=(),
            )
            corpus = "corpus-fixture-wrong"
        elif case_id == "deleted-archive":
            case = replace(
                self._cases_by_id["refund-allowed"],
                case_id=case_id,
                expected_source_ids=(),
            )
        if case is None:
            raise KeyError(f"unknown trusted Milvus case: {case_id}")
        return _CaseSpec(
            case=case,
            corpus_version=corpus,
            scope_source_id=_CASE_SCOPE.get(case_id),
            subtree=case_id == "payment-subtree-card-only",
        )

    def _policy_and_request(
        self,
        spec: _CaseSpec,
    ) -> tuple[RetrievalPolicyContext, AnswerRequest]:
        case = spec.case
        groups = frozenset(case.group_ids)
        resource_grants: tuple[ResourceGrant, ...] = ()
        resource_refs: tuple[ResourceRef, ...] = ()
        if spec.scope_source_id is not None:
            chunk = self._chunk(spec.scope_source_id)
            anchor = _document_anchor(chunk) if spec.subtree else None
            subtrees: tuple[ResourceSubtreeGrant, ...] = ()
            anchor_keys: frozenset[str] = frozenset()
            if anchor is not None:
                anchor_key = anchor_authorization_key(anchor)
                anchor_keys = frozenset({anchor_key})
                subtrees = (
                    ResourceSubtreeGrant(
                        anchor_key=anchor_key,
                        root_ids=(chunk.root_id,),
                        logical_chunk_ids=(chunk.logical_chunk_id,),
                    ),
                )
            resource_grants = (
                ResourceGrant(
                    family="doc",
                    source_id=chunk.source_id,
                    revision_kind="blob_version",
                    revision=chunk.source_revision,
                    source_content_hash=chunk.source_content_hash,
                    allowed_anchor_keys=anchor_keys,
                    subtree_grants=subtrees,
                ),
            )
            resource_refs = (
                ResourceRef(
                    family=SourceFamily.DOC,
                    source_id=chunk.source_id,
                    mode=ResourceMode.SCOPE,
                    requested_revision=chunk.source_revision,
                    anchor=anchor,
                ),
            )
        subject = VerifiedSubjectFacts(
            tenant_id=case.tenant_id,
            user_id="milvus-gate-reader",
            group_ids=groups,
            roles=frozenset({"reader"}),
            token_verified=True,
        )
        environments = frozenset({case.environment or "global"})
        project = ProjectPolicy(
            tenant_id=case.tenant_id,
            project_id=case.project_id,
            permission_granted=True,
            allowed_group_ids=groups,
            classification_ceiling=case.classification_ceiling,
            allowed_environments=environments,
            allowed_source_families=frozenset({"doc"}),
            active_corpus_version=spec.corpus_version,
            acl_digest=f"milvus-gate:{case.case_id}",
            policy_version="milvus-gate-v1",
            decision_id=f"milvus-gate:{case.case_id}",
            resource_grants=resource_grants,
        )
        policy = build_retrieval_policy_context(
            subject,
            project,
            requested_tenant_id=case.tenant_id,
            requested_project_id=case.project_id,
        )
        request = AnswerRequest(
            query=case.query,
            source_families=(SourceFamily.DOC,),
            resource_refs=resource_refs,
            requested_environment=case.environment,
            requested_corpus_version=spec.corpus_version,
            top_k=10,
        )
        return policy, request

    def _search_config(self) -> MilvusSearchConfig:
        target = MilvusIndexTarget(
            family=SourceFamily.DOC,
            alias=self.manifest.alias,
            physical_name_prefix="kb_doc_v1_",
            schema_version=self.manifest.schema_version,
            schema_sha256=self.manifest.schema_sha256,
            corpus_version=self.manifest.corpus_version,
            embedding_model_version=self.manifest.embedding_model_version,
            vector_dimension=self.manifest.vector_dimension,
        )
        return MilvusSearchConfig(
            uri=self.settings.uri,
            database=self.settings.database,
            username=self.settings.reader_username,
            password=self.settings.reader_password,
            targets={SourceFamily.DOC: target},
            candidate_limit=10,
            timeout_seconds=10.0,
            max_connections=1,
        )

    def _raw_client(self, username: str, password: SecretStr) -> MilvusClient:
        return MilvusClient(
            uri=self.settings.uri,
            user=username,
            password=password.get_secret_value(),
            db_name=self.settings.database,
            timeout=10.0,
        )

    def _chunk(self, source_id: str) -> DocFixtureChunk:
        return next(chunk for chunk in self.manifest.chunks if chunk.source_id == source_id)

    def _fixture_row(self, source_id: str) -> Mapping[str, object]:
        chunk = self._chunk(source_id)
        return next(
            row
            for row in fixture_rows(
                self.manifest,
                {item_id: record.vector for item_id, record in self.snapshot.chunks.items()},
            )
            if row["chunk_id"] == chunk.chunk_id
        )


def scoped_volume_reset_command(
    project: str,
    *,
    allow_reset: bool,
    recorder: Callable[[tuple[str, ...]], None] | None = None,
) -> tuple[str, ...]:
    """Build only the exact destructive command after both independent gates pass."""
    if allow_reset is not True:
        raise ValueError("explicit volume reset opt-in is required")
    validated = _validated_project(project)
    command = (
        "docker",
        "compose",
        "-p",
        validated,
        "--profile",
        "milvus",
        "down",
        "-v",
        "--remove-orphans",
    )
    if recorder is not None:
        recorder(command)
    return command


def _runtime_settings(settings: Mapping[str, str]) -> MilvusRuntimeSettings:
    return MilvusRuntimeSettings(
        uri=_required(settings, "MILVUS_URI"),
        database=_required(settings, "MILVUS_DATABASE"),
        reader_username=_required(settings, "MILVUS_READER_USERNAME"),
        reader_password=SecretStr(_required(settings, "MILVUS_READER_PASSWORD")),
        writer_username=_required(settings, "MILVUS_WRITER_USERNAME"),
        writer_password=SecretStr(_required(settings, "MILVUS_WRITER_PASSWORD")),
        provisioner_username=_required(settings, "MILVUS_PROVISIONER_USERNAME"),
        provisioner_password=SecretStr(_required(settings, "MILVUS_PROVISIONER_PASSWORD")),
        compose_project=_validated_project(
            settings.get("TAP_MILVUS_COMPOSE_PROJECT", "tap-milvus-local-experiment")
        ),
    )


def _required(settings: Mapping[str, str], name: str) -> str:
    value = settings.get(name)
    if not isinstance(value, str) or not value or len(value) > 2_048:
        raise ValueError(f"{name} is required for the real Milvus gate")
    return value


def _validated_project(project: object) -> str:
    if not isinstance(project, str) or _SAFE_PROJECT.fullmatch(project) is None:
        raise ValueError("safe compose project name is required")
    return project


def _load_snapshot(
    path: Path,
    manifest: DocFixtureManifest,
    cases: tuple[QueryCase, ...],
) -> VectorSnapshot:
    return load_vector_snapshot(
        path,
        chunk_hashes={chunk.chunk_id: chunk.chunk_content_hash for chunk in manifest.chunks},
        query_hashes={case.case_id: content_hash(case.query) for case in cases},
    )


def _document_anchor(chunk: DocFixtureChunk) -> DocumentAnchor:
    raw = json.loads(chunk.anchor_json)
    return DocumentAnchor(
        heading_path=tuple(raw.get("headingPath", ())),
        page=raw.get("page"),
        bbox=tuple(raw.get("bbox", ())),
        start_offset=raw.get("startOffset"),
        end_offset=raw.get("endOffset"),
    )


def _search_source_ids(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], list):
        raise AssertionError("single-channel Milvus result shape is malformed")
    sources: list[str] = []
    for hit in raw[0]:
        if not isinstance(hit, Mapping):
            raise AssertionError("single-channel Milvus hit shape is malformed")
        entity = hit.get("entity")
        if not isinstance(entity, Mapping) or not isinstance(entity.get("source_id"), str):
            raise AssertionError("single-channel Milvus source provenance is missing")
        sources.append(cast(str, entity["source_id"]))
    return tuple(sources)


def _rebuild_row(raw: Mapping[str, object] | object) -> dict[str, object]:
    if not isinstance(raw, Mapping) or set(raw) != set(_REBUILD_FIELDS):
        raise AssertionError("rebuild reconciliation row is widened or incomplete")
    row = dict(raw)
    for name in ("allowed_group_ids", "derived_from_chunk_ids"):
        value = row[name]
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise AssertionError("rebuild reconciliation array is malformed")
        row[name] = list(value)
    return row


def _expected_rebuild_row(raw: Mapping[str, object] | object) -> dict[str, object]:
    expected_fields = set(_REBUILD_FIELDS) | _REBUILD_SOURCE_ONLY_FIELDS
    if not isinstance(raw, Mapping) or set(raw) != expected_fields:
        raise AssertionError("expected rebuild source row is widened or incomplete")
    return _rebuild_row({name: raw[name] for name in _REBUILD_FIELDS})


def _rows_digest(rows: Sequence[Mapping[str, object]]) -> str:
    by_chunk = sorted((dict(row) for row in rows), key=lambda row: cast(str, row["chunk_id"]))
    encoded = json.dumps(
        by_chunk,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


async def _run_command(command: tuple[str, ...]) -> None:
    def run() -> None:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            raise RuntimeError("scoped Docker Compose operation failed")

    await asyncio.to_thread(run)
