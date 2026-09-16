"""Exercise the product import guard without importing either product."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
CHECKER = ROOT / "scripts" / "check_backend_boundary.py"
SOURCES = {
    "tap": "apps/backend/src/tap_platform",
    "ai": "apps/tap-ai-backend/src/tap",
}


def run_guard(root: Path, product: str) -> subprocess.CompletedProcess[str]:
    # -I -S excludes PYTHONPATH and all site packages: no product install needed.
    return subprocess.run(
        [sys.executable, "-I", "-S", str(CHECKER), "--root", str(root), "--product", product],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("product", SOURCES)
@pytest.mark.parametrize(
    "template",
    [
        "import {package}.app as foreign",
        "from {package} import app",
        "from {source} import app",
        "from apps import {app}",
        "import importlib as loader; loader.import_module('{package}.app')",
        "from importlib import import_module as load; load('{package}.app')",
        "__import__('{package}.app')",
        "import importlib; importlib.import_module('apps.{app}.src.{package}')",
        "import importlib; importlib.import_module(variable_target)",
    ],
)
def test_guard_rejects_reciprocal_imports(tmp_path: Path, product: str, template: str):
    source = tmp_path / SOURCES[product] / "nested" / "violation.py"
    source.parent.mkdir(parents=True)
    foreign = "ai" if product == "tap" else "tap"
    package = "tap" if foreign == "ai" else "tap_platform"
    app = "tap_ai_backend" if foreign == "ai" else "backend"
    source.write_text(
        template.format(package=package, source=f"apps.{app}.src.{package}", app=app),
        encoding="utf-8",
    )
    result = run_guard(tmp_path, product)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "violation.py:1" in result.stdout
    assert "cross-product import" in result.stdout


@pytest.mark.parametrize("product", SOURCES)
def test_guard_allows_own_imports_without_other_product(tmp_path: Path, product: str):
    source = tmp_path / SOURCES[product] / "app.py"
    source.parent.mkdir(parents=True)
    package = "tap" if product == "ai" else "tap_platform"
    source.write_text(
        f"import {package}.own\nfrom . import own\nraise RuntimeError('never execute source')",
        encoding="utf-8",
    )
    result = run_guard(tmp_path, product)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_ai_product_has_no_tap_imports():
    result = run_guard(ROOT, "ai")
    assert result.returncode == 0, result.stdout + result.stderr


def test_guard_fails_closed_for_missing_product_source(tmp_path: Path):
    result = run_guard(tmp_path, "ai")
    assert result.returncode == 1
    assert "Missing product source" in result.stdout
