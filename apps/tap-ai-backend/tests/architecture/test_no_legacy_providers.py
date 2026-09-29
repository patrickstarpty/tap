from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
# Codex: any dotted module name containing "codex". Azure: dotted names starting with "azure".
FORBIDDEN = re.compile(r"codex|^azure")
_CODEX = re.compile(r"codex")
_AZURE = re.compile(r"^azure")


def _imported_names(tree: ast.AST) -> set[str]:
    """Every imported module name, including function-local and relative imports."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                names.update(alias.name for alias in node.names)
            else:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _violations(pattern: re.Pattern[str]) -> list[str]:
    return sorted(
        f"{path.relative_to(SRC)}: {name}"
        for path in SRC.rglob("*.py")
        for name in _imported_names(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        if FORBIDDEN.search(name) and pattern.search(name)
    )


def test_src_has_no_codex_imports() -> None:
    assert _violations(_CODEX) == []


@pytest.mark.xfail(strict=True, reason="removed in Task 6")
def test_src_has_no_azure_imports() -> None:
    assert _violations(_AZURE) == []


@pytest.mark.parametrize(
    "source",
    [
        "import tap.adapters.legacy_codex\n",
        "from . import codex_exec\n",
        "def build():\n    from tap.modules.knowledge.adapters.codex_target import X\n",
    ],
)
def test_scanner_flags_codex_anywhere_in_the_import_name(source: str) -> None:
    assert any(_CODEX.search(name) for name in _imported_names(ast.parse(source)))


def test_scanner_flags_azure_only_at_the_dotted_name_start() -> None:
    names = _imported_names(ast.parse("import azure.storage.blob\nimport tap.azure_like\n"))
    assert {name for name in names if FORBIDDEN.search(name)} == {"azure.storage.blob"}
