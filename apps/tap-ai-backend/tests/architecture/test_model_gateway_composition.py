from __future__ import annotations

from pathlib import Path

from tap.entrypoints.tapper_runtime import TapperSettings


def test_v1_knowledge_models_share_one_gateway() -> None:
    from tap.entrypoints.tapper_runtime import _create_embeddings
    from tap.modules.ai.ports.gateway import ModelGateway
    from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway

    models = _create_embeddings(
        TapperSettings.from_mapping({"LITELLM_MODEL": "dashscope/qwen-plus"})
    )
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
