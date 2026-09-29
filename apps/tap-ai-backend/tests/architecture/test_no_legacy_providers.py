from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN = re.compile(r"(^|\.)codex|^azure")


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _violations(prefix: str) -> list[str]:
    return sorted(
        f"{path.relative_to(SRC)}: {module}"
        for path in SRC.rglob("*.py")
        for module in _imported_modules(path)
        if (match := FORBIDDEN.search(module)) and match.group(0).lstrip(".") == prefix
    )


def test_src_has_no_codex_imports() -> None:
    assert _violations("codex") == []


@pytest.mark.xfail(strict=True, reason="removed in Task 6")
def test_src_has_no_azure_imports() -> None:
    assert _violations("azure") == []
