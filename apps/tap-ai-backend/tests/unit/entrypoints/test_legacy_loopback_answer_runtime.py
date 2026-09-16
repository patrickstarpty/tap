from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_tapper_runtime import valid_settings

from tap.entrypoints.legacy_loopback_answer_runtime import (
    LegacyLoopbackSettings,
    build_legacy_app,
)
from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tests.conftest import validation_http_services


def _runtime():
    return importlib.import_module("tap.entrypoints.legacy_loopback_answer_runtime")


def test_codex_settings_accept_the_approved_configuration() -> None:
    settings = _runtime().TapperSettings.from_mapping(
        valid_settings()
        | {
            "TAPPER_ANSWER_BACKEND": "codex",
            "TAPPER_CODEX_MODEL": "gpt-5.6-sol",
            "TAPPER_CODEX_REASONING_EFFORT": "ultra",
            "TAPPER_CODEX_TIMEOUT_SECONDS": "300",
        }
    )

    assert (
        settings.answer_backend,
        settings.codex_model,
        settings.codex_reasoning_effort,
        settings.codex_timeout_seconds,
    ) == ("codex", "gpt-5.6-sol", "ultra", 300.0)


@pytest.mark.parametrize("backend", ["", "Codex", "codex ", "codex/litellm", "fake"])
def test_codex_settings_reject_an_unapproved_answer_backend(backend: str) -> None:
    with pytest.raises(ValueError, match="TAPPER_ANSWER_BACKEND"):
        _runtime().TapperSettings.from_mapping(
            valid_settings() | {"TAPPER_ANSWER_BACKEND": backend}
        )


@pytest.mark.parametrize(
    "model",
    [
        "GPT-5.6-sol",
        "gpt 5.6-sol",
        "openai/gpt-5.6-sol",
        "gpt-5.6-sol\n",
        "a" * 129,
    ],
)
def test_codex_settings_reject_a_widened_model_name(model: str) -> None:
    with pytest.raises(ValueError, match="TAPPER_CODEX_MODEL"):
        _runtime().TapperSettings.from_mapping(valid_settings() | {"TAPPER_CODEX_MODEL": model})


@pytest.mark.parametrize("effort", ["", "HIGH", "highest"])
def test_codex_settings_reject_an_unsupported_reasoning_effort(effort: str) -> None:
    with pytest.raises(ValueError, match="TAPPER_CODEX_REASONING_EFFORT"):
        _runtime().TapperSettings.from_mapping(
            valid_settings() | {"TAPPER_CODEX_REASONING_EFFORT": effort}
        )


@pytest.mark.parametrize("timeout", ["29.9", "901", "NaN", "Infinity"])
def test_codex_settings_reject_an_unsafe_timeout(timeout: str) -> None:
    with pytest.raises(ValueError, match="TAPPER_CODEX_TIMEOUT_SECONDS"):
        _runtime().TapperSettings.from_mapping(
            valid_settings() | {"TAPPER_CODEX_TIMEOUT_SECONDS": timeout}
        )


def test_codex_settings_reject_a_boolean_timeout() -> None:
    values = valid_settings()
    values["TAPPER_CODEX_TIMEOUT_SECONDS"] = True  # type: ignore[assignment]

    with pytest.raises(ValueError, match="TAPPER_CODEX_TIMEOUT_SECONDS"):
        _runtime().TapperSettings.from_mapping(values)


def test_codex_settings_reject_the_fake_model_backend() -> None:
    with pytest.raises(ValueError, match="TAPPER_ANSWER_BACKEND"):
        _runtime().TapperSettings.from_mapping(
            valid_settings()
            | {
                "TAP_DEMO_MODE": "e2e",
                "TAPPER_MODEL_BACKEND": "fake",
                "TAPPER_ANSWER_BACKEND": "codex",
            }
        )


def test_codex_settings_parse_without_cli_or_network_probes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def unexpected_probe(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("settings parsing performed a runtime probe")

    monkeypatch.setattr(shutil, "which", unexpected_probe)
    monkeypatch.setattr(subprocess, "run", unexpected_probe)
    monkeypatch.setattr(socket, "create_connection", unexpected_probe)

    settings = _runtime().TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )

    assert settings.answer_backend == "codex"


@pytest.mark.asyncio
async def test_codex_api_composes_litellm_embeddings_and_codex_answers(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """The selected answer backend must not replace query Embedding."""

    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )
    events: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            events.append(self.name)

    engine = Resource("engine")
    blob = Resource("blob")
    redis = Resource("redis")
    embeddings = Resource("embeddings")
    codex = Resource("codex")
    search = Resource("search")

    async def database(_settings):  # type: ignore[no-untyped-def]
        return engine, SimpleNamespace(scope=VALIDATION_SCOPE)

    async def create_search(_settings, *, audit_sink, owners=None):  # type: ignore[no-untyped-def]
        return search, object(), object()

    def legacy_model(_settings):  # type: ignore[no-untyped-def]
        raise AssertionError("legacy combined model factory called")

    monkeypatch.setattr(module, "_create_database", database)
    monkeypatch.setattr(module, "_create_blob", lambda _settings: blob)
    monkeypatch.setattr(module, "_create_redis", lambda _settings: redis)
    monkeypatch.setattr(module, "_create_model", legacy_model, raising=False)
    monkeypatch.setattr(
        module,
        "_create_embeddings",
        lambda _settings: embeddings,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "_create_answer_backend",
        lambda _settings, *, embeddings: module.TapperAnswerBackend(
            generator=codex,
            readiness=codex.aclose,
            owner=codex,
        ),
        raising=False,
    )
    monkeypatch.setattr(module, "_create_search", create_search)
    monkeypatch.setattr(module, "_create_models_probe_client", lambda _settings: None)
    monkeypatch.setattr(module, "_create_readiness", lambda **_kwargs: object())

    graph = await module.create_api_runtime(settings)
    retrieval = graph.http_services.knowledge._answers._knowledge._retrieval

    assert retrieval._embeddings is embeddings
    assert retrieval._answers is codex
    await graph.aclose()
    await graph.aclose()
    assert events == ["search", "codex", "embeddings", "redis", "blob", "engine"]


def test_litellm_answer_backend_reuses_the_embedding_adapter_without_a_second_owner() -> None:
    module = _runtime()
    settings = module.TapperSettings.from_mapping(valid_settings())
    embeddings = object()

    backend = module._create_answer_backend(settings, embeddings=embeddings)

    assert backend.generator is embeddings
    assert backend.readiness is None
    assert backend.owner is None


def test_codex_answer_backend_uses_only_the_resolved_login_location(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    from tap.modules.knowledge.adapters.codex_exec import CodexExecAnswerAdapter
    from tap.modules.knowledge.adapters.codex_target import (
        NativeCodexTarget,
        NativeTargetHeader,
        NativeTargetIdentity,
    )

    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )
    command = tmp_path / "codex"
    command.write_bytes(b"native-candidate")
    command.chmod(0o700)
    command_stat = command.stat()
    target = NativeCodexTarget(
        executable=command.resolve(),
        install_root=tmp_path.resolve(),
        version="0.149.0",
        identity=NativeTargetIdentity(
            device=command_stat.st_dev,
            inode=command_stat.st_ino,
            size=command_stat.st_size,
            mtime_ns=command_stat.st_mtime_ns,
        ),
        header=NativeTargetHeader(
            format="mach-o",
            magic=b"\xcf\xfa\xed\xfe",
            bits=64,
            byteorder="little",
            machine=0x0100000C,
        ),
    )
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    auth = codex_home / "auth.json"
    auth.write_text("PRIVATE_AUTH_CONTENT", encoding="utf-8")
    resolver_calls: list[tuple[Path, str, str, str, int]] = []

    def resolve(
        path: Path,
        *,
        system: str,
        machine: str,
        expected_version: str,
        uid: int,
    ) -> NativeCodexTarget:
        resolver_calls.append((path, system, machine, expected_version, uid))
        return target

    original_read_text = Path.read_text

    def forbid_auth_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == auth:
            raise AssertionError("runtime read Codex auth content")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(shutil, "which", lambda name: str(command) if name == "codex" else None)
    monkeypatch.setattr(
        module,
        "resolve_native_codex_target",
        resolve,
        raising=False,
    )
    monkeypatch.setattr(Path, "read_text", forbid_auth_read)

    backend = module._create_answer_backend(settings, embeddings=object())

    assert isinstance(backend.generator, CodexExecAnswerAdapter)
    assert backend.readiness == backend.generator.check_ready
    assert backend.owner is backend.generator
    assert backend.generator.config.target is target
    assert backend.generator.config.codex_home == codex_home.resolve()
    assert backend.generator.config.model_id == "gpt-5.6-sol"
    assert backend.generator.config.reasoning_effort == "ultra"
    assert backend.generator.config.timeout_seconds == 300.0
    assert backend.generator.config.profile_id == "quick-hybrid-v1"
    assert resolver_calls == [
        (
            command,
            module.platform.system(),
            module.platform.machine(),
            "0.149.0",
            module.os.getuid(),
        )
    ]


@pytest.mark.asyncio
async def test_codex_answer_backend_starts_unavailable_without_exposing_a_login_path(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from tap.modules.knowledge.ports.errors import AnswerUnavailable

    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )

    class Embeddings:
        def __init__(self) -> None:
            self.answer_calls = 0

        async def answer(self, *_args: object, **_kwargs: object) -> None:
            self.answer_calls += 1
            raise AssertionError("LiteLLM answer fallback was called")

    embeddings = Embeddings()
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    backend = module._create_answer_backend(settings, embeddings=embeddings)

    assert backend.owner is None
    assert backend.readiness is not None
    with pytest.raises(AnswerUnavailable) as readiness:
        await backend.readiness()
    with pytest.raises(AnswerUnavailable) as request:
        await backend.generator.answer("query", (), "quick-hybrid-v1")

    assert str(readiness.value) == "Codex answer backend is unavailable"
    assert str(request.value) == "Codex answer backend is unavailable"
    assert "/" not in str(readiness.value)
    assert embeddings.answer_calls == 0


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "resolve-rejected",
        "missing-codex-home",
        "nondirectory-codex-home",
    ],
)
@pytest.mark.asyncio
async def test_unavailable_codex_discovery_keeps_api_live_and_answers_closed(
    monkeypatch,
    tmp_path: Path,
    failure: str,
) -> None:  # type: ignore[no-untyped-def]
    from tap.modules.knowledge.adapters.codex_target import CodexTargetRejected

    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )
    events: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            events.append(self.name)

    class UnavailableKnowledge:
        scope = VALIDATION_SCOPE

        def __init__(self, answers) -> None:  # type: ignore[no-untyped-def]
            self._answers = answers

        async def answer(self, request):  # type: ignore[no-untyped-def]
            return await self._answers.answer(request.query, (), "quick-hybrid-v1")

    async def create_database(_settings):  # type: ignore[no-untyped-def]
        return Resource("engine"), SimpleNamespace(scope=VALIDATION_SCOPE)

    async def create_search(_settings, *, audit_sink, owners=None):  # type: ignore[no-untyped-def]
        return Resource("search"), object(), object()

    def assemble(**kwargs):  # type: ignore[no-untyped-def]
        return validation_http_services(
            knowledge=UnavailableKnowledge(kwargs["answers"]),
            readiness=kwargs["readiness"],
        )

    if failure == "missing":
        monkeypatch.setattr(shutil, "which", lambda name: None if name == "codex" else None)
    elif failure == "resolve-rejected":
        private_path = "/private/login/bin/codex"
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name: private_path if name == "codex" else None,
        )

        def reject(*_args: object, **_kwargs: object) -> None:
            raise CodexTargetRejected(f"rejected {private_path}")

        monkeypatch.setattr(module, "resolve_native_codex_target", reject)
    else:
        private_path = "/private/login/bin/codex"
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name: private_path if name == "codex" else None,
        )
        monkeypatch.setattr(
            module, "resolve_native_codex_target", lambda *_args, **_kwargs: object()
        )
        codex_home = tmp_path / "invalid-private-login"
        if failure == "nondirectory-codex-home":
            codex_home.write_text("not a login directory", encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

    monkeypatch.setattr(module, "_create_database", create_database)
    monkeypatch.setattr(module, "_create_blob", lambda _settings: Resource("blob"))
    monkeypatch.setattr(module, "_create_redis", lambda _settings: Resource("redis"))
    monkeypatch.setattr(module, "_create_embeddings", lambda _settings: Resource("embeddings"))
    monkeypatch.setattr(module, "_create_search", create_search)
    monkeypatch.setattr(module, "_create_models_probe_client", lambda _settings: None)
    monkeypatch.setattr(module, "_create_readiness", lambda **_kwargs: object())
    monkeypatch.setattr(module, "_assemble_http_services", assemble)

    runtime = await module.create_api_runtime(settings)
    try:
        client = TestClient(
            create_app(
                runtime.http_services, allowed_origins=frozenset({"http://127.0.0.1:15175"})
            ),
            raise_server_exceptions=False,
            headers={"Origin": "http://127.0.0.1:15175"},
        )

        liveness = client.get("/health/live")
        response = client.post(
            "/api/v1/projects/tapper-demo/knowledge/answers",
            json={
                "query": "What is the rule?",
                "resourceRefs": [{"family": "doc", "sourceId": "doc-a", "mode": "scope"}],
            },
        )

        assert liveness.status_code == 200
        assert liveness.json() == {"status": "ok"}
        assert response.status_code == 503
        correlation_id = response.headers["X-Correlation-ID"]
        assert len(correlation_id) == 32
        assert all(character in "0123456789abcdef" for character in correlation_id)
        assert response.json() == {
            "type": "https://tap.example/problems/answer-unavailable",
            "title": "Answer unavailable",
            "status": 503,
            "detail": "The answer service is currently unavailable.",
            "correlationId": correlation_id,
            "retryable": True,
            "failureStage": "answer",
        }
        assert "private" not in response.text
        assert "login" not in response.text
    finally:
        await runtime.aclose()
        await runtime.aclose()

    assert events == ["search", "embeddings", "redis", "blob", "engine"]


@pytest.mark.parametrize("stage", ["resolver", "config", "adapter"])
@pytest.mark.parametrize(
    "error_type",
    [AttributeError, OSError, RuntimeError, ValueError],
)
def test_codex_factory_propagates_unrelated_programmer_failures(
    monkeypatch,
    stage: str,
    error_type: type[Exception],
) -> None:  # type: ignore[no-untyped-def]
    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )
    primary = error_type(f"private-{stage}-bug")
    monkeypatch.setattr(shutil, "which", lambda _name: "/private/bin/codex")

    if stage == "resolver":

        def fail_resolver(*_args: object, **_kwargs: object) -> None:
            raise primary

        monkeypatch.setattr(module, "resolve_native_codex_target", fail_resolver)
    else:
        monkeypatch.setattr(
            module,
            "resolve_native_codex_target",
            lambda *_args, **_kwargs: object(),
        )

    if stage == "config":

        def fail_config(*_args: object, **_kwargs: object) -> None:
            raise primary

        monkeypatch.setattr(module, "_codex_config", fail_config)
    elif stage == "adapter":
        monkeypatch.setattr(module, "_codex_config", lambda *_args, **_kwargs: object())

        def fail_adapter(*_args: object, **_kwargs: object) -> None:
            raise primary

        monkeypatch.setattr(module, "CodexExecAnswerAdapter", fail_adapter)

    with pytest.raises(error_type) as raised:
        module._create_answer_backend(settings, embeddings=object())

    assert raised.value is primary


@pytest.mark.parametrize("primary", [asyncio.CancelledError(), SystemExit(17)])
def test_codex_factory_never_catches_process_control_failures(
    monkeypatch,
    primary: BaseException,
) -> None:  # type: ignore[no-untyped-def]
    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/private/bin/codex")

    def fail_resolver(*_args: object, **_kwargs: object) -> None:
        raise primary

    monkeypatch.setattr(module, "resolve_native_codex_target", fail_resolver)

    with pytest.raises(type(primary)) as raised:
        module._create_answer_backend(settings, embeddings=object())

    assert raised.value is primary


@pytest.mark.asyncio
async def test_codex_owner_closes_once_when_api_construction_fails_after_selection(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )
    events: list[str] = []
    primary = RuntimeError("search-construction-failed")

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            events.append(self.name)

    engine = Resource("engine")
    blob = Resource("blob")
    redis = Resource("redis")
    embeddings = Resource("embeddings")
    codex = Resource("codex")

    async def create_database(_settings):  # type: ignore[no-untyped-def]
        return engine, SimpleNamespace(scope=VALIDATION_SCOPE)

    async def fail_search(_settings, *, audit_sink, owners=None):  # type: ignore[no-untyped-def]
        raise primary

    monkeypatch.setattr(module, "_create_database", create_database)
    monkeypatch.setattr(module, "_create_blob", lambda _settings: blob)
    monkeypatch.setattr(module, "_create_redis", lambda _settings: redis)
    monkeypatch.setattr(module, "_create_embeddings", lambda _settings: embeddings)
    monkeypatch.setattr(
        module,
        "_create_answer_backend",
        lambda _settings, *, embeddings: module.TapperAnswerBackend(
            generator=codex,
            readiness=codex.aclose,
            owner=codex,
        ),
    )
    monkeypatch.setattr(module, "_create_search", fail_search)

    with pytest.raises(RuntimeError) as captured:
        await module.create_api_runtime(settings)

    assert captured.value is primary
    assert events == ["codex", "embeddings", "redis", "blob", "engine"]


@pytest.mark.asyncio
async def test_codex_worker_constructs_only_litellm_embeddings(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )

    class Resource:
        async def aclose(self) -> None:
            return None

    async def database(_settings):  # type: ignore[no-untyped-def]
        return Resource(), SimpleNamespace(scope=VALIDATION_SCOPE)

    async def document_index(_settings, _engine):  # type: ignore[no-untyped-def]
        return Resource()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("worker attempted answer backend construction or discovery")

    original_which = shutil.which

    def forbid_codex_discovery(name: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if name == "codex":
            forbidden(name)
        return original_which(name, *args, **kwargs)

    monkeypatch.setattr(module, "_create_database", database)
    monkeypatch.setattr(module, "_create_blob", lambda _settings: Resource())
    monkeypatch.setattr(module, "_create_redis", lambda _settings: Resource())
    monkeypatch.setattr(module, "_create_model", forbidden, raising=False)
    monkeypatch.setattr(
        module,
        "_create_embeddings",
        lambda _settings: Resource(),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "_create_answer_backend",
        forbidden,
        raising=False,
    )
    monkeypatch.setattr(shutil, "which", forbid_codex_discovery)
    monkeypatch.setattr(module, "_create_document_index", document_index)
    monkeypatch.setattr(module, "_create_stage_controller", lambda *_args: None)

    graph = await module.create_worker_runtime(settings)

    assert graph.worker is not None
    await graph.resources[0].aclose()


@pytest.mark.asyncio
async def test_codex_models_readiness_checks_embedding_before_non_generating_cli() -> None:
    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )
    readiness_calls: list[str] = []

    class Response:
        status_code = 200
        content = json.dumps(
            {
                "object": "list",
                "data": [
                    {
                        "id": "tapper-embedding",
                        "object": "model",
                        "created": 1,
                        "owned_by": "litellm",
                    }
                ],
            }
        ).encode()

        async def aiter_bytes(self):  # type: ignore[no-untyped-def]
            yield self.content

    class ResponseContext:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class ModelsClient:
        def stream(self, method: str, path: str) -> ResponseContext:
            assert (method, path) == ("GET", "v1/models")
            return ResponseContext()

    async def codex_ready() -> None:
        readiness_calls.append("codex-ready")

    service = module._create_readiness(
        settings=settings,
        engine=object(),
        redis=object(),
        artifacts=object(),
        embeddings=object(),
        answer_backend=module.TapperAnswerBackend(
            generator=object(),
            readiness=codex_ready,
            owner=object(),
        ),
        milvus_reader=object(),
        milvus_target=object(),
        models_probe_client=ModelsClient(),
    )

    assert await service._checks[4]() is True
    assert readiness_calls == ["codex-ready"]


@pytest.mark.asyncio
async def test_codex_models_readiness_skips_cli_when_embedding_alias_is_missing() -> None:
    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )
    readiness_calls: list[str] = []

    class Response:
        status_code = 200
        content = json.dumps(
            {
                "object": "list",
                "data": [
                    {
                        "id": "tapper-chat",
                        "object": "model",
                        "created": 1,
                        "owned_by": "litellm",
                    }
                ],
            }
        ).encode()

        async def aiter_bytes(self):  # type: ignore[no-untyped-def]
            yield self.content

    class ResponseContext:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class ModelsClient:
        def stream(self, _method: str, _path: str) -> ResponseContext:
            return ResponseContext()

    async def forbidden_readiness() -> None:
        readiness_calls.append("unexpected")
        raise AssertionError("Codex readiness ran before embedding alias validation")

    service = module._create_readiness(
        settings=settings,
        engine=object(),
        redis=object(),
        artifacts=object(),
        embeddings=object(),
        answer_backend=module.TapperAnswerBackend(
            generator=object(),
            readiness=forbidden_readiness,
            owner=None,
        ),
        milvus_reader=object(),
        milvus_target=object(),
        models_probe_client=ModelsClient(),
    )

    assert await service._checks[4]() is False
    assert readiness_calls == []


@pytest.mark.asyncio
async def test_codex_readiness_failure_stays_on_the_closed_models_remediation() -> None:
    module = _runtime()
    settings = module.TapperSettings.from_mapping(
        valid_settings() | {"TAPPER_ANSWER_BACKEND": "codex"}
    )

    class Response:
        status_code = 200
        content = json.dumps(
            {
                "object": "list",
                "data": [
                    {
                        "id": "tapper-embedding",
                        "object": "model",
                        "created": 1,
                        "owned_by": "litellm",
                    }
                ],
            }
        ).encode()

        async def aiter_bytes(self):  # type: ignore[no-untyped-def]
            yield self.content

    class ResponseContext:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class ModelsClient:
        def stream(self, _method: str, _path: str) -> ResponseContext:
            return ResponseContext()

    async def fail_codex() -> None:
        raise RuntimeError("login=/Users/operator/.codex/auth.json provider-secret")

    service = module._create_readiness(
        settings=settings,
        engine=object(),
        redis=object(),
        artifacts=object(),
        embeddings=object(),
        answer_backend=module.TapperAnswerBackend(
            generator=object(),
            readiness=fail_codex,
            owner=None,
        ),
        milvus_reader=object(),
        milvus_target=object(),
        models_probe_client=ModelsClient(),
    )

    result = await service.check()
    models = next(item for item in result.components if item.name.value == "models")
    assert models.state.value == "failed"
    assert models.remediation_code.value == "configure-models"
    assert "provider-secret" not in result.model_dump_json()


def test_legacy_loopback_runtime_requires_explicit_codex_answer_backend() -> None:
    assert (
        LegacyLoopbackSettings.from_mapping({"TAPPER_ANSWER_BACKEND": "codex"}).answer_backend
        == "codex"
    )


def test_legacy_app_has_no_project_api_and_rejects_non_loopback():
    from fastapi.testclient import TestClient

    settings = LegacyLoopbackSettings.from_mapping({"TAPPER_ANSWER_BACKEND": "codex"})
    app = build_legacy_app(settings)
    assert all(not route.path.startswith("/api/v1/") for route in app.routes)
    assert TestClient(app).get("/api/v1/projects/tapper-demo/ai/models").status_code == 404
    with pytest.raises(ValueError):
        LegacyLoopbackSettings.from_mapping(
            {"TAPPER_ANSWER_BACKEND": "codex", "TAPPER_API_HOST": "0.0.0.0"}
        )
    with pytest.raises(ValueError):
        LegacyLoopbackSettings.from_mapping({"TAPPER_ANSWER_BACKEND": "litellm"})


def test_explicit_legacy_command_cannot_select_a_litellm_answer_route(tmp_path):
    executable = tmp_path / "uv"
    executable.write_text(
        "#!/usr/bin/python3\nimport json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], "
        "'backend': os.environ['TAPPER_ANSWER_BACKEND']}))\n"
    )
    executable.chmod(0o700)
    root = Path(__file__).resolve().parents[5]
    result = subprocess.run(
        ["bash", str(root / "scripts/run-tapper-legacy-codex-dev.sh")],
        cwd=root,
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "TAPPER_ANSWER_BACKEND": "litellm"},
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {
        "argv": [
            "run",
            "--project",
            "apps/tap-ai-backend",
            "python",
            "-m",
            "tap.entrypoints.legacy_loopback_answer_runtime",
        ],
        "backend": "codex",
    }


def test_legacy_main_binds_only_loopback_and_never_mounts_project_routes(monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: calls.append((app, options)))
    _runtime().main({"TAPPER_ANSWER_BACKEND": "codex"})
    app, options = calls[0]
    assert options["host"] == "127.0.0.1"
    assert all(not route.path.startswith("/api/v1/") for route in app.routes)
    with pytest.raises(ValueError):
        _runtime().main({"TAPPER_ANSWER_BACKEND": "codex", "TAPPER_API_HOST": "0.0.0.0"})
    assert len(calls) == 1
