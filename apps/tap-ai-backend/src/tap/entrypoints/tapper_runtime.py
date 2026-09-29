"""Strict settings, lifecycle ownership, and composition roots for Tapper local runtime."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
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
from tap.modules.access.adapters.validation import VALIDATION_SCOPE, ValidationScopeProvider
from tap.modules.access.application.ports import AuthorizationPolicy, ScopeProvider
from tap.modules.access.application.scope import RequestFacts
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.adapters.litellm import (
    LiteLLMModelGateway,
    LiteLLMModelGatewayConfig,
)
from tap.modules.ai.adapters.litellm_catalog import LiteLLMCatalog, ModelRoles
from tap.modules.ai.application.catalog import ModelCatalog
from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
from tap.modules.knowledge.adapters.milvus.audit import (
    SearchAuditSink,
)
from tap.modules.knowledge.adapters.mysql_audit import MysqlSearchAuditSink
from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
from tap.modules.knowledge.ports.documents import (
    DocumentEmbeddingPort,
)
from tap.modules.knowledge.ports.redaction import EgressRedactionPort
from tap.modules.knowledge.ports.search import (
    QueryEmbeddingPort,
    SearchPort,
)
from tap.operations.milvus.contracts import validate_milvus_role_usernames

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import (
        AsyncConnection,
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
    )

    from tap.entrypoints.tapper_ingestion_worker import WorkerRuntime
    from tap.modules.governance.ports.audit import ProjectAuditPort
    from tap.modules.knowledge.adapters.milvus.config import (
        MilvusIndexTarget,
        MilvusSearchConfig,
    )
    from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
    from tap.modules.knowledge.adapters.milvus.transport import MilvusReader, PyMilvusReader
    from tap.modules.knowledge.adapters.milvus_documents import MilvusDocumentIndex
    from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
    from tap.modules.knowledge.adapters.mysql_managed_chunks import MysqlManagedChunks
    from tap.modules.knowledge.adapters.mysql_projection import MysqlProjectionCoordinator
    from tap.modules.knowledge.adapters.object_artifacts import KnowledgeArtifactStore
    from tap.modules.knowledge.application.ingestion import IngestionStageHook
    from tap.modules.knowledge.ports.answers import AnswerSnapshotRepository
    from tap.modules.knowledge.ports.documents import JobStage
    from tap.operations.milvus.client import TapperDocumentMilvusClients

_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{2,62}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MODEL_NAME = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*\Z")
_FIXED_COLLECTION = "kb_doc_v1_tapper_demo"
_FIXED_ALIAS = "kb_doc_tapper_demo_active"
_FIXED_CORPUS = "tapper-demo-v1"
_FIXED_RETRIEVAL_PROFILE = "quick-hybrid-v1"
_FIXED_SCHEMA_VERSION = "doc-schema-v1"
_FIXED_TENANT = "local"
_FIXED_PROJECT = "tapper-demo"
_FIXED_GROUP = "tapper-local"
_FIXED_ENVIRONMENT = "global"


@dataclass(frozen=True, slots=True)
class TapperSettings:
    """One validated authority for every API, worker, and provider setting."""

    api_host: str
    parser_socket: str
    api_port: int
    web_host: str
    web_port: int
    model_backend: str
    graph_extraction_mode: str
    embedding_dimension: int
    poll_seconds: float
    job_batch_size: int
    collection: str
    alias: str
    corpus_version: str
    default_chat_model: str
    embedding_model: str
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
    litellm_base_url: str
    litellm_api_key: str = field(repr=False)
    milvus_uri: str
    milvus_database: str
    milvus_reader_username: str
    milvus_reader_password: str = field(repr=False)
    milvus_writer_username: str
    milvus_writer_password: str = field(repr=False)
    milvus_provisioner_username: str
    milvus_provisioner_password: str = field(repr=False)
    e2e_mode: bool
    vision_model: str | None = None
    vision_timeout_seconds: float = 60.0
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_region: str = ""
    s3_access_key: str = field(default="", repr=False)
    s3_secret_key: str = field(default="", repr=False)
    s3_store_id: str = ""
    tenant_id: str = _FIXED_TENANT
    project_id: str = _FIXED_PROJECT
    group_id: str = _FIXED_GROUP
    environment: str = _FIXED_ENVIRONMENT
    insights_base_url: str = ""
    insights_delegated_user_token: str = field(default="", repr=False)
    insights_service_token: str = field(default="", repr=False)
    insights_authorization_version: str = ""
    insights_expires_at: str = ""
    insights_max_micros_per_token: int = 0

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> TapperSettings:
        if not isinstance(values, Mapping):
            raise TypeError("Tapper settings require a string mapping")
        insight_names = (
            "TAP_INSIGHTS_BASE_URL",
            "TAP_INSIGHTS_DELEGATED_USER_TOKEN",
            "TAP_INSIGHTS_SERVICE_TOKEN",
            "TAP_INSIGHTS_DELEGATED_PROJECT_ID",
            "TAP_INSIGHTS_DELEGATED_EXPIRES_AT",
            "TAP_INSIGHTS_AUTHORIZATION_VERSION",
            "TAP_INSIGHTS_MAX_MICROS_PER_TOKEN",
        )
        configured_insights = any(values.get(name) for name in insight_names)
        if configured_insights:
            if not all(values.get(name) for name in insight_names):
                raise ValueError(
                    "Insights explanation requires all server-side delegation settings"
                )
            if values["TAP_INSIGHTS_DELEGATED_PROJECT_ID"] != _FIXED_PROJECT:
                raise ValueError("Insights delegation must match the fixed Tapper project")
            if (
                len(values["TAP_INSIGHTS_DELEGATED_USER_TOKEN"]) < 16
                or len(values["TAP_INSIGHTS_SERVICE_TOKEN"]) < 16
            ):
                raise ValueError("Insights delegation credentials must be bounded")
            expires = datetime.fromisoformat(values["TAP_INSIGHTS_DELEGATED_EXPIRES_AT"])
            if expires.utcoffset() is None or expires <= datetime.now(UTC):
                raise ValueError("Insights delegation must have a future expiry")
            price = int(values["TAP_INSIGHTS_MAX_MICROS_PER_TOKEN"])
            if not 1 <= price <= 1000:
                raise ValueError("Insights token price ceiling is outside the bound")
            url = urlsplit(values["TAP_INSIGHTS_BASE_URL"])
            if (
                url.scheme != "http"
                or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("Insights runtime must target loopback HTTP")
            if _IDENTITY.fullmatch(values["TAP_INSIGHTS_AUTHORIZATION_VERSION"]) is None:
                raise ValueError("Insights authorization version must be bounded")
        else:
            price = 0
        backend = _fixed_choice(
            values,
            "TAPPER_MODEL_BACKEND",
            default="litellm",
            choices=frozenset({"litellm", "fake"}),
        )
        demo_mode = _value(values, "TAP_DEMO_MODE", "")
        if demo_mode not in {"", "e2e"} or ((demo_mode == "e2e") != (backend == "fake")):
            raise ValueError(
                "TAPPER_MODEL_BACKEND=fake requires exact TAP_DEMO_MODE=e2e and vice versa"
            )

        api_host = _loopback_host(values, "TAPPER_API_HOST", "127.0.0.1")
        web_host = _loopback_host(values, "TAPPER_WEB_HOST", "127.0.0.1")
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
        s3_values = {
            name: _value(values, "TAPPER_S3_" + name.upper(), "")
            for name in ("endpoint", "bucket", "region", "access_key", "secret_key", "store_id")
        }
        from pydantic import SecretStr

        from tap.platform.storage.s3 import S3ObjectConfig

        for name, value in s3_values.items():
            if not value:
                raise ValueError(f"TAPPER_S3_{name.upper()} is required")
        _loopback_url(
            values,
            "TAPPER_S3_ENDPOINT",
            "",
            schemes=frozenset({"http"}),
            allow_userinfo=False,
            root_only=True,
        )
        S3ObjectConfig(
            endpoint=s3_values["endpoint"],
            bucket=s3_values["bucket"],
            region=s3_values["region"],
            access_key=SecretStr(s3_values["access_key"]),
            secret_key=SecretStr(s3_values["secret_key"]),
            store_id=s3_values["store_id"],
        )
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
        schema_version = _fixed_choice(
            values,
            "TAPPER_SCHEMA_VERSION",
            default="doc-schema-v2",
            choices=frozenset({"doc-schema-v1", "doc-schema-v2"}),
        )
        collection = _fixed_value(
            values,
            "TAPPER_COLLECTION",
            "kb_doc_v2_tapper_demo" if schema_version == "doc-schema-v2" else _FIXED_COLLECTION,
        )
        alias = _fixed_value(values, "TAPPER_ALIAS", _FIXED_ALIAS)
        corpus = _fixed_value(
            values,
            "TAPPER_CORPUS_VERSION",
            "tapper-demo-v2" if schema_version == "doc-schema-v2" else _FIXED_CORPUS,
        )
        default_chat_model = _model_name(values, "TAPPER_DEFAULT_CHAT_MODEL", "qwen-plus")
        embedding_model = _model_name(values, "TAPPER_EMBEDDING_MODEL", "text-embedding-v4")
        vision_model = _model_name(values, "TAPPER_VISION_MODEL", "") or None
        retrieval_profile = _fixed_value(
            values,
            "TAPPER_RETRIEVAL_PROFILE",
            _FIXED_RETRIEVAL_PROFILE,
        )
        dimension = _integer(
            values,
            "TAPPER_EMBEDDING_DIMENSION",
            1536,
            minimum=1,
            maximum=4096,
        )
        if dimension != 1536:
            raise ValueError("TAPPER_EMBEDDING_DIMENSION must equal 1536")

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
            parser_socket=_value(
                values, "TAPPER_PARSER_SOCKET", "/tmp/tapper-parser-unavailable.sock"
            ),
            api_port=_integer(values, "TAPPER_API_PORT", 8000, minimum=1, maximum=65535),
            web_host=web_host,
            web_port=_integer(values, "TAPPER_WEB_PORT", 5173, minimum=1, maximum=65535),
            model_backend=backend,
            graph_extraction_mode=_fixed_choice(
                values,
                "TAPPER_GRAPH_EXTRACTION_MODE",
                default="fake",
                choices=frozenset({"fake", "model"}),
            ),
            embedding_dimension=dimension,
            poll_seconds=_duration(values, "TAPPER_POLL_SECONDS", 1.0, maximum=60),
            job_batch_size=_integer(
                values,
                "TAPPER_JOB_BATCH_SIZE",
                10,
                minimum=1,
                maximum=50,
            ),
            collection=collection,
            alias=alias,
            corpus_version=corpus,
            default_chat_model=default_chat_model,
            embedding_model=embedding_model,
            retrieval_profile=retrieval_profile,
            schema_version=schema_version,
            index_version=_fixed_value(values, "TAPPER_INDEX_VERSION", "tapper-index-v1"),
            pipeline_version=_fixed_value(values, "TAPPER_PIPELINE_VERSION", "tapper-ingestion-v1"),
            worker_id=_identity(values, "TAPPER_WORKER_ID", "tapper-local-worker"),
            compose_project=_project(values),
            ready_timeout_seconds=_duration(
                values, "TAPPER_READY_TIMEOUT_SECONDS", 2.0, maximum=30
            ),
            model_timeout_seconds=_duration(
                values, "TAPPER_MODEL_TIMEOUT_SECONDS", 15.0, maximum=60
            ),
            blob_timeout_seconds=_duration(values, "TAPPER_BLOB_TIMEOUT_SECONDS", 15.0, maximum=60),
            milvus_timeout_seconds=_duration(
                values, "TAPPER_MILVUS_TIMEOUT_SECONDS", 10.0, maximum=60
            ),
            database_url=database_url,
            alembic_database_url=alembic_database_url,
            redis_url=redis_url,
            redis_stream=_identity(values, "TAP_REDIS_COMMAND_STREAM", "tap:commands"),
            s3_endpoint=s3_values["endpoint"],
            s3_bucket=s3_values["bucket"],
            s3_region=s3_values["region"],
            s3_access_key=s3_values["access_key"],
            s3_secret_key=s3_values["secret_key"],
            s3_store_id=s3_values["store_id"],
            litellm_base_url=litellm_base_url,
            litellm_api_key=_secret(values, "LITELLM_MASTER_KEY", "tap-local-master-key"),
            vision_model=vision_model,
            vision_timeout_seconds=_duration(
                values, "TAPPER_VISION_TIMEOUT_SECONDS", 60.0, maximum=60
            ),
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
            insights_base_url=values.get("TAP_INSIGHTS_BASE_URL", ""),
            insights_delegated_user_token=values.get("TAP_INSIGHTS_DELEGATED_USER_TOKEN", ""),
            insights_service_token=values.get("TAP_INSIGHTS_SERVICE_TOKEN", ""),
            insights_authorization_version=values.get("TAP_INSIGHTS_AUTHORIZATION_VERSION", ""),
            insights_expires_at=values.get("TAP_INSIGHTS_DELEGATED_EXPIRES_AT", ""),
            insights_max_micros_per_token=price,
        )


class _AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


CloseCallback = Callable[[], Awaitable[object] | object]


class TapperEmbeddingPort(QueryEmbeddingPort, DocumentEmbeddingPort, Protocol):
    """The one real or fake adapter shared by query and document Embedding."""


class TapperFailureController(Protocol):
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
                        raise TimeoutError("Tapper runtime resource close timed out")
                    task.result()
            except BaseException as error:
                errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            if all(isinstance(error, Exception) for error in errors):
                raise ExceptionGroup(
                    "Tapper runtime lifecycle failed",
                    cast(list[Exception], errors),
                )
            raise BaseExceptionGroup("Tapper runtime lifecycle failed", errors)


def _consume_background_task(task: asyncio.Future[object]) -> None:
    """Retrieve a detached close result without extending the shutdown deadline."""

    try:
        task.exception()
    except BaseException:
        pass


@dataclass(frozen=True, slots=True)
class ReadinessNotice:
    """A healthy check with operator-safe information surfaced as the component detail."""

    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("readiness notice requires a detail")
        object.__setattr__(self, "detail", self.detail[:2048])


ReadinessCheck = Callable[[], Awaitable["bool | ReadinessNotice"]]


class ReadinessProblem(Exception):
    """A failed check with an operator-safe reason surfaced as the component detail."""

    def __init__(self, detail: str) -> None:
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError("readiness problem requires a detail")
        super().__init__(detail[:2048])
        self.detail = detail[:2048]


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
            asyncio.create_task(self._bounded(check), name=f"tapper-ready-{name.value}")
            for name, check in zip(self._ORDER, self._checks, strict=True)
        )
        results = await asyncio.gather(*tasks)
        components = [
            HealthComponent(
                name=name,
                state=HealthComponentState.OK if healthy else HealthComponentState.FAILED,
                remediation_code=None if healthy else self._REMEDIATION[name],
                detail=detail,
            )
            for name, (healthy, detail) in zip(self._ORDER, results, strict=True)
        ]
        return ReadyHealth(
            status="ready" if all(healthy for healthy, _detail in results) else "unready",
            components=components,
        )

    async def _bounded(self, check: ReadinessCheck) -> tuple[bool, str | None]:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await check()
                if isinstance(result, ReadinessNotice):
                    return True, result.detail
                return result is True, None
        except asyncio.CancelledError:
            raise
        except ReadinessProblem as problem:
            return False, problem.detail
        except Exception:
            return False, None


def create_project_audit(
    connection: AsyncConnection, *, scope: ProjectScopeContext
) -> ProjectAuditPort:
    """Bind Audit to an existing application transaction and explicit trusted scope."""
    from tap.modules.governance.adapters.mysql_audit import MysqlProjectAudit

    return MysqlProjectAudit(connection, scope=scope)


@dataclass(slots=True)
class TapperApiRuntime:
    """One API process graph with a single outer ownership boundary."""

    http_services: HttpServices
    quality_models: KnowledgeModelGateway
    _resources: OwnedResources
    failure_controller: TapperFailureController | None = None

    async def aclose(self) -> None:
        await self._resources.aclose()


async def create_api_runtime(
    settings: TapperSettings, *, model_gateway_max_retries: int = 1
) -> TapperApiRuntime:
    """Construct the API graph only after one complete settings snapshot validates."""

    if not isinstance(settings, TapperSettings):
        raise TypeError("Tapper API runtime requires validated settings")
    resources = OwnedResources()
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        engine, repository = await _create_database(settings)
        resources.push(engine)
        artifacts = _create_blob(settings)
        resources.push(artifacts)
        redis = _create_redis(settings)
        resources.push(redis)
        failure_controller = _create_stage_controller(settings, redis)
        embeddings = _create_embeddings(
            settings,
            max_retries=model_gateway_max_retries,
        )
        _push_if_owned(resources, embeddings)
        search, reader, target = await _create_search(
            settings,
            owners=repository,
            audit_sink=MysqlSearchAuditSink(
                async_sessionmaker(engine, expire_on_commit=False),
                scope=repository.scope,
                policy_version="tapper-demo-policy-v1",
            ),
        )
        resources.push(search)
        readiness = _create_readiness(
            settings=settings,
            engine=engine,
            redis=redis,
            artifacts=artifacts,
            embeddings=embeddings,
            milvus_reader=reader,
            milvus_target=target,
        )
        scope_provider, authorization_policy = _create_validation_authority(engine)
        asset_catalog = await _create_asset_catalog(engine, repository.scope)
        from tap.modules.knowledge.adapters.managed_chunk_search import ManagedChunkSearch

        chunk_index = await _create_document_index(settings, engine)
        resources.push(chunk_index)
        chunk_manager = _create_chunk_manager(
            settings, engine, repository, artifacts, embeddings, chunk_index
        )
        services = _assemble_http_services(
            repository=repository,
            artifacts=artifacts,
            search=ManagedChunkSearch(search, chunk_manager),
            embeddings=embeddings,
            readiness=readiness,
            redactor=PatternEgressRedactor(),
            scope_provider=scope_provider,
            authorization_policy=authorization_policy,
            asset_catalog=asset_catalog,
            conversation_sessions=async_sessionmaker(engine, expire_on_commit=False),
            graph_sessions=async_sessionmaker(engine, expire_on_commit=False),
            test_plan_sessions=async_sessionmaker(engine, expire_on_commit=False),
            review_sessions=async_sessionmaker(engine, expire_on_commit=False),
            parser_socket=settings.parser_socket,
            corpus_version=settings.corpus_version,
        )
        services = replace(services, chunk_manager=chunk_manager)
        if settings.insights_base_url:
            if services.insights_knowledge_search is None:
                raise ValueError("Insights knowledge evidence authority is unavailable")
            from tap.interfaces.http.insights_explanation_runtime import (
                ConfiguredInsightsExplanation,
            )
            from tap.modules.ai.adapters.published_knowledge import PublishedKnowledgeEvidence
            from tap.modules.ai.adapters.tap_insights import (
                ServiceIdentity,
                TapInsightsAdapter,
                TapInsightsConfig,
            )
            from tap.modules.knowledge.application.answers import AnswerService
            from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority

            expiry = datetime.fromisoformat(settings.insights_expires_at)
            insights_adapter = TapInsightsAdapter(
                TapInsightsConfig(base_url=settings.insights_base_url),
                service_identity=lambda: ServiceIdentity(
                    authorization=settings.insights_service_token,
                    audience="tap-insights",
                    expires_at=expiry,
                ),
                clock=lambda: datetime.now(UTC),
            )
            resources.push(insights_adapter)
            services = replace(
                services,
                insights_explanation=ConfiguredInsightsExplanation(
                    insights=insights_adapter,
                    gateway=embeddings.gateway,
                    model_alias=embeddings.chat_alias,
                    project_id=settings.project_id,
                    delegated_user_token=settings.insights_delegated_user_token,
                    authorization_version=settings.insights_authorization_version,
                    max_micros_per_token=settings.insights_max_micros_per_token,
                    knowledge_evidence_factory=lambda selection: PublishedKnowledgeEvidence(
                        searches=cast(AnswerService, services.insights_knowledge_search),
                        publication_authority=cast(
                            PublishedKnowledgeAuthority | None,
                            services.insights_publication_authority,
                        ),
                        selection=selection,
                    ),
                ),
            )
        return TapperApiRuntime(
            http_services=services,
            quality_models=embeddings,
            _resources=resources,
            failure_controller=failure_controller,
        )
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("resource settlement unexpectedly returned")


async def create_worker_runtime(settings: TapperSettings) -> WorkerRuntime:
    """Construct one ingestion worker graph behind a single outer owner."""

    if not isinstance(settings, TapperSettings):
        raise TypeError("Tapper worker runtime requires validated settings")
    resources = OwnedResources()
    try:
        engine, repository = await _create_database(settings)
        resources.push(engine)
        artifacts = _create_blob(settings)
        resources.push(artifacts)
        redis = _create_redis(settings)
        resources.push(redis)
        embeddings = _create_embeddings(settings)
        _push_if_owned(resources, embeddings)
        index = await _create_document_index(settings, engine)
        # MilvusDocumentIndex transitively owns all three role clients and the
        # projection coordinator.  Register only this complete aggregate.
        resources.push(index)
        stage_hook = _create_stage_controller(settings, redis)
        runtime = _assemble_worker_runtime(
            settings=settings,
            repository=repository,
            artifacts=artifacts,
            embeddings=embeddings,
            index=index,
            redis=redis,
            resources=resources,
            stage_hook=stage_hook,
        )
        manager = _create_chunk_manager(settings, engine, repository, artifacts, embeddings, index)
        return replace(runtime, pending_work=manager.run_pending)
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("worker resource settlement unexpectedly returned")


def _create_chunk_manager(
    settings: TapperSettings,
    engine: AsyncEngine,
    repository: MysqlDocumentRepository,
    artifacts: KnowledgeArtifactStore,
    embeddings: TapperEmbeddingPort,
    index: MilvusDocumentIndex,
) -> MysqlManagedChunks:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tap.modules.knowledge.adapters.isolated_parser import IsolatedParser
    from tap.modules.knowledge.adapters.mysql_managed_chunks import MysqlManagedChunks
    from tap.modules.knowledge.ports.documents import ArtifactStore

    return MysqlManagedChunks(
        sessions=async_sessionmaker(engine, expire_on_commit=False),
        repository=repository,
        scope=repository.scope,
        artifacts=cast(ArtifactStore, artifacts),
        embeddings=embeddings,
        index=index,
        embedding_model_alias=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
        index_version=settings.index_version,
        parser=IsolatedParser(settings.parser_socket),
    )


def _create_stage_controller(
    settings: TapperSettings,
    redis: Redis,
) -> TapperFailureController | None:
    if not settings.e2e_mode:
        return None
    from tap.testing.failure_injection import RedisStageFailureController

    return cast(
        TapperFailureController,
        RedisStageFailureController(redis=redis, project=settings.compose_project),
    )


def _push_if_owned(resources: OwnedResources, resource: object | None) -> None:
    if resource is not None and any(
        callable(getattr(resource, name, None)) for name in ("aclose", "close", "dispose")
    ):
        resources.push(resource)


async def _create_database(
    settings: TapperSettings,
) -> tuple[AsyncEngine, MysqlDocumentRepository]:
    engine, sessions = _open_database(settings)
    try:
        scope = await ValidationScopeProvider().current(RequestFacts())
        repository = _build_document_repository(
            sessions, scope=scope, default_chat_model=settings.default_chat_model
        )
    except BaseException as error:
        local = OwnedResources()
        local.push(engine)
        await local.aclose(error)
        raise AssertionError("database helper settlement unexpectedly returned")
    return engine, repository


def _open_database(
    settings: TapperSettings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    from tap.platform.db.session import create_engine_and_session_factory

    return create_engine_and_session_factory(settings.database_url)


def _build_document_repository(
    sessions: async_sessionmaker[AsyncSession],
    *,
    scope: ProjectScopeContext,
    default_chat_model: str,
) -> MysqlDocumentRepository:
    from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore, MysqlGraphReadyProjection
    from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository

    graph_jobs = MysqlGraphJobStore(sessions)
    return MysqlDocumentRepository(
        sessions,
        scope=scope,
        audit_factory=create_project_audit,
        ready_projection=MysqlGraphReadyProjection(graph_jobs, model_alias=default_chat_model),
    )


async def create_graph_worker_runtime(settings: TapperSettings) -> WorkerRuntime:
    """Construct the independently restartable durable Graph worker."""

    if not isinstance(settings, TapperSettings):
        raise TypeError("Tapper graph worker runtime requires validated settings")
    resources = OwnedResources()
    try:
        engine, sessions = _open_database(settings)
        resources.push(engine)
        scope = await ValidationScopeProvider().current(RequestFacts())
        artifacts = _create_blob(settings)
        resources.push(artifacts)
        redis = _create_redis(settings)
        resources.push(redis)

        from tap.entrypoints.tapper_ingestion_worker import WorkerRuntime
        from tap.modules.graph.adapters.fake_extraction import DeterministicGraphExtraction
        from tap.modules.graph.adapters.model_gateway_extraction import ModelGatewayGraphExtraction
        from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
        from tap.modules.graph.application.worker import GraphWorker
        from tap.modules.graph.ports.extraction import GraphExtractionPort
        from tap.modules.knowledge.ports.documents import ArtifactStore
        from tap.platform.messaging.redis_dispatch import AsyncRedisStream
        from tap.platform.messaging.redis_wakeup import RedisWakeupConsumer

        if settings.graph_extraction_mode == "model":
            embeddings = _create_embeddings(settings)
            _push_if_owned(resources, embeddings)
            extractor: GraphExtractionPort = ModelGatewayGraphExtraction(
                embeddings.gateway, timeout_seconds=settings.model_timeout_seconds
            )
        else:
            extractor = DeterministicGraphExtraction()
        worker = GraphWorker(
            jobs=MysqlGraphJobStore(sessions),
            artifacts=cast(ArtifactStore, artifacts),
            extractor=extractor,
            scope=scope,
            worker_id=settings.worker_id + "-graph",
        )
        wakeups = RedisWakeupConsumer(
            scope=scope,
            redis=cast(AsyncRedisStream, redis),
            stream_name=settings.redis_stream,
            group_name="tapper-graph",
            consumer_name=settings.worker_id + "-graph",
            aggregate_type="GraphSnapshot",
        )
        return WorkerRuntime(worker=worker, wakeups=wakeups, resources=(resources,))
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("graph worker resource settlement unexpectedly returned")


async def create_test_design_worker_runtime(settings: TapperSettings) -> WorkerRuntime:
    """Construct the independently restartable durable Test Design worker."""

    if not isinstance(settings, TapperSettings):
        raise TypeError("Tapper test design worker runtime requires validated settings")
    resources = OwnedResources()
    try:
        engine, sessions = _open_database(settings)
        resources.push(engine)
        scope = await ValidationScopeProvider().current(RequestFacts())
        redis = _create_redis(settings)
        resources.push(redis)
        models = _create_embeddings(settings)
        _push_if_owned(resources, models)

        from tap.entrypoints.tapper_ingestion_worker import WorkerRuntime
        from tap.modules.test_management.adapters.deterministic_generation import (
            DeterministicTestDesign,
        )
        from tap.modules.test_management.adapters.model_gateway_generation import (
            ModelGatewayTestDesign,
        )
        from tap.modules.test_management.adapters.mysql import (
            MysqlReconciledTestDesign,
            MysqlTestPlanRepository,
        )
        from tap.modules.test_management.application.generation import TestDesignWorker
        from tap.platform.messaging.redis_dispatch import AsyncRedisStream
        from tap.platform.messaging.redis_wakeup import RedisWakeupConsumer

        generator = (
            DeterministicTestDesign()
            if settings.e2e_mode
            else MysqlReconciledTestDesign(
                sessions,
                scope=scope,
                delegate=ModelGatewayTestDesign(
                    models.gateway, timeout_seconds=settings.model_timeout_seconds
                ),
            )
        )
        worker_id = settings.worker_id + "-test-design"
        worker = TestDesignWorker(
            jobs=MysqlTestPlanRepository(
                sessions,
                scope=scope,
                model_alias=settings.default_chat_model,
                knowledge_requires_publication=False,
            ),
            generator=generator,
            scope=scope,
            worker_id=worker_id,
        )
        wakeups = RedisWakeupConsumer(
            scope=scope,
            redis=cast(AsyncRedisStream, redis),
            stream_name=settings.redis_stream,
            group_name="tapper-test-design",
            consumer_name=worker_id,
            aggregate_type="TestPlanRevision",
        )
        return WorkerRuntime(worker=worker, wakeups=wakeups, resources=(resources,))
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("test design worker resource settlement unexpectedly returned")


def _create_blob(settings: TapperSettings) -> KnowledgeArtifactStore:
    from pydantic import SecretStr

    from tap.modules.knowledge.adapters.object_artifacts import KnowledgeArtifactStore
    from tap.platform.storage.s3 import S3ObjectConfig, S3ObjectStore

    return KnowledgeArtifactStore(
        S3ObjectStore(
            S3ObjectConfig(
                endpoint=settings.s3_endpoint,
                bucket=settings.s3_bucket,
                region=settings.s3_region,
                access_key=SecretStr(settings.s3_access_key),
                secret_key=SecretStr(settings.s3_secret_key),
                store_id=settings.s3_store_id,
                timeout_seconds=settings.blob_timeout_seconds,
            ),
            scope=VALIDATION_SCOPE,
        )
    )


def _create_redis(settings: TapperSettings) -> Redis:
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


async def _redact_model_context(text: str) -> str:
    return await PatternEgressRedactor(max_chars=262144).redact_text(text)


def _gated_ready_sources(list_sources, flowchart_gate, project_id: str):
    """Hide flowchart images from chat sources until their review is published."""
    if flowchart_gate is None:
        return list_sources

    async def gated():
        return await flowchart_gate.filter_sources(project_id, await list_sources())

    return gated


def _answer_planner(models: KnowledgeModelGateway):
    from tap.modules.chat.adapters.model_gateway_planner import ModelGatewayPlanner
    from tap.modules.chat.application.plan_answer import AnswerPlanner

    return AnswerPlanner(
        ModelGatewayPlanner(models.gateway, scope=models.scope, redact=_redact_model_context)
    )


def _create_model_catalog(settings: TapperSettings) -> LiteLLMCatalog:
    """One LiteLLM model catalog per process, owned by the gateway built on it."""

    return LiteLLMCatalog(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)


def _create_embeddings(
    settings: TapperSettings,
    *,
    max_retries: int = 1,
    catalog: LiteLLMCatalog | None = None,
) -> KnowledgeModelGateway:
    config = LiteLLMModelGatewayConfig(
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
        roles=ModelRoles(
            settings.default_chat_model, settings.embedding_model, settings.vision_model
        ),
        embedding_dimension=settings.embedding_dimension,
        timeout_seconds=(
            max(settings.model_timeout_seconds, settings.vision_timeout_seconds)
            if settings.vision_model
            else settings.model_timeout_seconds
        ),
        max_retries=max_retries,
    )
    gateway: LiteLLMModelGateway
    if settings.e2e_mode:
        from tap.testing.deterministic_model_gateway import DeterministicModelGateway

        gateway = DeterministicModelGateway(
            config, scope=VALIDATION_SCOPE, redact=_redact_model_context
        )
    else:
        gateway = LiteLLMModelGateway(
            config,
            scope=VALIDATION_SCOPE,
            redact=_redact_model_context,
            catalog=catalog or _create_model_catalog(settings),
        )
    return KnowledgeModelGateway(
        gateway,
        scope=VALIDATION_SCOPE,
        redact=_redact_model_context,
        embedding_alias=settings.embedding_model,
        chat_alias=settings.default_chat_model,
        embedding_dimension=settings.embedding_dimension,
        timeout_seconds=settings.model_timeout_seconds,
    )


async def _create_document_index(
    settings: TapperSettings,
    engine: AsyncEngine,
) -> MilvusDocumentIndex:
    """Build the role-isolated index and settle every untransferred child."""

    parts = await _open_document_clients(settings)
    coordinator: MysqlProjectionCoordinator | None = None
    try:
        scope = await ValidationScopeProvider().current(RequestFacts())
        coordinator = _create_projection_coordinator(settings, engine, scope=scope)
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
    settings: TapperSettings,
) -> TapperDocumentMilvusClients:
    from pydantic import SecretStr

    from tap.operations.milvus.client import create_tapper_document_clients

    return await create_tapper_document_clients(
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
    settings: TapperSettings,
    engine: AsyncEngine,
    *,
    scope: ProjectScopeContext,
) -> MysqlProjectionCoordinator:
    from tap.modules.knowledge.adapters.mysql_projection import MysqlProjectionCoordinator

    return MysqlProjectionCoordinator(
        engine,
        authority_namespace=settings.compose_project,
        scope=scope,
    )


def _build_document_index(
    settings: TapperSettings,
    coordinator: MysqlProjectionCoordinator,
    parts: TapperDocumentMilvusClients,
) -> MilvusDocumentIndex:
    from tap.modules.knowledge.adapters.milvus_documents import (
        MilvusDocumentIndex,
        TapperMilvusConfig,
    )

    config = TapperMilvusConfig(
        physical_collection=settings.collection,
        alias=settings.alias,
        schema_version=settings.schema_version,
        corpus_version=settings.corpus_version,
        embedding_model=settings.embedding_model,
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
    settings: TapperSettings,
    *,
    audit_sink: SearchAuditSink,
    owners: AnswerSnapshotRepository | None = None,
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
        schema_sha256=doc_schema_sha256(settings.schema_version),
        corpus_version=settings.corpus_version,
        embedding_model_version=settings.embedding_model,
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
        search = _build_search_adapter(config, reader, audit_sink=audit_sink, owners=owners)
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
    *,
    audit_sink: SearchAuditSink,
    owners: AnswerSnapshotRepository | None = None,
) -> MilvusSearchAdapter:
    from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter

    return MilvusSearchAdapter(
        config,
        reader,
        audit_sink,
        owners=owners,
    )


def _create_readiness(
    *,
    settings: TapperSettings,
    engine: AsyncEngine,
    redis: Redis,
    artifacts: KnowledgeArtifactStore,
    embeddings: QueryEmbeddingPort,
    milvus_reader: MilvusReader,
    milvus_target: MilvusIndexTarget,
) -> ReadinessService:
    from sqlalchemy import text

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
        return await artifacts.is_private()

    async def milvus_ready() -> bool:
        bound = await bind_target(milvus_reader, milvus_target)
        rows = await milvus_reader.query(
            MilvusQueryRequest(
                collection_name=bound.physical_collection,
                filter_expression=('chunk_id == "__tapper_readiness_reserved_never_persisted__"'),
                output_fields=("chunk_id",),
                limit=1,
            )
        )
        return rows == ()

    async def models_ready() -> bool | ReadinessNotice:
        if settings.e2e_mode:
            embedding = await embeddings.embed("Tapper deterministic readiness")
            vector = embedding.vector
            return (
                embedding.model_id == settings.embedding_model
                and isinstance(vector, tuple)
                and len(vector) == settings.embedding_dimension
                and all(type(value) is float and math.isfinite(value) for value in vector)
                and math.isclose(
                    math.sqrt(sum(value * value for value in vector)),
                    1.0,
                    rel_tol=1e-12,
                )
            )
        gateway = getattr(embeddings, "gateway", None)
        if not isinstance(gateway, LiteLLMModelGateway):
            return False
        health = await gateway.health()
        if health.problems:
            raise ReadinessProblem("; ".join(health.problems))
        if health.notices:
            return ReadinessNotice("; ".join(health.notices))
        return True

    return ReadinessService(
        mysql=mysql_ready,
        redis=redis_ready,
        blob=blob_ready,
        milvus=milvus_ready,
        models=models_ready,
        timeout_seconds=settings.ready_timeout_seconds,
    )


def _discover_alembic_head() -> str:
    """Resolve the checked-in migration head without opening a database connection."""

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    if len(heads) != 1 or not heads[0]:
        raise RuntimeError("Tapper requires one current Alembic head")
    return heads[0]


def _create_validation_authority(engine: AsyncEngine) -> tuple[ScopeProvider, AuthorizationPolicy]:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tap.modules.access.adapters.mysql import MysqlIdentityRegistry
    from tap.modules.access.adapters.validation import (
        ValidationAuthorizationPolicy,
        ValidationScopeProvider,
    )

    registry = MysqlIdentityRegistry(async_sessionmaker(engine, expire_on_commit=False))
    return ValidationScopeProvider(), ValidationAuthorizationPolicy(registry)


async def _create_asset_catalog(engine: AsyncEngine, scope: ProjectScopeContext) -> object:
    """Create and seed the only server-approved, project-scoped AI catalog."""

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.assets import validation_asset_seed

    catalog = MysqlAssetCatalog(async_sessionmaker(engine, expire_on_commit=False), scope=scope)
    await catalog.seed(validation_asset_seed(scope))
    return catalog


def _assemble_http_services(
    *,
    repository: MysqlDocumentRepository,
    artifacts: KnowledgeArtifactStore,
    search: SearchPort,
    embeddings: KnowledgeModelGateway,
    readiness: ReadinessHttpService,
    redactor: EgressRedactionPort,
    scope_provider: ScopeProvider,
    authorization_policy: AuthorizationPolicy,
    asset_catalog: object | None = None,
    conversation_sessions: object | None = None,
    graph_sessions: object | None = None,
    test_plan_sessions: object | None = None,
    review_sessions: async_sessionmaker[AsyncSession] | None = None,
    parser_socket: str | None = None,
    corpus_version: str = "tapper-demo-v1",
) -> HttpServices:
    """Assemble the one approved Tapper application graph from existing services."""

    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.knowledge.api import KnowledgeAPI
    from tap.modules.knowledge.application.answers import AnswerService
    from tap.modules.knowledge.application.citations import CitationResolver
    from tap.modules.knowledge.application.demo_policy import DemoCurrentPolicyVerifier
    from tap.modules.knowledge.application.documents import DocumentService
    from tap.modules.knowledge.application.sources import SourceService
    from tap.modules.knowledge.ports.answers import AnswerSnapshotRepository
    from tap.modules.knowledge.ports.citations import (
        CitationArtifactStore,
        CitationRepository,
    )
    from tap.modules.knowledge.ports.documents import ArtifactStore, DocumentRepository
    from tap.modules.knowledge.ports.sources import SourceRepository

    document_repository = cast(DocumentRepository, repository)
    artifact_store = cast(ArtifactStore, artifacts)

    review_repository = None
    publication_authority = None
    flowchart_gate = None
    if review_sessions is not None:
        from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
        from tap.modules.knowledge.application.publication import (
            FlowchartPublicationGate,
            PublishedKnowledgeAuthority,
        )

        review_repository = MysqlKnowledgeReviewRepository(
            review_sessions,
            scope=repository.scope,  # type: ignore[arg-type]
        )
        # Chunks index directly; flowchart images still answer only after review.
        flowchart_gate = FlowchartPublicationGate(PublishedKnowledgeAuthority(review_repository))

    documents = DocumentService(repository=document_repository, artifacts=artifact_store)
    knowledge = KnowledgeAPI(
        search=search,
        models=embeddings,
        policy_verifier=DemoCurrentPolicyVerifier(
            repository,
            scope_provider=scope_provider,
            authorization_policy=authorization_policy,
            corpus_version=corpus_version,
        ),
        redactor=redactor,
        publication_authority=publication_authority,
        flowchart_gate=flowchart_gate,
    )
    answer_service = AnswerService(
        repository=cast(AnswerSnapshotRepository, repository),
        knowledge=knowledge,
        corpus_version=corpus_version,
        publication_authority=publication_authority,
        flowchart_gate=flowchart_gate,
    )
    search_service = answer_service
    citations = CitationResolver(
        repository=cast(CitationRepository, repository),
        artifacts=cast(CitationArtifactStore, artifacts),
        publication_authority=publication_authority,
    )
    conversations = None
    if conversation_sessions is not None:
        from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
        from tap.modules.chat.application.conversations import ConversationService

        conversations = ConversationService(
            MysqlConversationRepository(
                conversation_sessions,  # type: ignore[arg-type]
                scope=repository.scope,
                default_chat_model=embeddings.chat_alias,
            ),
            scope=repository.scope,
        )
    graph = None
    graph_enricher = None
    if graph_sessions is not None:
        from tap.modules.graph.adapters.mysql import MysqlGraphStore
        from tap.modules.knowledge.application.graph_enrichment import GraphAnswerEnricher

        graph = MysqlGraphStore(graph_sessions)  # type: ignore[arg-type]
        graph_enricher = GraphAnswerEnricher(graph, publication_authority=publication_authority)
    test_plans = None
    if test_plan_sessions is not None:
        from tap.modules.test_management.adapters.mysql import MysqlTestPlanRepository
        from tap.modules.test_management.application.plans import TestPlanApplication

        test_plans = TestPlanApplication(
            MysqlTestPlanRepository(
                test_plan_sessions,  # type: ignore[arg-type]
                scope=repository.scope,  # type: ignore[arg-type]
                model_alias=embeddings.chat_alias,
                knowledge_requires_publication=False,
            )
        )
    knowledge_reviews = None
    if review_sessions is not None:
        from tap.interfaces.http.knowledge_review_service import KnowledgeReviewHttpService
        from tap.modules.knowledge.adapters.isolated_parser import IsolatedParser
        from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources
        from tap.modules.knowledge.adapters.mysql_review import (
            MysqlApprovedProjectionVerifier,
        )
        from tap.modules.knowledge.application.review import KnowledgeReviewApplication

        assert review_repository is not None
        knowledge_reviews = KnowledgeReviewHttpService(
            KnowledgeReviewApplication(
                review_repository,
                MysqlApprovedProjectionVerifier(
                    review_sessions,
                    scope=repository.scope,  # type: ignore[arg-type]
                ),
                artifact_store,
                parser=None if parser_socket is None else IsolatedParser(parser_socket),
            ),
            scope=repository.scope,
            authorization_policy=authorization_policy,
            ready_sources=_gated_ready_sources(
                MysqlReadySources(review_sessions, repository.scope).list_sources,
                flowchart_gate,
                repository.scope.project_id,
            ),
            source_impact_notifier=(
                None if test_plans is None else test_plans.mark_knowledge_sources_changed
            ),
        )
    return HttpServices(
        asset_catalog=asset_catalog,  # type: ignore[arg-type]
        model_catalog=ModelCatalog(
            embeddings.gateway,
            scope=embeddings.scope,
            default_alias=embeddings.chat_alias,
        ),
        knowledge=KnowledgeHttpService(
            documents=documents,
            answers=answer_service,
            citations=citations,
            searches=search_service,
            sources=SourceService(cast(SourceRepository, repository), documents),
            corpus_version=corpus_version,
            graph_enricher=graph_enricher,
            models=embeddings,
            answer_planner=_answer_planner(embeddings),
        ),
        readiness=readiness,
        scope_provider=scope_provider,
        authorization_policy=authorization_policy,
        scope=repository.scope,
        conversations=conversations,
        graph=graph,
        test_plans=test_plans,
        knowledge_reviews=knowledge_reviews,
        insights_knowledge_search=answer_service,
        insights_publication_authority=publication_authority,
    )


def _assemble_worker_runtime(
    *,
    settings: TapperSettings,
    repository: MysqlDocumentRepository,
    artifacts: KnowledgeArtifactStore,
    embeddings: TapperEmbeddingPort,
    index: MilvusDocumentIndex,
    redis: Redis,
    resources: OwnedResources,
    stage_hook: IngestionStageHook | None,
) -> WorkerRuntime:
    """Assemble only the existing ingestion service and durable wake-up path."""

    from tap.entrypoints.tapper_ingestion_worker import WorkerRuntime
    from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
    from tap.modules.knowledge.adapters.flowchart_vision import ModelGatewayFlowchartVision
    from tap.modules.knowledge.adapters.isolated_parser import IsolatedParser
    from tap.modules.knowledge.application.ingestion import IngestionWorker
    from tap.modules.knowledge.ports.documents import (
        ArtifactStore,
        DocumentEmbeddingPort,
        DocumentIndexPort,
        DocumentRepository,
    )
    from tap.platform.messaging.redis_dispatch import AsyncRedisStream
    from tap.platform.messaging.redis_wakeup import RedisWakeupConsumer

    vision = None
    if settings.vision_model:
        gateway = getattr(embeddings, "gateway", None)
        if gateway is None:
            raise ValueError("configured vision requires the governed model gateway")
        vision = ModelGatewayFlowchartVision(
            gateway,
            repository.scope,
            alias=settings.vision_model,
            timeout_seconds=settings.vision_timeout_seconds,
        )
    worker = IngestionWorker(
        repository=cast(DocumentRepository, repository),
        artifacts=cast(ArtifactStore, artifacts),
        parser=IsolatedParser(settings.parser_socket),
        chunker=StructuralChunker(),
        embeddings=cast(DocumentEmbeddingPort, embeddings),
        index=cast(DocumentIndexPort, index),
        worker_id=settings.worker_id,
        embedding_model_alias=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
        index_version=settings.index_version,
        stage_hook=stage_hook,
        vision=vision,
    )
    wakeups = RedisWakeupConsumer(
        scope=repository.scope,
        redis=cast(AsyncRedisStream, redis),
        stream_name=settings.redis_stream,
        group_name="tapper-ingestion",
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
        raise ValueError(f"{name} must use the fixed Tapper value")
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


def _model_name(values: Mapping[str, str], name: str, default: str) -> str:
    """Read one LiteLLM `model_name`; an empty optional role stays empty."""

    value = _value(values, name, default)
    if value == "" and default == "":
        return value
    if len(value) > 128 or _MODEL_NAME.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lower-case LiteLLM model name")
    return value


def _project(values: Mapping[str, str]) -> str:
    name = "TAP_TAPPER_COMPOSE_PROJECT"
    value = _value(values, name, "tap-tapper-demo")
    if _PROJECT.fullmatch(value) is None:
        raise ValueError(f"{name} must be a safe Compose project")
    return value


def _secret(values: Mapping[str, str], name: str, default: str) -> str:
    value = _value(values, name, default)
    if not value or len(value) > 4096 or "\x00" in value:
        raise ValueError(f"{name} must be a nonblank bounded secret")
    return value
