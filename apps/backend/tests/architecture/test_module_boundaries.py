"""Static dependency guards for the Knowledge module boundary."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

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


def chat_import_is_forbidden(reference: ImportReference) -> bool:
    """Require Chat to import Knowledge only through private, allowlisted API names."""
    if reference.module == "tap.modules.knowledge.api":
        return not (
            reference.symbol in CHAT_API_SYMBOLS
            and reference.alias is not None
            and reference.alias.startswith("_")
        )
    if reference.module == "tap" and reference.symbol == "modules":
        return True
    if reference.module == "tap.modules":
        return True
    return reference.module == "tap.modules.knowledge" or reference.module.startswith(
        "tap.modules.knowledge."
    )


def policy_import_exposes_construction(reference: ImportReference) -> bool:
    """Identify direct and parent-module paths to the private policy constructor."""
    if reference.module == POLICY_MODULE:
        return reference.symbol in {None, "*", POLICY_FACTORY}
    if reference.module == "tap.modules.access.domain":
        return reference.symbol in {None, "*", "policy"}
    if reference.module == "tap.modules.access":
        return reference.symbol in {None, "*", "domain"}
    if reference.module == "tap.modules":
        return reference.symbol in {None, "*", "access"}
    return reference.module == "tap" and reference.symbol == "modules"


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
        forbidden_imports = {
            reference for reference in _imports(path) if chat_import_is_forbidden(reference)
        }
        assert not forbidden_imports, (path, forbidden_imports)


def test_only_access_authorization_imports_the_private_policy_factory() -> None:
    """The private constructor is a lint boundary, not a caller security capability."""
    authorize = ACCESS / "application" / "authorize.py"
    construction_consumers: set[Path] = set()

    for path in recursive_python_files(BACKEND_SOURCE / "modules"):
        if any(policy_import_exposes_construction(reference) for reference in _imports(path)):
            construction_consumers.add(path)

    assert construction_consumers == {authorize}


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


@pytest.mark.parametrize(
    "reference",
    [
        ImportReference("tap.modules", "knowledge", "_knowledge"),
        ImportReference("tap.modules", "knowledge", "knowledge_alias"),
        ImportReference("tap.modules", None, "_modules"),
        ImportReference("tap", "modules", "_modules"),
        ImportReference("tap.modules.knowledge", None, "_knowledge"),
        ImportReference("tap.modules.knowledge", "api", "_api"),
        ImportReference("tap.modules.knowledge.api", None, "_api"),
        ImportReference("tap.modules.knowledge.api", "KnowledgeAPI", "KnowledgeAPI"),
        ImportReference("tap.modules.knowledge.api", "AuthorizedRetrieval", "_Internal"),
    ],
)
def test_chat_parent_package_and_non_exact_api_imports_are_forbidden(
    reference: ImportReference,
) -> None:
    """Every literal bypass must violate the exact public API import form."""
    assert chat_import_is_forbidden(reference) is True


@pytest.mark.parametrize(
    "reference",
    [
        ImportReference("tap.modules.knowledge.api", "KnowledgeAPI", "_KnowledgeAPI"),
        ImportReference("tap.modules.knowledge.api", "SearchRequest", "_SearchRequest"),
        ImportReference("tap.modules.chat.domain", "Chat", "_Chat"),
    ],
)
def test_chat_exact_private_api_imports_and_unrelated_modules_remain_allowed(
    reference: ImportReference,
) -> None:
    assert chat_import_is_forbidden(reference) is False


@pytest.mark.parametrize(
    "reference",
    [
        ImportReference("tap.modules.access", "domain", "_domain"),
        ImportReference("tap.modules.access", None, "_access"),
        ImportReference("tap.modules", "access", "_access"),
        ImportReference("tap.modules", None, "_modules"),
        ImportReference("tap", "modules", "_modules"),
        ImportReference("tap.modules.access.domain", None, "_domain"),
        ImportReference("tap.modules.access.domain", "policy", "_policy"),
        ImportReference("tap.modules.access.domain.policy", None, "_policy"),
        ImportReference(POLICY_MODULE, POLICY_FACTORY, "_factory"),
    ],
)
def test_policy_parent_package_imports_expose_the_private_construction_path(
    reference: ImportReference,
) -> None:
    assert policy_import_exposes_construction(reference) is True


def test_unrelated_access_import_does_not_expose_policy_construction() -> None:
    reference = ImportReference(
        "tap.modules.access.application.ports",
        "CurrentPolicyVerificationPort",
        "_Verifier",
    )
    assert policy_import_exposes_construction(reference) is False
