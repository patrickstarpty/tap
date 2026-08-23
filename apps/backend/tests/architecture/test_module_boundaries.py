"""Static dependency guards for the Knowledge module boundary."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_SOURCE = Path(__file__).resolve().parents[2] / "src" / "tap"
KNOWLEDGE = BACKEND_SOURCE / "modules" / "knowledge"
ACCESS = BACKEND_SOURCE / "modules" / "access"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_framework_free_knowledge_layers_do_not_import_framework_or_provider_sdks() -> None:
    """Moving Pydantic, HTTP, or provider types into a stable layer must fail."""
    forbidden = ("fastapi", "pydantic", "httpx", "azure", "litellm")
    layer_files = [KNOWLEDGE / "domain" / "models.py"]
    layer_files.extend((KNOWLEDGE / "application").glob("*.py"))
    layer_files.extend((KNOWLEDGE / "ports").glob("*.py"))
    layer_files.extend((ACCESS / "domain").glob("*.py"))
    layer_files.extend((ACCESS / "application").glob("*.py"))

    assert layer_files
    for path in layer_files:
        assert path.is_file(), f"missing intended Knowledge layer: {path}"
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imported_modules(path)
            for prefix in forbidden
        ), path


def test_knowledge_adapters_never_import_chat_and_chat_uses_only_knowledge_api() -> None:
    """A direct Chat↔Knowledge-internals dependency must fail this boundary check."""
    adapter_files = list((KNOWLEDGE / "adapters").glob("*.py"))
    assert adapter_files
    for path in adapter_files:
        assert not any(
            module == "tap.modules.chat" or module.startswith("tap.modules.chat.")
            for module in imported_modules(path)
        ), path

    chat_files = list((BACKEND_SOURCE / "modules" / "chat").rglob("*.py"))
    for path in chat_files:
        forbidden_imports = {
            module
            for module in imported_modules(path)
            if module.startswith("tap.modules.knowledge.") and module != "tap.modules.knowledge.api"
        }
        assert not forbidden_imports, (path, forbidden_imports)
