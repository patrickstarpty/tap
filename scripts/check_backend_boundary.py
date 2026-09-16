"""Check static product imports without loading either product package."""

import argparse
import ast
from pathlib import Path

PRODUCTS = {
    "tap": (
        "apps/backend/src/tap_platform",
        ("tap", "apps.tap_ai_backend", "apps.tap-ai-backend"),
    ),
    "ai": ("apps/tap-ai-backend/src/tap", ("tap_platform", "apps.backend")),
}


def check_source(path: Path, forbidden: tuple[str, ...]) -> list[str]:
    """Static dependency lint, not a sandbox for deliberately obfuscated code."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    references: list[tuple[int, str]] = []
    loaders = {"__import__"}
    importlibs = {"importlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                references.append((node.lineno, alias.name))
                if alias.name == "importlib":
                    importlibs.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                references.append((node.lineno, f"{node.module}.{alias.name}"))
                if (node.module, alias.name) in {
                    ("importlib", "import_module"),
                    ("builtins", "__import__"),
                }:
                    loaders.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        dynamic = isinstance(function, ast.Name) and function.id in loaders
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            dynamic |= (
                function.value.id in importlibs and function.attr == "import_module"
            )
        if dynamic:
            target = (
                node.args[0]
                if node.args
                else next((kw.value for kw in node.keywords if kw.arg == "name"), None)
            )
            if not isinstance(target, ast.Constant) or not isinstance(
                target.value, str
            ):
                references.append((node.lineno, "<unresolved dynamic import>"))
            else:
                references.append((node.lineno, target.value))
    return [
        f"{path}:{line}: cross-product import: {module}"
        for line, module in references
        if module == "<unresolved dynamic import>"
        or any(
            module == prefix or module.startswith(prefix + ".") for prefix in forbidden
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--product", choices=("tap", "ai", "all"), default="all")
    args = parser.parse_args()
    violations = []
    for product in PRODUCTS if args.product == "all" else [args.product]:
        relative, forbidden = PRODUCTS[product]
        source = args.root / relative
        files = sorted(source.rglob("*.py"))
        if not files:
            violations.append(f"Missing product source: {source}")
        for path in files:
            violations.extend(check_source(path, forbidden))
    if violations:
        print("\n".join(violations))
        return 1
    print(f"Backend product imports passed ({args.product}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
