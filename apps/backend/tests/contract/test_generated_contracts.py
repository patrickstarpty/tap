"""Behavior checks for the repository's generated public contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
EXPORTER = REPOSITORY_ROOT / "scripts" / "export_contracts.py"
REQUIRED_ENVELOPE_FIELDS = {
    "eventId",
    "sequence",
    "turnId",
    "occurredAt",
    "schemaVersion",
    "event",
}


def export_contracts(output_directory: Path, *, check: bool = False) -> None:
    command = [sys.executable, str(EXPORTER), "--output-dir", str(output_directory)]
    if check:
        command.append("--check")
    subprocess.run(command, check=True, cwd=REPOSITORY_ROOT)


def test_exporter_generates_byte_identical_openapi_with_stable_turn_operation_id(
    tmp_path: Path,
) -> None:
    """A changed route operation ID or non-deterministic JSON must fail this test."""
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    export_contracts(first_output)
    export_contracts(second_output)
    export_contracts(first_output, check=True)
    export_contracts(second_output, check=True)

    first_openapi = (first_output / "openapi" / "api.json").read_bytes()
    second_openapi = (second_output / "openapi" / "api.json").read_bytes()

    assert first_openapi == second_openapi
    assert (
        json.loads(first_openapi)["paths"]["/v1/chats/{chat_id}/turns"]["post"]["operationId"]
        == "chat_create_turn"
    )


def test_exporter_generates_sse_envelope_with_required_recovery_fields(tmp_path: Path) -> None:
    """Removing a required SSE recovery field must fail this public-contract check."""
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    export_contracts(first_output)
    export_contracts(second_output)
    export_contracts(first_output, check=True)
    export_contracts(second_output, check=True)

    first_schema = (first_output / "events" / "chat-stream.schema.json").read_bytes()
    second_schema = (second_output / "events" / "chat-stream.schema.json").read_bytes()

    assert first_schema == second_schema
    envelope = json.loads(first_schema)
    assert REQUIRED_ENVELOPE_FIELDS <= set(envelope["required"])
