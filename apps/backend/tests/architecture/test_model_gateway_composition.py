from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tap.entrypoints.tapper_runtime import TapperSettings


def test_default_runtime_fails_closed_for_the_retired_codex_answer_selector() -> None:
    with pytest.raises(ValueError, match="TAPPER_ANSWER_BACKEND=codex is unavailable"):
        TapperSettings.from_mapping({"TAPPER_ANSWER_BACKEND": "codex"})


@pytest.mark.parametrize("entrypoint", ["tapper_runtime", "tapper_api", "tapper_ingestion_worker"])
def test_v1_import_graph_never_loads_direct_codex(entrypoint: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import tap.entrypoints.{entrypoint}; import sys; "
            "assert not any('codex_exec' in name or 'codex_target' in name or "
            "'legacy_loopback' in name or 'legacy_litellm' in name for name in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_v1_knowledge_models_share_one_gateway() -> None:
    from tap.entrypoints.tapper_runtime import _create_embeddings
    from tap.modules.ai.ports.gateway import ModelGateway
    from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway

    models = _create_embeddings(TapperSettings.from_mapping({}))
    assert isinstance(models, KnowledgeModelGateway)
    assert isinstance(models.gateway, ModelGateway)


def test_v1_composition_has_no_narrow_answer_provider_slot() -> None:
    import inspect

    from tap.entrypoints import tapper_runtime

    assert "answers" not in inspect.signature(tapper_runtime._assemble_http_services).parameters
    assert not hasattr(tapper_runtime, "AnswerGenerationPort")


def test_default_api_has_no_deprecated_knowledge_or_catalog_aliases() -> None:
    from tap.entrypoints.tapper_api import app, build_runtime_app

    for application in (app, build_runtime_app(TapperSettings.from_mapping({}))):
        paths = {getattr(route, "path", "") for route in application.routes}
        assert "/api/v1/projects/{project_id}/ai/models" in paths
        assert not any(
            path.startswith(("/v1/knowledge", "/v1/citations", "/v1/ai")) for path in paths
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("factory_name", ["create_api_runtime", "create_worker_runtime"])
async def test_runtime_factories_reject_retired_selection_before_resources(
    factory_name, monkeypatch
):
    from dataclasses import replace

    from tap.entrypoints import tapper_runtime

    settings = replace(TapperSettings.from_mapping({}), answer_backend="codex")

    async def forbidden(_settings):
        pytest.fail("retired selection reached a runtime resource")

    monkeypatch.setattr(tapper_runtime, "_create_database", forbidden)
    with pytest.raises(ValueError, match="governed model gateway"):
        await getattr(tapper_runtime, factory_name)(settings)


def test_v1_modules_cannot_import_provider_clients() -> None:
    import ast

    root = Path(__file__).resolve().parents[2] / "src/tap/modules"
    forbidden = {"httpx", "openai", "litellm", "subprocess"}
    for name in ("graph", "test_generation", "automation"):
        for path in (root / name).rglob("*.py"):
            imports = [
                node
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, (ast.Import, ast.ImportFrom))
            ]
            for node in imports:
                modules = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [x.name for x in node.names]
                )
                assert all(
                    module.split(".")[0] not in forbidden and "codex" not in module
                    for module in modules
                ), path


def test_default_command_rejects_codex_before_any_child(tmp_path):
    import os
    import shutil

    root = Path(__file__).resolve().parents[4]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "run-tapper-dev.sh"
    shutil.copy(root / "scripts/run-tapper-dev.sh", script)
    executable = tmp_path / "uv"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    result = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "TAPPER_SUPERVISOR_ENV": "preloaded",
            "TAPPER_ANSWER_BACKEND": "codex",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stderr == "Tapper V1 requires the governed model gateway.\n"
