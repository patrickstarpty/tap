"""Strict settings, lifecycle ownership, and composition roots for Athena local runtime."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

from tap.contracts.http import (
    HealthComponent,
    HealthComponentName,
    HealthComponentState,
    HealthRemediationCode,
    ReadyHealth,
)
from tap.interfaces.http.dependencies import HttpServices, ReadinessHttpService
from tap.modules.knowledge.adapters.milvus.audit import (
    MilvusSearchAuditEvent,
    SearchAuditSink,
)
from tap.modules.knowledge.ports.documents import (
    DocumentEmbeddingPort,
)
from tap.modules.knowledge.ports.models import RedactionResult
from tap.modules.knowledge.ports.redaction import EgressRedactionPort
from tap.modules.knowledge.ports.search import (
    AnswerGenerationPort,
    ModelPort,
    QueryEmbeddingPort,
    SearchPort,
)
from tap.operations.milvus.contracts import validate_milvus_role_usernames

if TYPE_CHECKING:
    import httpx
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from tap.entrypoints.athena_ingestion_worker import WorkerRuntime
    from tap.modules.knowledge.adapters.blob_artifacts import AzureBlobArtifactStore
    from tap.modules.knowledge.adapters.milvus.config import (
        MilvusIndexTarget,
        MilvusSearchConfig,
    )
    from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
    from tap.modules.knowledge.adapters.milvus.transport import MilvusReader, PyMilvusReader
    from tap.modules.knowledge.adapters.milvus_documents import MilvusDocumentIndex
    from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
    from tap.modules.knowledge.adapters.mysql_projection import MysqlProjectionCoordinator
    from tap.modules.knowledge.application.ingestion import IngestionStageHook
    from tap.modules.knowledge.ports.documents import JobStage
    from tap.operations.milvus.client import AthenaDocumentMilvusClients

_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{2,62}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MODEL_ROUTE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}(?:/[A-Za-z0-9][A-Za-z0-9._:-]{0,127})*\Z"
)
_FIXED_COLLECTION = "kb_doc_v1_athena_demo"
_FIXED_ALIAS = "kb_doc_athena_demo_active"
_FIXED_CORPUS = "athena-demo-v1"
_FIXED_CHAT_ALIAS = "athena-chat"
_FIXED_EMBEDDING_ALIAS = "athena-embedding"
_FIXED_RETRIEVAL_PROFILE = "quick-hybrid-v1"
_FIXED_SCHEMA_VERSION = "doc-schema-v1"
_FIXED_TENANT = "local"
_FIXED_PROJECT = "athena-demo"
_FIXED_GROUP = "athena-local"
_FIXED_ENVIRONMENT = "global"


@dataclass(frozen=True, slots=True)
class AthenaSettings:
    """One validated authority for every API, worker, and provider setting."""

    api_host: str
    api_port: int
    web_host: str
    web_port: int
    model_backend: str
    embedding_dimension: int
    poll_seconds: float
    job_batch_size: int
    collection: str
    alias: str
    corpus_version: str
    chat_alias: str
    embedding_alias: str
    retrieval_profile: str
    schema_version: str
    index_version: str
    pipeline_version: str
    worker_id: str
    compose_project: str
    ready_timeout_seconds: float
    model_timeout_seconds: float
    blob_timeout_seconds: float
    milvus_timeout_seconds: float
    database_url: str = field(repr=False)
    alembic_database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    redis_stream: str
    blob_connection_string: str = field(repr=False)
    litellm_base_url: str
    litellm_api_key: str = field(repr=False)
    litellm_model: str = field(repr=False)
    litellm_embedding_model: str = field(repr=False)
    allowed_answer_model_labels: frozenset[str] = field(repr=False)
    allowed_embedding_model_labels: frozenset[str] = field(repr=False)
    milvus_uri: str
    milvus_database: str
    milvus_reader_username: str
    milvus_reader_password: str = field(repr=False)
    milvus_writer_username: str
    milvus_writer_password: str = field(repr=False)
    milvus_provisioner_username: str
    milvus_provisioner_password: str = field(repr=False)
    e2e_mode: bool
    tenant_id: str = _FIXED_TENANT
    project_id: str = _FIXED_PROJECT
    group_id: str = _FIXED_GROUP
    environment: str = _FIXED_ENVIRONMENT

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> AthenaSettings:
        if not isinstance(values, Mapping):
            raise TypeError("Athena settings require a string mapping")
        backend = _fixed_choice(
            values,
            "ATHENA_MODEL_BACKEND",
            default="litellm",
            choices=frozenset({"litellm", "fake"}),
        )
        demo_mode = _value(values, "TAP_DEMO_MODE", "")
        if demo_mode not in {"", "e2e"} or ((demo_mode == "e2e") != (backend == "fake")):
            raise ValueError(
                "ATHENA_MODEL_BACKEND=fake requires exact TAP_DEMO_MODE=e2e and vice versa"
            )

        api_host = _loopback_host(values, "ATHENA_API_HOST", "127.0.0.1")
        web_host = _loopback_host(values, "ATHENA_WEB_HOST", "127.0.0.1")
        database_url = _loopback_url(
            values,
            "TAP_DATABASE_URL",
            "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
            schemes=frozenset({"mysql+asyncmy"}),
            expected_path="/tap",
            expected_query="charset=utf8mb4",
        )
        alembic_database_url = _loopback_url(
            values,
            "TAP_ALEMBIC_DATABASE_URL",
            "mysql+pymysql://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
            schemes=frozenset({"mysql+pymysql"}),
            expected_path="/tap",
            expected_query="charset=utf8mb4",
        )
        redis_url = _loopback_url(
            values,
            "TAP_REDIS_URL",
            "redis://127.0.0.1:6379/0",
            schemes=frozenset({"redis"}),
            expected_path="/0",
            expected_query="",
        )
        blob_connection_string = _blob_connection_string(values)
        litellm_base_url = _loopback_url(
            values,
            "LITELLM_BASE_URL",
            "http://127.0.0.1:4000",
            schemes=frozenset({"http"}),
            allow_userinfo=False,
            root_only=True,
        )
        milvus_uri = _loopback_url(
            values,
            "MILVUS_URI",
            "http://127.0.0.1:19530",
            schemes=frozenset({"http"}),
            allow_userinfo=False,
            root_only=True,
        )
        if backend == "litellm":
            litellm_model = _model_route(values, "LITELLM_MODEL")
            litellm_embedding_model = _model_route(values, "LITELLM_EMBEDDING_MODEL")
            allowed_answer_labels = _model_labels(_FIXED_CHAT_ALIAS, litellm_model)
            allowed_embedding_labels = _model_labels(
                _FIXED_EMBEDDING_ALIAS,
                litellm_embedding_model,
            )
            if allowed_answer_labels & allowed_embedding_labels:
                raise ValueError(
                    "LITELLM_EMBEDDING_MODEL must not overlap LITELLM_MODEL response labels"
                )
        else:
            litellm_model = _FIXED_CHAT_ALIAS
            litellm_embedding_model = _FIXED_EMBEDDING_ALIAS
            allowed_answer_labels = frozenset({_FIXED_CHAT_ALIAS})
            allowed_embedding_labels = frozenset({_FIXED_EMBEDDING_ALIAS})

        collection = _fixed_value(values, "ATHENA_COLLECTION", _FIXED_COLLECTION)
        alias = _fixed_value(values, "ATHENA_ALIAS", _FIXED_ALIAS)
        corpus = _fixed_value(values, "ATHENA_CORPUS_VERSION", _FIXED_CORPUS)
        chat_alias = _fixed_value(values, "ATHENA_CHAT_ALIAS", _FIXED_CHAT_ALIAS)
        embedding_alias = _fixed_value(values, "ATHENA_EMBEDDING_ALIAS", _FIXED_EMBEDDING_ALIAS)
        retrieval_profile = _fixed_value(
            values,
            "ATHENA_RETRIEVAL_PROFILE",
            _FIXED_RETRIEVAL_PROFILE,
        )
        dimension = _integer(
            values,
            "ATHENA_EMBEDDING_DIMENSION",
            1536,
            minimum=1,
            maximum=4096,
        )
        if dimension != 1536:
            raise ValueError("ATHENA_EMBEDDING_DIMENSION must equal 1536")

        milvus_reader_username = _identity(
            values,
            "MILVUS_READER_USERNAME",
            "tap_reader",
        )
        milvus_writer_username = _identity(
            values,
            "MILVUS_WRITER_USERNAME",
            "tap_writer",
        )
        milvus_provisioner_username = _identity(
            values,
            "MILVUS_PROVISIONER_USERNAME",
            "tap_provisioner",
        )
        validate_milvus_role_usernames(
            reader_username=milvus_reader_username,
            writer_username=milvus_writer_username,
            provisioner_username=milvus_provisioner_username,
        )

        return cls(
            api_host=api_host,
            api_port=_integer(values, "ATHENA_API_PORT", 8000, minimum=1, maximum=65535),
            web_host=web_host,
            web_port=_integer(values, "ATHENA_WEB_PORT", 5173, minimum=1, maximum=65535),
            model_backend=backend,
            embedding_dimension=dimension,
            poll_seconds=_duration(values, "ATHENA_POLL_SECONDS", 1.0, maximum=60),
            job_batch_size=_integer(
                values,
                "ATHENA_JOB_BATCH_SIZE",
                10,
                minimum=1,
                maximum=50,
            ),
            collection=collection,
            alias=alias,
            corpus_version=corpus,
            chat_alias=chat_alias,
            embedding_alias=embedding_alias,
            retrieval_profile=retrieval_profile,
            schema_version=_FIXED_SCHEMA_VERSION,
            index_version=_fixed_value(values, "ATHENA_INDEX_VERSION", "athena-index-v1"),
            pipeline_version=_fixed_value(values, "ATHENA_PIPELINE_VERSION", "athena-ingestion-v1"),
            worker_id=_identity(values, "ATHENA_WORKER_ID", "athena-local-worker"),
            compose_project=_project(values),
            ready_timeout_seconds=_duration(
                values, "ATHENA_READY_TIMEOUT_SECONDS", 2.0, maximum=30
            ),
            model_timeout_seconds=_duration(
                values, "ATHENA_MODEL_TIMEOUT_SECONDS", 15.0, maximum=60
            ),
            blob_timeout_seconds=_duration(values, "ATHENA_BLOB_TIMEOUT_SECONDS", 15.0, maximum=60),
            milvus_timeout_seconds=_duration(
                values, "ATHENA_MILVUS_TIMEOUT_SECONDS", 10.0, maximum=60
            ),
            database_url=database_url,
            alembic_database_url=alembic_database_url,
            redis_url=redis_url,
            redis_stream=_identity(values, "TAP_REDIS_COMMAND_STREAM", "tap:commands"),
            blob_connection_string=blob_connection_string,
            litellm_base_url=litellm_base_url,
            litellm_api_key=_secret(values, "LITELLM_MASTER_KEY", "tap-local-master-key"),
            litellm_model=litellm_model,
            litellm_embedding_model=litellm_embedding_model,
            allowed_answer_model_labels=allowed_answer_labels,
            allowed_embedding_model_labels=allowed_embedding_labels,
            milvus_uri=milvus_uri,
            milvus_database=_identity(values, "MILVUS_DATABASE", "default"),
            milvus_reader_username=milvus_reader_username,
            milvus_reader_password=_secret(values, "MILVUS_READER_PASSWORD", "tap-local-Reader1!"),
            milvus_writer_username=milvus_writer_username,
            milvus_writer_password=_secret(values, "MILVUS_WRITER_PASSWORD", "tap-local-Writer1!"),
            milvus_provisioner_username=milvus_provisioner_username,
            milvus_provisioner_password=_secret(
                values,
                "MILVUS_PROVISIONER_PASSWORD",
                "tap-local-Provisioner1!",
            ),
            e2e_mode=demo_mode == "e2e",
        )


class _AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


CloseCallback = Callable[[], Awaitable[object] | object]


class AthenaModel(ModelPort, DocumentEmbeddingPort, Protocol):
    """One adapter implements query, answer, and document embedding needs."""


class AthenaFailureController(Protocol):
    """Closed E2E-only controller shared by the API arm route and worker hook."""

    async def arm(self, stage: str) -> str: ...

    async def before_stage(self, stage: JobStage) -> None: ...


class OwnedResources:
    """Idempotently settle one owned runtime in strict reverse construction order."""

    def __init__(self, *, close_timeout_seconds: float = 5.0) -> None:
        if (
            isinstance(close_timeout_seconds, bool)
            or not isinstance(close_timeout_seconds, (int, float))
            or not math.isfinite(close_timeout_seconds)
            or not 0 < close_timeout_seconds <= 30
        ):
            raise ValueError("runtime close timeout must be finite and bounded")
        self._callbacks: list[CloseCallback] = []
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_timeout_seconds = float(close_timeout_seconds)

    def callback(self, callback: CloseCallback) -> None:
        if self._closed:
            raise RuntimeError("runtime resource ownership is already closed")
        if not callable(callback):
            raise TypeError("runtime cleanup callback must be callable")
        if not inspect.iscoroutinefunction(callback):
            raise TypeError("runtime cleanup callback must be explicitly asynchronous")
        self._callbacks.append(callback)

    def push(self, resource: object) -> object:
        for name in ("aclose", "close", "dispose"):
            callback = getattr(resource, name, None)
            if callable(callback):
                self.callback(cast(CloseCallback, callback))
                return resource
        raise TypeError("owned runtime resource has no async close operation")

    async def aclose(self, primary: BaseException | None = None) -> None:
        async with self._lock:
            if self._closed:
                if primary is not None:
                    raise primary
                return
            self._closed = True
            callbacks = tuple(reversed(self._callbacks))
            self._callbacks.clear()

        errors: list[BaseException] = []
        if primary is not None:
            errors.append(primary)
        for callback in callbacks:
            try:
                result = callback()
                if inspect.isawaitable(result):
                    task = asyncio.ensure_future(cast(Awaitable[object], result))
                    try:
                        done, _ = await asyncio.wait(
                            {task},
                            timeout=self._close_timeout_seconds,
                        )
                    except BaseException:
                        task.cancel()
                        task.add_done_callback(_consume_background_task)
                        raise
                    if not done:
                        # Some third-party SDKs swallow cancellation.  Process
                        # shutdown must still advance to the remaining owners,
                        # so cancellation is requested but deliberately not
                        # awaited past this hard containment deadline.
                        task.cancel()
                        task.add_done_callback(_consume_background_task)
                        raise TimeoutError("Athena runtime resource close timed out")
                    task.result()
            except BaseException as error:
                errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            if all(isinstance(error, Exception) for error in errors):
                raise ExceptionGroup(
                    "Athena runtime lifecycle failed",
                    cast(list[Exception], errors),
                )
            raise BaseExceptionGroup("Athena runtime lifecycle failed", errors)


def _consume_background_task(task: asyncio.Future[object]) -> None:
    """Retrieve a detached close result without extending the shutdown deadline."""

    try:
        task.exception()
    except BaseException:
        pass


ReadinessCheck = Callable[[], Awaitable[bool]]


class ReadinessService:
    """Run every fixed dependency probe independently under one closed timeout."""

    _ORDER = (
        HealthComponentName.MYSQL,
        HealthComponentName.REDIS,
        HealthComponentName.BLOB,
        HealthComponentName.MILVUS,
        HealthComponentName.MODELS,
    )
    _REMEDIATION = {
        HealthComponentName.MYSQL: HealthRemediationCode.START_MYSQL,
        HealthComponentName.REDIS: HealthRemediationCode.START_REDIS,
        HealthComponentName.BLOB: HealthRemediationCode.START_BLOB,
        HealthComponentName.MILVUS: HealthRemediationCode.START_MILVUS,
        HealthComponentName.MODELS: HealthRemediationCode.CONFIGURE_MODELS,
    }

    def __init__(
        self,
        *,
        mysql: ReadinessCheck,
        redis: ReadinessCheck,
        blob: ReadinessCheck,
        milvus: ReadinessCheck,
        models: ReadinessCheck,
        timeout_seconds: float,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("readiness timeout must be finite and bounded")
        checks = (mysql, redis, blob, milvus, models)
        if not all(callable(check) for check in checks):
            raise TypeError("readiness checks must be callable")
        self._checks = checks
        self._timeout_seconds = float(timeout_seconds)

    async def check(self) -> ReadyHealth:
        tasks = tuple(
            asyncio.create_task(self._bounded(check), name=f"athena-ready-{name.value}")
            for name, check in zip(self._ORDER, self._checks, strict=True)
        )
        results = await asyncio.gather(*tasks)
        components = [
            HealthComponent(
                name=name,
                state=HealthComponentState.OK if healthy else HealthComponentState.FAILED,
                remediation_code=None if healthy else self._REMEDIATION[name],
            )
            for name, healthy in zip(self._ORDER, results, strict=True)
        ]
        return ReadyHealth(
            status="ready" if all(results) else "unready",
            components=components,
        )

    async def _bounded(self, check: ReadinessCheck) -> bool:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await check() is True
        except asyncio.CancelledError:
            raise
        except Exception:
            return False


@dataclass(slots=True)
class AthenaApiRuntime:
    """One API process graph with a single outer ownership boundary."""

    http_services: HttpServices
    _resources: OwnedResources
    failure_controller: AthenaFailureController | None = None

    async def aclose(self) -> None:
        await self._resources.aclose()


async def create_api_runtime(settings: AthenaSettings) -> AthenaApiRuntime:
    """Construct the API graph only after one complete settings snapshot validates."""

    if not isinstance(settings, AthenaSettings):
        raise TypeError("Athena API runtime requires validated settings")
    resources = OwnedResources()
    try:
        engine, repository = await _create_database(settings)
        resources.push(engine)
        artifacts = _create_blob(settings)
        resources.push(artifacts)
        redis = _create_redis(settings)
        resources.push(redis)
        failure_controller = _create_stage_controller(settings, redis)
        model = _create_model(settings)
        _push_if_owned(resources, model)
        search, reader, target = await _create_search(settings)
        resources.push(search)
        models_probe_client = _create_models_probe_client(settings)
        _push_if_owned(resources, models_probe_client)
        readiness = _create_readiness(
            settings=settings,
            engine=engine,
            redis=redis,
            artifacts=artifacts,
            model=model,
            milvus_reader=reader,
            milvus_target=target,
            models_probe_client=models_probe_client,
        )
        services = _assemble_http_services(
            repository=repository,
            artifacts=artifacts,
            search=search,
            embeddings=model,
            answers=model,
            readiness=readiness,
            redactor=LocalEgressRedactor(),
        )
        return AthenaApiRuntime(
            http_services=services,
            _resources=resources,
            failure_controller=failure_controller,
        )
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("resource settlement unexpectedly returned")


async def create_worker_runtime(settings: AthenaSettings) -> WorkerRuntime:
    """Construct one ingestion worker graph behind a single outer owner."""

    if not isinstance(settings, AthenaSettings):
        raise TypeError("Athena worker runtime requires validated settings")
    resources = OwnedResources()
    try:
        engine, repository = await _create_database(settings)
        resources.push(engine)
        artifacts = _create_blob(settings)
        resources.push(artifacts)
        redis = _create_redis(settings)
        resources.push(redis)
        model = _create_model(settings)
        _push_if_owned(resources, model)
        index = await _create_document_index(settings, engine)
        # MilvusDocumentIndex transitively owns all three role clients and the
        # projection coordinator.  Register only this complete aggregate.
        resources.push(index)
        stage_hook = _create_stage_controller(settings, redis)
        return _assemble_worker_runtime(
            settings=settings,
            repository=repository,
            artifacts=artifacts,
            model=model,
            index=index,
            redis=redis,
            resources=resources,
            stage_hook=stage_hook,
        )
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("worker resource settlement unexpectedly returned")


def _create_stage_controller(
    settings: AthenaSettings,
    redis: Redis,
) -> AthenaFailureController | None:
    if not settings.e2e_mode:
        return None
    from tap.testing.failure_injection import RedisStageFailureController

    return cast(
        AthenaFailureController,
        RedisStageFailureController(redis=redis, project=settings.compose_project),
    )


def _push_if_owned(resources: OwnedResources, resource: object | None) -> None:
    if resource is not None and any(
        callable(getattr(resource, name, None)) for name in ("aclose", "close", "dispose")
    ):
        resources.push(resource)


class LocalEgressRedactor:
    """Fixed local egress decision that never logs or stores query content."""

    async def redact(self, text: str) -> RedactionResult:
        return RedactionResult(
            sanitized_text=text,
            redaction_version="athena-local-egress-v1",
        )


class LocalSearchAuditSink(SearchAuditSink):
    """Accept the fixed secret-free event without logging query or evidence content."""

    async def emit(self, event: MilvusSearchAuditEvent) -> None:
        if not isinstance(event, MilvusSearchAuditEvent):
            raise TypeError("Athena search audit requires the fixed Milvus event")


async def _create_database(
    settings: AthenaSettings,
) -> tuple[AsyncEngine, MysqlDocumentRepository]:
    engine, sessions = _open_database(settings)
    try:
        repository = _build_document_repository(sessions)
    except BaseException as error:
        local = OwnedResources()
        local.push(engine)
        await local.aclose(error)
        raise AssertionError("database helper settlement unexpectedly returned")
    return engine, repository


def _open_database(
    settings: AthenaSettings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    from tap.platform.db.session import create_engine_and_session_factory

    return create_engine_and_session_factory(settings.database_url)


def _build_document_repository(
    sessions: async_sessionmaker[AsyncSession],
) -> MysqlDocumentRepository:
    from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository

    return MysqlDocumentRepository(sessions)


def _create_blob(settings: AthenaSettings) -> AzureBlobArtifactStore:
    from pydantic import SecretStr

    from tap.modules.knowledge.adapters.blob_artifacts import (
        AzureBlobArtifactConfig,
        AzureBlobArtifactStore,
    )

    return AzureBlobArtifactStore(
        AzureBlobArtifactConfig(
            connection_string=SecretStr(settings.blob_connection_string),
            operation_timeout_seconds=settings.blob_timeout_seconds,
        )
    )


def _create_redis(settings: AthenaSettings) -> Redis:
    from redis.asyncio import Redis

    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=20,
        socket_connect_timeout=min(5.0, settings.ready_timeout_seconds),
        socket_timeout=min(5.0, settings.ready_timeout_seconds),
        socket_keepalive=True,
        health_check_interval=30,
    )


def _create_model(settings: AthenaSettings) -> AthenaModel:
    if settings.e2e_mode:
        from tap.testing.deterministic_model import DeterministicAthenaModel

        return cast(AthenaModel, DeterministicAthenaModel())

    from tap.modules.knowledge.adapters.litellm import LiteLLMAdapter, LiteLLMConfig

    return cast(
        AthenaModel,
        LiteLLMAdapter(
            LiteLLMConfig(
                base_url=settings.litellm_base_url,
                api_key=settings.litellm_api_key,
                embedding_model_id=settings.embedding_alias,
                answer_model_id=settings.chat_alias,
                answer_profile_id=settings.retrieval_profile,
                embedding_dimension=settings.embedding_dimension,
                allowed_embedding_model_labels=settings.allowed_embedding_model_labels,
                allowed_answer_model_labels=settings.allowed_answer_model_labels,
                allowed_retrieval_profile_ids=frozenset({settings.retrieval_profile}),
                deadline_seconds=settings.model_timeout_seconds,
                read_timeout_seconds=min(10.0, settings.model_timeout_seconds),
            )
        ),
    )


async def _create_document_index(
    settings: AthenaSettings,
    engine: AsyncEngine,
) -> MilvusDocumentIndex:
    """Build the role-isolated index and settle every untransferred child."""

    parts = await _open_document_clients(settings)
    coordinator: MysqlProjectionCoordinator | None = None
    try:
        coordinator = _create_projection_coordinator(settings, engine)
        return _build_document_index(settings, coordinator, parts)
    except BaseException as error:
        local = OwnedResources()
        if coordinator is not None:
            local.push(coordinator)
        # Construction order is provisioner, writer, reader.  The outer owner
        # reverses these callbacks, matching MilvusDocumentIndex.close().
        local.push(parts.provisioner)
        local.push(parts.writer)
        local.push(parts.reader)
        await local.aclose(error)
        raise AssertionError("document index helper settlement unexpectedly returned")


async def _open_document_clients(
    settings: AthenaSettings,
) -> AthenaDocumentMilvusClients:
    from pydantic import SecretStr

    from tap.operations.milvus.client import create_athena_document_clients

    return await create_athena_document_clients(
        uri=settings.milvus_uri,
        database=settings.milvus_database,
        provisioner_username=settings.milvus_provisioner_username,
        provisioner_password=SecretStr(settings.milvus_provisioner_password),
        writer_username=settings.milvus_writer_username,
        writer_password=SecretStr(settings.milvus_writer_password),
        reader_username=settings.milvus_reader_username,
        reader_password=SecretStr(settings.milvus_reader_password),
    )


def _create_projection_coordinator(
    settings: AthenaSettings,
    engine: AsyncEngine,
) -> MysqlProjectionCoordinator:
    from tap.modules.knowledge.adapters.mysql_projection import MysqlProjectionCoordinator

    return MysqlProjectionCoordinator(
        engine,
        authority_namespace=settings.compose_project,
    )


def _build_document_index(
    settings: AthenaSettings,
    coordinator: MysqlProjectionCoordinator,
    parts: AthenaDocumentMilvusClients,
) -> MilvusDocumentIndex:
    from tap.modules.knowledge.adapters.milvus_documents import (
        AthenaMilvusConfig,
        MilvusDocumentIndex,
    )

    config = AthenaMilvusConfig(
        physical_collection=settings.collection,
        alias=settings.alias,
        schema_version=settings.schema_version,
        corpus_version=settings.corpus_version,
        embedding_model=settings.embedding_alias,
        vector_dimension=settings.embedding_dimension,
        tenant_id=settings.tenant_id,
        project_id=settings.project_id,
        group_id=settings.group_id,
        environment=settings.environment,
    )
    return MilvusDocumentIndex(
        config=config,
        provisioner=parts.provisioner,
        writer=parts.writer,
        reader=parts.reader,
        coordinator=coordinator,
    )


async def _create_search(
    settings: AthenaSettings,
) -> tuple[MilvusSearchAdapter, PyMilvusReader, MilvusIndexTarget]:
    from pydantic import SecretStr

    from tap.modules.knowledge.adapters.milvus.config import (
        MilvusIndexTarget,
        MilvusSearchConfig,
    )
    from tap.modules.knowledge.domain.models import SourceFamily
    from tap.operations.milvus.doc_schema import doc_schema_sha256

    target = MilvusIndexTarget(
        family=SourceFamily.DOC,
        alias=settings.alias,
        physical_name_prefix=settings.collection,
        schema_version=settings.schema_version,
        schema_sha256=doc_schema_sha256(),
        corpus_version=settings.corpus_version,
        embedding_model_version=settings.embedding_alias,
        vector_dimension=settings.embedding_dimension,
        exact_generation_names=True,
    )
    config = MilvusSearchConfig(
        uri=settings.milvus_uri,
        database=settings.milvus_database,
        username=settings.milvus_reader_username,
        password=SecretStr(settings.milvus_reader_password),
        targets={SourceFamily.DOC: target},
        timeout_seconds=settings.milvus_timeout_seconds,
    )
    reader = _open_search_reader(config)
    try:
        search = _build_search_adapter(config, reader)
    except BaseException as error:
        local = OwnedResources()
        local.push(reader)
        await local.aclose(error)
        raise AssertionError("search helper settlement unexpectedly returned")
    return search, reader, target


def _open_search_reader(config: MilvusSearchConfig) -> PyMilvusReader:
    from tap.modules.knowledge.adapters.milvus.transport import PyMilvusReader

    return PyMilvusReader(config)


def _build_search_adapter(
    config: MilvusSearchConfig,
    reader: MilvusReader,
) -> MilvusSearchAdapter:
    from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter

    return MilvusSearchAdapter(
        config,
        reader,
        LocalSearchAuditSink(),
    )


def _create_models_probe_client(settings: AthenaSettings) -> httpx.AsyncClient | None:
    if settings.e2e_mode:
        return None
    import httpx

    return httpx.AsyncClient(
        base_url=settings.litellm_base_url.rstrip("/") + "/",
        headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
        timeout=httpx.Timeout(settings.ready_timeout_seconds),
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        transport=httpx.AsyncHTTPTransport(retries=0),
    )


def _is_private_blob_container(properties: object) -> bool:
    return (
        isinstance(properties, Mapping)
        and "public_access" in properties
        and properties["public_access"] is None
    )


def _create_readiness(
    *,
    settings: AthenaSettings,
    engine: AsyncEngine,
    redis: Redis,
    artifacts: AzureBlobArtifactStore,
    model: AthenaModel,
    milvus_reader: MilvusReader,
    milvus_target: MilvusIndexTarget,
    models_probe_client: httpx.AsyncClient | None,
) -> ReadinessService:
    from sqlalchemy import text

    from tap.modules.knowledge.adapters.blob_artifacts import (
        ARTIFACTS_CONTAINER,
        ORIGINALS_CONTAINER,
    )
    from tap.modules.knowledge.adapters.milvus.targets import bind_target
    from tap.modules.knowledge.adapters.milvus.transport import MilvusQueryRequest

    expected_head = _discover_alembic_head()

    async def mysql_ready() -> bool:
        async with engine.connect() as connection:
            ping = (await connection.execute(text("SELECT 1"))).scalar_one()
            version = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
        return ping == 1 and version == expected_head

    async def redis_ready() -> bool:
        return await redis.ping() is True

    async def blob_ready() -> bool:
        for container in (ORIGINALS_CONTAINER, ARTIFACTS_CONTAINER):
            properties = await artifacts.container_properties(container)
            if not _is_private_blob_container(properties):
                return False
        return True

    async def milvus_ready() -> bool:
        bound = await bind_target(milvus_reader, milvus_target)
        rows = await milvus_reader.query(
            MilvusQueryRequest(
                collection_name=bound.physical_collection,
                filter_expression=('chunk_id == "__athena_readiness_reserved_never_persisted__"'),
                output_fields=("chunk_id",),
                limit=1,
            )
        )
        return rows == ()

    async def models_ready() -> bool:
        if settings.e2e_mode:
            embedding = await model.embed("Athena deterministic readiness")
            vector = embedding.vector
            return (
                embedding.model_id == settings.embedding_alias
                and isinstance(vector, tuple)
                and len(vector) == settings.embedding_dimension
                and all(type(value) is float and math.isfinite(value) for value in vector)
                and math.isclose(
                    math.sqrt(sum(value * value for value in vector)),
                    1.0,
                    rel_tol=1e-12,
                )
            )
        if models_probe_client is None:
            return False
        labels = await _read_models_labels(models_probe_client)
        return labels is not None and {settings.chat_alias, settings.embedding_alias} <= labels

    return ReadinessService(
        mysql=mysql_ready,
        redis=redis_ready,
        blob=blob_ready,
        milvus=milvus_ready,
        models=models_ready,
        timeout_seconds=settings.ready_timeout_seconds,
    )


async def _read_models_labels(client: httpx.AsyncClient) -> frozenset[str] | None:
    """Read one bounded standard OpenAI models page without buffering overflow."""

    async with client.stream("GET", "v1/models") as response:
        if response.status_code != 200:
            return None
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if not isinstance(chunk, bytes) or len(body) + len(chunk) > 1_048_576:
                return None
            body.extend(chunk)
    try:
        payload = json.loads(bytes(body))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"data", "object"}
        or payload.get("object") != "list"
    ):
        return None
    data = payload["data"]
    if not isinstance(data, list) or not 1 <= len(data) <= 100:
        return None
    labels: set[str] = set()
    for item in data:
        if not isinstance(item, Mapping) or set(item) != {
            "id",
            "object",
            "created",
            "owned_by",
        }:
            return None
        label = item.get("id")
        owner = item.get("owned_by")
        created = item.get("created")
        if (
            not isinstance(label, str)
            or not label
            or len(label) > 256
            or item.get("object") != "model"
            or type(created) is not int
            or not 0 <= created <= 2**63 - 1
            or not isinstance(owner, str)
            or not owner
            or len(owner) > 256
            or any(ord(character) < 0x20 for character in owner)
        ):
            return None
        labels.add(label)
    return frozenset(labels)


def _discover_alembic_head() -> str:
    """Resolve the checked-in migration head without opening a database connection."""

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    if len(heads) != 1 or not heads[0]:
        raise RuntimeError("Athena requires one current Alembic head")
    return heads[0]


def _assemble_http_services(
    *,
    repository: MysqlDocumentRepository,
    artifacts: AzureBlobArtifactStore,
    search: SearchPort,
    embeddings: QueryEmbeddingPort,
    answers: AnswerGenerationPort,
    readiness: ReadinessHttpService,
    redactor: EgressRedactionPort,
) -> HttpServices:
    """Assemble the one approved Athena application graph from existing services."""

    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.knowledge.api import KnowledgeAPI
    from tap.modules.knowledge.application.answers import AnswerService
    from tap.modules.knowledge.application.citations import CitationResolver
    from tap.modules.knowledge.application.demo_policy import DemoCurrentPolicyVerifier
    from tap.modules.knowledge.application.documents import DocumentService
    from tap.modules.knowledge.ports.answers import AnswerSnapshotRepository
    from tap.modules.knowledge.ports.citations import (
        CitationArtifactStore,
        CitationRepository,
    )
    from tap.modules.knowledge.ports.documents import ArtifactStore, DocumentRepository

    document_repository = cast(DocumentRepository, repository)
    artifact_store = cast(ArtifactStore, artifacts)

    documents = DocumentService(repository=document_repository, artifacts=artifact_store)
    knowledge = KnowledgeAPI(
        search=search,
        embeddings=embeddings,
        answers=answers,
        policy_verifier=DemoCurrentPolicyVerifier(repository),
        redactor=redactor,
    )
    answer_service = AnswerService(
        repository=cast(AnswerSnapshotRepository, repository),
        knowledge=knowledge,
    )
    search_service = answer_service
    citations = CitationResolver(
        repository=cast(CitationRepository, repository),
        artifacts=cast(CitationArtifactStore, artifacts),
    )
    return HttpServices(
        knowledge=KnowledgeHttpService(
            documents=documents,
            answers=answer_service,
            citations=citations,
            searches=search_service,
        ),
        readiness=readiness,
    )


def _assemble_worker_runtime(
    *,
    settings: AthenaSettings,
    repository: MysqlDocumentRepository,
    artifacts: AzureBlobArtifactStore,
    model: AthenaModel,
    index: MilvusDocumentIndex,
    redis: Redis,
    resources: OwnedResources,
    stage_hook: IngestionStageHook | None,
) -> WorkerRuntime:
    """Assemble only the existing ingestion service and durable wake-up path."""

    from tap.entrypoints.athena_ingestion_worker import WorkerRuntime
    from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
    from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
    from tap.modules.knowledge.application.ingestion import IngestionWorker
    from tap.modules.knowledge.ports.documents import (
        ArtifactStore,
        DocumentEmbeddingPort,
        DocumentIndexPort,
        DocumentRepository,
    )
    from tap.platform.messaging.redis_dispatch import AsyncRedisStream
    from tap.platform.messaging.redis_wakeup import RedisWakeupConsumer

    worker = IngestionWorker(
        repository=cast(DocumentRepository, repository),
        artifacts=cast(ArtifactStore, artifacts),
        parser=ParserRegistry(),
        chunker=StructuralChunker(),
        embeddings=cast(DocumentEmbeddingPort, model),
        index=cast(DocumentIndexPort, index),
        worker_id=settings.worker_id,
        embedding_model_alias=settings.embedding_alias,
        embedding_dimension=settings.embedding_dimension,
        index_version=settings.index_version,
        stage_hook=stage_hook,
    )
    wakeups = RedisWakeupConsumer(
        redis=cast(AsyncRedisStream, redis),
        stream_name=settings.redis_stream,
        group_name="athena-ingestion",
        consumer_name=settings.worker_id,
        aggregate_type="knowledge_document",
    )
    return WorkerRuntime(
        worker=worker,
        wakeups=wakeups,
        resources=(resources,),
    )


def _value(values: Mapping[str, str], name: str, default: str) -> str:
    value = values.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _fixed_value(values: Mapping[str, str], name: str, expected: str) -> str:
    value = _value(values, name, expected)
    if value != expected:
        raise ValueError(f"{name} must use the fixed Athena value")
    return value


def _fixed_choice(
    values: Mapping[str, str],
    name: str,
    *,
    default: str,
    choices: frozenset[str],
) -> str:
    value = _value(values, name, default)
    if value not in choices:
        raise ValueError(f"{name} is outside the closed set")
    return value


def _integer(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = _value(values, name, str(default))
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)", raw):
        raise ValueError(f"{name} must be a canonical integer")
    parsed = int(raw)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} is outside the closed bound")
    return parsed


def _duration(
    values: Mapping[str, str],
    name: str,
    default: float,
    *,
    maximum: float,
) -> float:
    raw = _value(values, name, str(default))
    try:
        parsed = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a finite duration") from None
    if not math.isfinite(parsed) or not 0 < parsed <= maximum:
        raise ValueError(f"{name} must be a finite bounded duration")
    return parsed


def _loopback_host(values: Mapping[str, str], name: str, default: str) -> str:
    host = _value(values, name, default)
    if host == "localhost":
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(f"{name} must resolve to loopback") from None
    if not address.is_loopback:
        raise ValueError(f"{name} must resolve to loopback")
    return host


def _loopback_url(
    values: Mapping[str, str],
    name: str,
    default: str,
    *,
    schemes: frozenset[str],
    allow_userinfo: bool = True,
    root_only: bool = False,
    expected_path: str | None = None,
    expected_query: str | None = None,
) -> str:
    value = _value(values, name, default)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError(f"{name} must be a valid loopback URL") from None
    if parsed.scheme not in schemes or hostname is None or port is None:
        raise ValueError(f"{name} must be a valid loopback URL")
    if not allow_userinfo and (parsed.username is not None or parsed.password is not None):
        raise ValueError(f"{name} must not contain user information")
    if root_only and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError(f"{name} must be an origin without path, query, or fragment")
    if expected_path is not None and parsed.path != expected_path:
        raise ValueError(f"{name} must use the fixed local path")
    if expected_query is not None and parsed.query != expected_query:
        raise ValueError(f"{name} must use the fixed local query")
    if (expected_path is not None or expected_query is not None) and parsed.fragment:
        raise ValueError(f"{name} must not contain a fragment")
    if hostname != "localhost":
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            raise ValueError(f"{name} must target loopback") from None
        if not address.is_loopback:
            raise ValueError(f"{name} must target loopback")
    if parsed.username is None and parsed.scheme.startswith("mysql"):
        raise ValueError(f"{name} must include a database user")
    return value


def _identity(values: Mapping[str, str], name: str, default: str) -> str:
    value = _value(values, name, default)
    if _IDENTITY.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded safe identifier")
    return value


def _model_route(values: Mapping[str, str], name: str) -> str:
    value = _value(values, name, "")
    if len(value) > 256 or _MODEL_ROUTE.fullmatch(value) is None:
        raise ValueError(f"{name} must be one bounded exact provider model route")
    return value


def _model_labels(alias: str, raw_route: str) -> frozenset[str]:
    return frozenset({alias, raw_route, raw_route.rsplit("/", 1)[-1]})


def _project(values: Mapping[str, str]) -> str:
    name = "TAP_ATHENA_COMPOSE_PROJECT"
    value = _value(values, name, "tap-athena-demo")
    if _PROJECT.fullmatch(value) is None:
        raise ValueError(f"{name} must be a safe Compose project")
    return value


def _secret(values: Mapping[str, str], name: str, default: str) -> str:
    value = _value(values, name, default)
    if not value or len(value) > 4096 or "\x00" in value:
        raise ValueError(f"{name} must be a nonblank bounded secret")
    return value


def _blob_connection_string(values: Mapping[str, str]) -> str:
    name = "AZURE_STORAGE_CONNECTION_STRING"
    value = _value(values, name, "UseDevelopmentStorage=true")
    if value == "UseDevelopmentStorage=true":
        return value
    pairs: dict[str, str] = {}
    try:
        for item in value.split(";"):
            if not item:
                continue
            key, raw = item.split("=", 1)
            if key in pairs or not key or not raw:
                raise ValueError
            pairs[key] = raw
    except ValueError:
        raise ValueError(f"{name} must be a valid Blob-only connection string") from None
    if (
        set(pairs)
        != {
            "DefaultEndpointsProtocol",
            "AccountName",
            "AccountKey",
            "BlobEndpoint",
        }
        or pairs.get("DefaultEndpointsProtocol") != "http"
        or pairs.get("AccountName") != ("devstoreaccount1")
    ):
        raise ValueError(f"{name} must contain only the fixed Blob connection fields")
    endpoint_mapping = {"_BLOB_ENDPOINT": pairs["BlobEndpoint"]}
    try:
        _loopback_url(
            endpoint_mapping,
            "_BLOB_ENDPOINT",
            "http://127.0.0.1:10000/devstoreaccount1",
            schemes=frozenset({"http"}),
            allow_userinfo=False,
            expected_path="/devstoreaccount1",
            expected_query="",
        )
    except ValueError:
        raise ValueError(f"{name} must use the exact loopback Blob endpoint") from None
    if len(value) > 8192 or "\x00" in value:
        raise ValueError(f"{name} must be a bounded connection string")
    return value
