"""Behavior checks for the repository's generated public contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tap.contracts.http import RetrievalHit

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
EXPORTER = REPOSITORY_ROOT / "scripts" / "export_contracts.py"
REFERENCE_CONTRACTS = REPOSITORY_ROOT / "docs" / "reference" / "2026-08-20-contracts.md"
REQUIRED_ENVELOPE_FIELDS = {
    "eventId",
    "sequence",
    "turnId",
    "occurredAt",
    "schemaVersion",
    "event",
}


def test_public_retrieval_hit_omits_provider_physical_target_everywhere() -> None:
    """Reintroducing a provider target in either public contract must fail."""
    properties = RetrievalHit.model_json_schema(by_alias=True)["properties"]
    assert "physicalIndex" not in properties
    assert "physicalCollection" not in properties

    reference = REFERENCE_CONTRACTS.read_text(encoding="utf-8")
    marker = "interface RetrievalResponse {"
    next_marker = "interface RetrievalAnswerResponse {"
    assert marker in reference
    documented_response = reference.split(marker, maxsplit=1)[1].split(next_marker, maxsplit=1)[0]
    assert "physicalIndex" not in documented_response
    assert "physicalCollection" not in documented_response


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
    paths = json.loads(first_openapi)["paths"]
    assert (
        paths["/api/v1/projects/{project_id}/knowledge/documents"]["post"]["operationId"]
        == "knowledge_upload_document"
    )
    assert (
        paths["/api/v1/projects/{project_id}/knowledge/answers"]["post"]["operationId"]
        == "knowledge_create_answer"
    )
    assert (
        paths["/api/v1/projects/{project_id}/knowledge/citations/{citation_id}"]["get"][
            "operationId"
        ]
        == "citation_get_preview"
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


def test_exporter_emits_private_events_and_problem_registry_without_public_leak(
    tmp_path: Path,
) -> None:
    export_contracts(tmp_path)
    internal = json.loads((tmp_path / "events/project-event.schema.json").read_bytes())
    problems = json.loads((tmp_path / "problem-types.json").read_bytes())
    types = {variant["properties"]["event_type"]["const"] for variant in internal["oneOf"]}
    assert len(types) == 22
    assert "conversation.turn.requested" in types
    assert any(
        problem["type"] == "https://tap.example/problems/scope-mismatch"
        for problem in problems["problems"]
    )
    for path in ("openapi/api.json", "events/chat-stream.schema.json"):
        content = (tmp_path / path).read_text()
        assert "ProjectEventEnvelope" not in content
        assert "inputSnapshotDigest" not in content
        assert "extractionProfileDigest" not in content


def test_check_detects_extra_owned_artifacts_and_preserves_unowned_files(tmp_path: Path) -> None:
    export_contracts(tmp_path)
    unowned = tmp_path / "events/notes.md"
    unowned.write_text("keep me")
    extra = tmp_path / "events/obsolete.schema.json"
    extra.write_text("{}")
    result = export_contracts(tmp_path, check=True, require_success=False)
    assert result.returncode == 1
    assert "events/obsolete.schema.json" in result.stderr
    export_contracts(tmp_path)
    assert extra.exists()  # Never silently delete an unexpected artifact.
    assert unowned.read_text() == "keep me"


def test_exported_problem_component_matches_runtime_and_all_refs_resolve(tmp_path: Path) -> None:
    from tap.interfaces.http.app import create_app

    export_contracts(tmp_path)
    schema = json.loads((tmp_path / "openapi/api.json").read_bytes())
    runtime = create_app().openapi()
    assert (
        schema["components"]["schemas"]["ProblemDetails"]
        == runtime["components"]["schemas"]["ProblemDetails"]
    )

    def check_refs(value: object) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/components/schemas/")
                assert (
                    reference.removeprefix("#/components/schemas/")
                    in schema["components"]["schemas"]
                )
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)

    check_refs(schema)
    assert "ProjectEventEnvelope" not in schema["components"]["schemas"]
