from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[4]


def test_tap_exporter_owns_only_its_product_contract(tmp_path: Path) -> None:
    """Writing the AI api.json would collapse the two product boundaries."""
    ai_bytes = b'{"ownedBy":"tap-ai"}\n'
    ai_path = tmp_path / "openapi" / "api.json"
    ai_path.parent.mkdir(parents=True)
    ai_path.write_bytes(ai_bytes)

    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "export_tap_contracts.py"),
            "--output-dir",
            str(tmp_path),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert ai_path.read_bytes() == ai_bytes
    tap_path = tmp_path / "openapi" / "tap-api.json"
    schema = json.loads(tap_path.read_text())
    assert schema["info"]["title"] == "TAP API"
    assert "/api/v1/projects/{project_id}/insights/queries" in schema["paths"]
    digest = hashlib.sha256(tap_path.read_bytes()).hexdigest()

    checked = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "export_tap_contracts.py"),
            "--output-dir",
            str(tmp_path),
            "--check",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert hashlib.sha256(tap_path.read_bytes()).hexdigest() == digest


def test_ai_exporter_check_ignores_tap_owned_contract(tmp_path: Path) -> None:
    """The AI drift check must not claim or delete TAP's independent OpenAPI file."""
    ai_export = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "export_contracts.py"),
            "--output-dir",
            str(tmp_path),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ai_export.returncode == 0, ai_export.stderr
    tap_path = tmp_path / "openapi" / "tap-api.json"
    tap_path.write_text('{"ownedBy":"tap"}\n')

    checked = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "export_contracts.py"),
            "--output-dir",
            str(tmp_path),
            "--check",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert checked.returncode == 0, checked.stderr
    assert tap_path.read_text() == '{"ownedBy":"tap"}\n'
