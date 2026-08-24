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
_DYNAMIC_IMPORT_ALIAS = "<dynamic-import>"
_UNRESOLVED_DYNAMIC_IMPORT = "<unresolved-dynamic-import>"


@dataclass(frozen=True, slots=True)
class ImportReference:
    module: str
    symbol: str | None
    alias: str | None


def parsed_imports(path: Path, *, package: str) -> set[ImportReference]:
    """Return static and recognizable dynamic imports for dependency linting.

    This is a conservative architecture guard, not a Python security capability. An
    unresolved target passed to a known dynamic-import facility is therefore emitted
    as a sentinel so guarded boundary checks can fail closed.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[ImportReference] = set()
    importlib_modules = {"importlib"}
    builtins_modules = {"builtins"}
    import_module_functions: set[str] = set()
    builtin_import_functions = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                ImportReference(module=alias.name, symbol=None, alias=alias.asname)
                for alias in node.names
            )
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                if alias.name == "importlib":
                    importlib_modules.add(local_name)
                elif alias.name == "builtins":
                    builtins_modules.add(local_name)
        elif isinstance(node, ast.ImportFrom):
            module = _canonical_import_from(node, package=package)
            imports.update(
                ImportReference(module=module, symbol=alias.name, alias=alias.asname)
                for alias in node.names
            )
            for alias in node.names:
                local_name = alias.asname or alias.name
                if module == "importlib" and alias.name == "import_module":
                    import_module_functions.add(local_name)
                elif module == "builtins" and alias.name == "__import__":
                    builtin_import_functions.add(local_name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_dynamic_import_call(
            node.func,
            importlib_modules=importlib_modules,
            builtins_modules=builtins_modules,
            import_module_functions=import_module_functions,
            builtin_import_functions=builtin_import_functions,
        ):
            continue
        target = (
            node.args[0].value
            if node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value
            else _UNRESOLVED_DYNAMIC_IMPORT
        )
        imports.add(
            ImportReference(
                module=target,
                symbol=None,
                alias=_DYNAMIC_IMPORT_ALIAS,
            )
        )
    return imports


def _is_dynamic_import_call(
    function: ast.expr,
    *,
    importlib_modules: set[str],
    builtins_modules: set[str],
    import_module_functions: set[str],
    builtin_import_functions: set[str],
) -> bool:
    if isinstance(function, ast.Name):
        return function.id in import_module_functions or function.id in builtin_import_functions
    return (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and (
            (function.attr == "import_module" and function.value.id in importlib_modules)
            or (function.attr == "__import__" and function.value.id in builtins_modules)
        )
    )


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
    if reference.module == _UNRESOLVED_DYNAMIC_IMPORT:
        return True
    if reference.module == "tap.modules.knowledge.api":
        return not (
            reference.symbol in CHAT_API_SYMBOLS
            and reference.alias is not None
            and reference.alias.startswith("_")
        )
    if reference.module == "tap" and reference.symbol in {None, "*", "modules"}:
        return True
    if reference.module == "tap.modules":
        return True
    return reference.module == "tap.modules.knowledge" or reference.module.startswith(
        "tap.modules.knowledge."
    )


def policy_import_exposes_construction(reference: ImportReference) -> bool:
    """Identify direct and parent-module paths to the private policy constructor."""
    if reference.module == _UNRESOLVED_DYNAMIC_IMPORT:
        return True
    if reference.module == POLICY_MODULE:
        return (
            reference.symbol in {None, "*", POLICY_FACTORY}
            or (isinstance(reference.symbol, str) and reference.symbol.startswith("_"))
            or reference.alias == _DYNAMIC_IMPORT_ALIAS
        )
    if reference.module.startswith(f"{POLICY_MODULE}."):
        return reference.alias == _DYNAMIC_IMPORT_ALIAS or reference.symbol is None
    if reference.module == "tap.modules.access.domain":
        return reference.symbol in {None, "*", "policy"}
    if reference.module == "tap.modules.access":
        return reference.symbol in {None, "*", "domain"}
    if reference.module == "tap.modules":
        return reference.symbol in {None, "*", "access"}
    return reference.module == "tap" and reference.symbol in {None, "*", "modules"}


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


def _parsed_source(tmp_path: Path, source_text: str) -> set[ImportReference]:
    source = tmp_path / "mutation.py"
    source.write_text(source_text, encoding="utf-8")
    return parsed_imports(source, package="tap.modules.chat.mutation")


@pytest.mark.parametrize(
    "source_text",
    [
        "import tap as _tap\n",
        "from tap import *\n",
        "import importlib\nimportlib.import_module('tap.modules.knowledge')\n",
        "import importlib as loader\nloader.import_module('tap.modules.knowledge.api')\n",
        (
            "from importlib import import_module\n"
            "import_module('tap.modules.knowledge.application.retrieve')\n"
        ),
        (
            "from importlib import import_module as load\n"
            "load('tap.modules.knowledge.domain.models')\n"
        ),
        "__import__('tap.modules.knowledge.api')\n",
        "import builtins as pybuiltins\npybuiltins.__import__('tap.modules.knowledge')\n",
        ("from builtins import __import__ as load\nload('tap.modules.knowledge.ports.search')\n"),
        "import importlib\ntarget = get_target()\nimportlib.import_module(target)\n",
    ],
    ids=(
        "root-module",
        "root-star",
        "importlib-module",
        "aliased-importlib-module",
        "import-module-function",
        "aliased-import-module-function",
        "builtin-import",
        "aliased-builtins-import",
        "aliased-builtin-function",
        "unresolved-dynamic-target",
    ),
)
def test_chat_source_scanner_rejects_root_star_and_dynamic_knowledge_bypasses(
    tmp_path: Path,
    source_text: str,
) -> None:
    """Removing any AST path above must let a real Chat source bypass the API boundary."""
    references = _parsed_source(tmp_path, source_text)

    assert any(chat_import_is_forbidden(reference) for reference in references), references


@pytest.mark.parametrize(
    "source_text",
    [
        "import tap as _tap\n",
        "from tap import *\n",
        (
            "from tap.modules.access.domain.policy import "
            "_CONSTRUCTION_TOKEN as _token, RetrievalPolicyContext as _Context\n"
        ),
        "import importlib\nimportlib.import_module('tap.modules.access.domain.policy')\n",
        "import importlib as loader\nloader.import_module('tap.modules.access.domain')\n",
        (
            "from importlib import import_module\n"
            "import_module('tap.modules.access.domain.policy')\n"
        ),
        ("from importlib import import_module as load\nload('tap.modules.access.domain.policy')\n"),
        "__import__('tap.modules.access.domain.policy')\n",
        (
            "import builtins as pybuiltins\n"
            "pybuiltins.__import__('tap.modules.access.domain.policy')\n"
        ),
        ("from builtins import __import__ as load\nload('tap.modules.access.domain.policy')\n"),
        "import importlib\ntarget = get_target()\nimportlib.import_module(target)\n",
    ],
    ids=(
        "root-module",
        "root-star",
        "private-construction-token",
        "importlib-module",
        "aliased-importlib-parent-module",
        "import-module-function",
        "aliased-import-module-function",
        "builtin-import",
        "aliased-builtins-import",
        "aliased-builtin-function",
        "unresolved-dynamic-target",
    ),
)
def test_policy_source_scanner_rejects_root_star_private_and_dynamic_bypasses(
    tmp_path: Path,
    source_text: str,
) -> None:
    """Only the authorizer may retain a source-level path to guarded construction."""
    references = _parsed_source(tmp_path, source_text)

    assert any(policy_import_exposes_construction(reference) for reference in references), (
        references
    )
