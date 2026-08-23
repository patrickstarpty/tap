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


def export_contracts(
    output_directory: Path, *, check: bool = False, require_success: bool = True
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(EXPORTER), "--output-dir", str(output_directory)]
    if check:
        command.append("--check")
    result = subprocess.run(command, cwd=REPOSITORY_ROOT, text=True, capture_output=True)
    if require_success:
        result.check_returncode()
    return result


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


def test_check_mode_rejects_missing_and_stale_artifacts(tmp_path: Path) -> None:
    """A disabled comparison in check mode must fail this regeneration guard."""
    output_directory = tmp_path / "contracts"

    missing = export_contracts(output_directory, check=True, require_success=False)
    assert missing.returncode == 1

    export_contracts(output_directory)
    openapi_path = output_directory / "openapi" / "api.json"
    openapi_path.write_bytes(openapi_path.read_bytes() + b"stale")

    stale = export_contracts(output_directory, check=True, require_success=False)
    assert stale.returncode == 1
    assert "openapi/api.json" in stale.stderr


def test_exporter_emits_closed_retrieval_intent_and_complete_chat_event_union(
    tmp_path: Path,
) -> None:
    """Widening browser DTOs or dropping a baseline event must fail this contract test."""
    output_directory = tmp_path / "contracts"
    export_contracts(output_directory)

    openapi = json.loads((output_directory / "openapi" / "api.json").read_bytes())
    components = openapi["components"]["schemas"]
    request = components["ChatTurnRequest"]
    properties = request["properties"]

    assert components["SourceFamily"]["enum"] == ["doc", "code", "bdd", "failure"]
    assert components["ResourceMode"]["enum"] == ["required", "preferred", "scope"]
    assert (
        properties["sourceScope"]["anyOf"][0]["items"]["$ref"]
        == "#/components/schemas/SourceFamily"
    )
    assert (
        properties["resourceRefs"]["anyOf"][0]["items"]["$ref"]
        == "#/components/schemas/ResourceRef"
    )
    assert components["ResourceRef"]["properties"]["mode"]["default"] == "preferred"
    assert set(components["StructuralAnchor"]["discriminator"]["mapping"]) == {
        "document",
        "code",
        "bdd",
        "openapi",
        "failure",
    }
    assert not {
        "tenantId",
        "projectId",
        "allowedGroupIds",
        "classification",
        "filter",
        "physicalIndex",
    } & set(properties)

    event_schema = json.loads(
        (output_directory / "events" / "chat-stream.schema.json").read_bytes()
    )
    event = event_schema["properties"]["event"]
    assert set(event["discriminator"]["mapping"]) == {
        "turn.started",
        "context.assembled",
        "query.plan_ready",
        "stage.started",
        "stage.completed",
        "retrieval.hits_ready",
        "rerank.completed",
        "answer.delta",
        "citation.resolved",
        "turn.completed",
        "turn.abstained",
        "turn.degraded",
        "turn.canceled",
        "turn.failed",
    }
