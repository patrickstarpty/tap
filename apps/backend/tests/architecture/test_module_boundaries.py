"""Static dependency guards for the Knowledge module boundary."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

BACKEND_SOURCE = Path(__file__).resolve().parents[2] / "src" / "tap"
KNOWLEDGE = BACKEND_SOURCE / "modules" / "knowledge"
ACCESS = BACKEND_SOURCE / "modules" / "access"
CHAT_API_SYMBOLS = {
    "AnswerRequest",
    "AnswerResponse",
    "KnowledgeAPI",
    "RetrievalPolicyContext",
    "SearchRequest",
    "SearchResponse",
}
POLICY_MODULE = "tap.modules.access.domain.policy"
POLICY_FACTORY = "_new_retrieval_policy_context"


@dataclass(frozen=True, slots=True)
class ImportReference:
    module: str
    symbol: str | None
    alias: str | None


def parsed_imports(path: Path, *, package: str) -> set[ImportReference]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[ImportReference] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                ImportReference(module=alias.name, symbol=None, alias=alias.asname)
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = _canonical_import_from(node, package=package)
            imports.update(
                ImportReference(module=module, symbol=alias.name, alias=alias.asname)
                for alias in node.names
            )
    return imports


def recursive_python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in root.rglob("*.py") if path.is_file()))


def _package_for(path: Path) -> str:
    relative = path.relative_to(BACKEND_SOURCE).with_suffix("")
    parts = ("tap", *relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    else:
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: Path) -> set[ImportReference]:
    return parsed_imports(path, package=_package_for(path))


def _canonical_import_from(node: ast.ImportFrom, *, package: str) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = package.split(".")
    remove = node.level - 1
    if remove >= len(package_parts):
        return ""
    base = package_parts[: len(package_parts) - remove]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def test_framework_free_knowledge_layers_do_not_import_framework_or_provider_sdks() -> None:
    """Moving Pydantic, HTTP, or provider types into a stable layer must fail."""
    forbidden = ("fastapi", "pydantic", "httpx", "azure", "litellm")
    layer_roots = (
        KNOWLEDGE / "domain",
        KNOWLEDGE / "application",
        KNOWLEDGE / "ports",
        ACCESS / "domain",
        ACCESS / "application",
    )
    layer_files = tuple(path for root in layer_roots for path in recursive_python_files(root))

    assert layer_files
    for path in layer_files:
        assert not any(
            reference.module == prefix or reference.module.startswith(f"{prefix}.")
            for reference in _imports(path)
            for prefix in forbidden
        ), path


def test_knowledge_adapters_never_import_chat_and_chat_uses_only_knowledge_api() -> None:
    """A direct Chat↔Knowledge-internals dependency must fail this boundary check."""
    adapter_files = recursive_python_files(KNOWLEDGE / "adapters")
    assert adapter_files
    for path in adapter_files:
        assert not any(
            reference.module == "tap.modules.chat"
            or reference.module.startswith("tap.modules.chat.")
            for reference in _imports(path)
        ), path

    chat_files = recursive_python_files(BACKEND_SOURCE / "modules" / "chat")
    for path in chat_files:
        knowledge_imports = {
            reference
            for reference in _imports(path)
            if reference.module == "tap.modules.knowledge"
            or reference.module.startswith("tap.modules.knowledge.")
        }
        forbidden_imports = {
            reference
            for reference in knowledge_imports
            if reference.module != "tap.modules.knowledge.api"
            or reference.symbol not in CHAT_API_SYMBOLS
            or reference.alias is None
            or not reference.alias.startswith("_")
        }
        assert not forbidden_imports, (path, forbidden_imports)


def test_only_access_authorization_imports_the_private_policy_factory() -> None:
    """The private constructor is a lint boundary, not a caller security capability."""
    authorize = ACCESS / "application" / "authorize.py"
    direct_consumers: set[Path] = set()
    module_object_consumers: set[Path] = set()

    for path in recursive_python_files(BACKEND_SOURCE / "modules"):
        for reference in _imports(path):
            if reference.module == POLICY_MODULE and reference.symbol == POLICY_FACTORY:
                direct_consumers.add(path)
            if (reference.module == POLICY_MODULE and reference.symbol in {None, "*"}) or (
                reference.module == "tap.modules.access.domain" and reference.symbol == "policy"
            ):
                module_object_consumers.add(path)

    assert direct_consumers == {authorize}
    assert not (module_object_consumers - {authorize})


def test_import_parser_canonicalizes_relative_imports_and_preserves_symbols(
    tmp_path: Path,
) -> None:
    source = tmp_path / "consumer.py"
    source.write_text(
        "from ..domain.policy import RetrievalPolicyContext as _Policy\n"
        "from tap.modules.knowledge.api import KnowledgeAPI as _KnowledgeAPI\n",
        encoding="utf-8",
    )

    assert parsed_imports(
        source,
        package="tap.modules.knowledge.application.nested",
    ) == {
        ImportReference(
            module="tap.modules.knowledge.application.domain.policy",
            symbol="RetrievalPolicyContext",
            alias="_Policy",
        ),
        ImportReference(
            module="tap.modules.knowledge.api",
            symbol="KnowledgeAPI",
            alias="_KnowledgeAPI",
        ),
    }


def test_python_layer_discovery_is_recursive(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "deeper" / "boundary.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "top.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert recursive_python_files(tmp_path) == (
        nested,
        tmp_path / "top.py",
    )
