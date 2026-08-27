"""Static dependency guards for the Knowledge module boundary."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib.util import resolve_name
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
ACCESS_APPLICATION_MODULE = "tap.modules.access.application"
AUTHORIZER_MODULE = f"{ACCESS_APPLICATION_MODULE}.authorize"
_DYNAMIC_IMPORT_ALIAS = "<dynamic-import>"
_UNRESOLVED_DYNAMIC_IMPORT = "<unresolved-dynamic-import>"
_IMPORT_MODULE_CALL = "import-module"
_BUILTIN_IMPORT_CALL = "builtin-import"
_AMBIGUOUS_IMPORT_CALL = "ambiguous-import"


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
    assignments: list[tuple[str, ast.expr]] = []
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
        elif isinstance(node, ast.Assign):
            assignments.extend(
                (target.id, node.value) for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                assignments.append((node.target.id, node.value))

    changed = True
    while changed:
        changed = False
        for target, value in assignments:
            call_kind = _dynamic_import_kind(
                value,
                importlib_modules=importlib_modules,
                builtins_modules=builtins_modules,
                import_module_functions=import_module_functions,
                builtin_import_functions=builtin_import_functions,
            )
            if call_kind in {_IMPORT_MODULE_CALL, _AMBIGUOUS_IMPORT_CALL}:
                before = len(import_module_functions)
                import_module_functions.add(target)
                changed = changed or len(import_module_functions) != before
            if call_kind in {_BUILTIN_IMPORT_CALL, _AMBIGUOUS_IMPORT_CALL}:
                before = len(builtin_import_functions)
                builtin_import_functions.add(target)
                changed = changed or len(builtin_import_functions) != before

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_kind = _dynamic_import_kind(
            node.func,
            importlib_modules=importlib_modules,
            builtins_modules=builtins_modules,
            import_module_functions=import_module_functions,
            builtin_import_functions=builtin_import_functions,
        )
        if call_kind is None:
            continue
        target = _dynamic_import_target(node, call_kind=call_kind)
        imports.add(
            ImportReference(
                module=target,
                symbol=None,
                alias=_DYNAMIC_IMPORT_ALIAS,
            )
        )
    return imports


def _dynamic_import_kind(
    function: ast.expr,
    *,
    importlib_modules: set[str],
    builtins_modules: set[str],
    import_module_functions: set[str],
    builtin_import_functions: set[str],
) -> str | None:
    if isinstance(function, ast.Name):
        is_import_module = function.id in import_module_functions
        is_builtin_import = function.id in builtin_import_functions
        if is_import_module and is_builtin_import:
            return _AMBIGUOUS_IMPORT_CALL
        if is_import_module:
            return _IMPORT_MODULE_CALL
        if is_builtin_import:
            return _BUILTIN_IMPORT_CALL
        return None
    if not isinstance(function, ast.Attribute) or not isinstance(function.value, ast.Name):
        return None
    if function.attr == "import_module" and function.value.id in importlib_modules:
        return _IMPORT_MODULE_CALL
    if function.attr == "__import__" and function.value.id in builtins_modules:
        return _BUILTIN_IMPORT_CALL
    return None


def _dynamic_import_target(call: ast.Call, *, call_kind: str) -> str:
    target = _literal_call_argument(call, position=0, keyword="name")
    if target is None:
        return _UNRESOLVED_DYNAMIC_IMPORT
    if not target.startswith("."):
        return target
    if call_kind != _IMPORT_MODULE_CALL:
        return _UNRESOLVED_DYNAMIC_IMPORT
    package = _literal_call_argument(call, position=1, keyword="package")
    if package is None:
        return _UNRESOLVED_DYNAMIC_IMPORT
    try:
        return resolve_name(target, package)
    except (ImportError, ValueError):
        return _UNRESOLVED_DYNAMIC_IMPORT


def _literal_call_argument(
    call: ast.Call,
    *,
    position: int,
    keyword: str,
) -> str | None:
    positional = call.args[position] if len(call.args) > position else None
    keywords = [item.value for item in call.keywords if item.arg == keyword]
    if positional is not None and keywords:
        return None
    value = positional if positional is not None else (keywords[0] if len(keywords) == 1 else None)
    if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
        return value.value
    return None


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
    if reference.module == AUTHORIZER_MODULE:
        return (
            reference.symbol in {None, "*"}
            or (isinstance(reference.symbol, str) and reference.symbol.startswith("_"))
            or reference.alias == _DYNAMIC_IMPORT_ALIAS
        )
    if reference.module.startswith(f"{AUTHORIZER_MODULE}."):
        return reference.alias == _DYNAMIC_IMPORT_ALIAS or reference.symbol is None
    if reference.module == ACCESS_APPLICATION_MODULE:
        return reference.symbol in {None, "*", "authorize"}
    if reference.module == "tap.modules.access.domain":
        return reference.symbol in {None, "*", "policy"}
    if reference.module == "tap.modules.access":
        return reference.symbol in {None, "*", "application", "domain"}
    if reference.module == "tap.modules":
        return reference.symbol in {None, "*", "access"}
    return reference.module == "tap" and reference.symbol in {None, "*", "modules"}


def test_framework_free_knowledge_layers_do_not_import_framework_or_provider_sdks() -> None:
    """Moving Pydantic, HTTP, or provider types into a stable layer must fail."""
    forbidden = ("fastapi", "pydantic", "httpx", "azure", "litellm", "pymilvus")
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


def test_stable_knowledge_layers_do_not_import_interface_adapters() -> None:
    """An HTTP upload type in application/ports/domain reverses the dependency direction."""
    stable_roots = (
        KNOWLEDGE / "domain",
        KNOWLEDGE / "application",
        KNOWLEDGE / "ports",
    )

    for root in stable_roots:
        for path in recursive_python_files(root):
            assert not any(
                reference.module == "tap.interfaces"
                or reference.module.startswith("tap.interfaces.")
                for reference in _imports(path)
            ), path


def test_only_milvus_transport_imports_pymilvus() -> None:
    """Importing SDK objects outside transport would leak capabilities across the adapter."""
    transport = KNOWLEDGE / "adapters" / "milvus" / "transport.py"
    consumers = {
        path
        for path in recursive_python_files(BACKEND_SOURCE)
        if any(
            reference.module == "pymilvus" or reference.module.startswith("pymilvus.")
            for reference in _imports(path)
        )
    }

    assert consumers == {transport}


def test_stable_layers_do_not_import_milvus_adapter_modules() -> None:
    """A provider adapter import in domain, application, or contracts would change public DTOs."""
    stable_roots = (
        BACKEND_SOURCE / "contracts",
        KNOWLEDGE / "domain",
        KNOWLEDGE / "application",
        KNOWLEDGE / "ports",
    )
    for root in stable_roots:
        for path in recursive_python_files(root):
            assert not any(
                reference.module == "tap.modules.knowledge.adapters.milvus"
                or reference.module.startswith("tap.modules.knowledge.adapters.milvus.")
                for reference in _imports(path)
            ), path


def test_search_provider_selection_exists_only_in_knowledge_bootstrap() -> None:
    """Reading the selector elsewhere would create multiple composition roots."""
    bootstrap = BACKEND_SOURCE / "entrypoints" / "knowledge_bootstrap.py"
    selector_consumers: set[Path] = set()
    for path in recursive_python_files(BACKEND_SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Constant) and node.value == "TAP_SEARCH_BACKEND"
            for node in ast.walk(tree)
        ):
            selector_consumers.add(path)

    assert selector_consumers == {bootstrap}


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


def test_chat_and_knowledge_register_tables_in_the_shared_platform_metadata() -> None:
    """Extracting Outbox must leave both adapters importable in one metadata registry."""
    from tap.modules.chat.adapters import mysql as chat_mysql
    from tap.modules.knowledge.adapters import mysql_documents
    from tap.platform.db.schema import metadata, outbox

    assert chat_mysql.metadata is metadata
    assert chat_mysql.outbox is outbox
    assert mysql_documents.knowledge_document.metadata is metadata
    assert metadata.tables["outbox"] is outbox


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


@pytest.mark.parametrize(
    "source_text",
    [
        ("import importlib\nload = importlib.import_module\nload('tap.modules.knowledge.api')\n"),
        (
            "import importlib as imports\n"
            "first = imports.import_module\n"
            "load = first\n"
            "load('tap.modules.knowledge.domain.models')\n"
        ),
        (
            "import builtins\n"
            "load = builtins.__import__\n"
            "load('tap.modules.knowledge.ports.search')\n"
        ),
        (
            "import builtins as runtime\n"
            "first = runtime.__import__\n"
            "load = first\n"
            "load('tap.modules.knowledge.application.retrieve')\n"
        ),
        ("import importlib\nload = importlib.import_module\ntarget = get_target()\nload(target)\n"),
        ("import builtins\nload = builtins.__import__\ntarget = get_target()\nload(target)\n"),
    ],
    ids=(
        "assigned-import-module",
        "assigned-import-module-alias-chain",
        "assigned-builtin-import",
        "assigned-builtin-import-alias-chain",
        "assigned-import-module-unresolved",
        "assigned-builtin-import-unresolved",
    ),
)
def test_chat_source_scanner_tracks_assigned_dynamic_import_callables(
    tmp_path: Path,
    source_text: str,
) -> None:
    """Dropping assignment tracking must reopen a callable-alias dependency bypass."""
    references = _parsed_source(tmp_path, source_text)

    assert any(chat_import_is_forbidden(reference) for reference in references), references


@pytest.mark.parametrize(
    ("source_text", "expected_module"),
    [
        (
            "import importlib\n"
            "importlib.import_module('..knowledge.api', package='tap.modules.chat')\n",
            "tap.modules.knowledge.api",
        ),
        (
            "from importlib import import_module as load\n"
            "load('..knowledge.api', 'tap.modules.chat')\n",
            "tap.modules.knowledge.api",
        ),
        (
            "import importlib\n"
            "first = importlib.import_module\n"
            "load = first\n"
            "load('.policy', package='tap.modules.access.domain')\n",
            "tap.modules.access.domain.policy",
        ),
    ],
    ids=("module-keyword-package", "function-positional-package", "assigned-alias-package"),
)
def test_dynamic_import_relative_literals_resolve_to_guarded_absolute_modules(
    tmp_path: Path,
    source_text: str,
    expected_module: str,
) -> None:
    """A relative target must be resolved, not compared as an inert dotted string."""
    references = _parsed_source(tmp_path, source_text)

    assert (
        ImportReference(
            module=expected_module,
            symbol=None,
            alias=_DYNAMIC_IMPORT_ALIAS,
        )
        in references
    )


@pytest.mark.parametrize(
    "source_text",
    [
        ("from importlib import import_module as load\nload('..knowledge.api')\n"),
        (
            "import importlib\n"
            "load = importlib.import_module\n"
            "package = get_package()\n"
            "load('.policy', package=package)\n"
        ),
    ],
    ids=("missing-package", "unresolved-package"),
)
def test_relative_dynamic_import_without_static_package_fails_closed(
    tmp_path: Path,
    source_text: str,
) -> None:
    references = _parsed_source(tmp_path, source_text)

    assert (
        ImportReference(
            module=_UNRESOLVED_DYNAMIC_IMPORT,
            symbol=None,
            alias=_DYNAMIC_IMPORT_ALIAS,
        )
        in references
    )


@pytest.mark.parametrize(
    "source_text",
    [
        (
            "from tap.modules.access.application.authorize import "
            "_new_retrieval_policy_context as factory\n"
        ),
        "import tap.modules.access.application.authorize as authorization\n",
        "from tap.modules.access.application import authorize as authorization\n",
        "import tap.modules.access.application as application\n",
        ("import importlib\nimportlib.import_module('tap.modules.access.application.authorize')\n"),
        ("from importlib import import_module as load\nload('tap.modules.access.application')\n"),
        (
            "import importlib\n"
            "load = importlib.import_module\n"
            "load('.authorize', package='tap.modules.access.application')\n"
        ),
    ],
    ids=(
        "private-factory-reexport",
        "authorizer-module-object",
        "application-parent-symbol",
        "application-parent-module",
        "dynamic-authorizer-module",
        "dynamic-application-parent",
        "assigned-relative-authorizer-module",
    ),
)
def test_policy_scanner_rejects_authorizer_reexport_paths(
    tmp_path: Path,
    source_text: str,
) -> None:
    references = _parsed_source(tmp_path, source_text)

    assert any(policy_import_exposes_construction(reference) for reference in references), (
        references
    )


def test_policy_scanner_allows_the_explicit_public_authorizer_builder(tmp_path: Path) -> None:
    references = _parsed_source(
        tmp_path,
        (
            "from tap.modules.access.application.authorize import "
            "build_retrieval_policy_context as build\n"
        ),
    )

    assert not any(policy_import_exposes_construction(reference) for reference in references), (
        references
    )
