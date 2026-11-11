"""Immutable user-selected bytes stay inside the authorized LCA project/owner."""

import base64
import hashlib

import pytest

from tap_platform.lca.store import LcaConflict, LcaNotFound, LcaStore
from tap_platform.lca.service import LcaService
from test_lca_service import actor, services as _services

services = _services


def asset_actor(**changes):
    return actor(
        actions=actor().actions | {"lca.assets.create", "lca.assets.read"}, **changes
    )


def upload(service, key="asset", data=b"selected file"):
    return service.create_asset(
        asset_actor(), key, "report.txt", base64.b64encode(data).decode()
    )


def test_asset_persists_with_opaque_metadata_and_idempotency(services):
    service, browser = services
    asset = upload(service)
    assert asset["sha256"] == hashlib.sha256(b"selected file").hexdigest()
    assert asset["size_bytes"] == 13
    assert upload(service)["id"] == asset["id"]
    assert "content_base64" not in asset and "object_ref" not in asset
    restarted = LcaService(LcaStore(service.store.engine), browser)
    assert restarted.list_assets(asset_actor()) == [asset]
    with pytest.raises(LcaConflict):
        upload(restarted, data=b"changed")


def test_asset_is_not_visible_to_another_project_or_owner(services):
    service, _ = services
    upload(service)
    assert service.list_assets(asset_actor(project_id="other")) == []
    assert service.list_assets(asset_actor(principal_id="bob")) == []
    with pytest.raises(PermissionError):
        service.list_assets(actor())


@pytest.mark.parametrize(
    "data",
    ["not-base64", "", base64.b64encode(b"x" * 256001).decode()],
    ids=["invalid", "empty", "oversized"],
)
def test_invalid_asset_bytes_are_rejected(services, data):
    with pytest.raises(ValueError):
        services[0].create_asset(asset_actor(), "bad", "report.txt", data)


def test_filename_is_sanitized_not_a_path(services):
    asset = services[0].create_asset(
        asset_actor(), "path", "../../secret\\report\r\n.txt", "YQ=="
    )
    assert asset["filename"] == "report.txt"


def test_upload_references_are_checked_before_snapshot_and_bytes_frozen(services):
    service, browser = services
    asset = upload(service)
    definition = {
        "schema_version": "lca-debug-v1",
        "allowed_origins": ["https://example.com"],
        "parameters": [],
        "targets": [{"id": "file", "kind": "test_id", "value": "file"}],
        "steps": [
            {
                "id": "upload",
                "operation": "upload_file",
                "timeout_ms": 1000,
                "target_id": "file",
                "arguments": {"asset_id": asset["id"], "sha256": asset["sha256"]},
            }
        ],
    }
    for outsider in (
        asset_actor(principal_id="bob"),
        asset_actor(project_id="other-project"),
    ):
        with pytest.raises(LcaNotFound):
            service.create_flow(outsider, "other", "Other", definition)
    flow = service.create_flow(asset_actor(), "flow", "Upload", definition)
    run = service.debug_run(asset_actor(), flow["id"], 1, {}, "run")
    bundle, inputs = browser.frozen_bundle("project-a", run["bundle_id"])
    assert (
        inputs["__lca_assets"][asset["id"]]["content_base64"]
        == base64.b64encode(b"selected file").decode()
    )
    assert bundle.input_sha256 == run["input_digest"]
    assert (
        asset["id"].encode()
        in service.store.get_snapshot("project-a", "alice", run["snapshot_id"])[
            "canonical_bytes"
        ]
    )
    definition["steps"][0]["arguments"]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        service.save_draft(asset_actor(), flow["id"], 1, definition)


def test_frozen_input_total_limit_fails_before_a_run_is_published(services):
    service, browser = services
    assets = [upload(service, f"asset-{i}", bytes([65 + i]) * 256000) for i in range(3)]
    definition = {
        "schema_version": "lca-debug-v1",
        "allowed_origins": ["https://example.com"],
        "parameters": [],
        "targets": [{"id": "file", "kind": "test_id", "value": "file"}],
        "steps": [
            {
                "id": f"upload_{i}",
                "operation": "upload_file",
                "target_id": "file",
                "timeout_ms": 1000,
                "arguments": {"asset_id": asset["id"], "sha256": asset["sha256"]},
            }
            for i, asset in enumerate(assets)
        ],
    }
    with pytest.raises(ValueError, match="Frozen input exceeds limit"):
        service.create_flow(asset_actor(), "too-large", "Too large", definition)
    assert service.list_flows(asset_actor()) == []
    from test_lca_service import count

    assert count(browser, "bundles") == 0
    assert count(browser, "executions") == 0


def test_tampered_or_missing_asset_is_not_dispatched(services):
    service, browser = services
    asset = upload(service)
    row = service.store.get_asset("project-a", "alice", asset["id"])
    (browser.objects.root / row["object_ref"]).write_bytes(b"tampered")
    raw = {
        "schema_version": "lca-debug-v1",
        "allowed_origins": ["https://example.com"],
        "parameters": [],
        "targets": [{"id": "file", "kind": "test_id", "value": "file"}],
        "steps": [
            {
                "id": "upload",
                "operation": "upload_file",
                "timeout_ms": 1000,
                "target_id": "file",
                "arguments": {"asset_id": asset["id"], "sha256": asset["sha256"]},
            }
        ],
    }
    with pytest.raises(ValueError, match="hash mismatch"):
        service.create_flow(asset_actor(), "tampered", "Tampered", raw)


@pytest.mark.parametrize(
    "filename,mime",
    [
        ("photo.PNG", "image/png"),
        ("rows.csv", "text/csv"),
        ("test.txt", "text/plain"),
        ("unknown.xyz", "application/octet-stream"),
    ],
)
def test_asset_content_type_is_frozen_browser_metadata(services, filename, mime):
    row = services[0].create_asset(asset_actor(), "mime", filename, "YQ==")
    assert row["content_type"] == mime
