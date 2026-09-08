"""New mixed-provider test cannot construct Azure without an actual owned server."""

import json

import pytest
from scripts import azurite_test_support as support

OWNER = "a" * 32
CONTAINER = "b" * 64
IMAGE = "sha256:" + "c" * 64
PROJECT = "tap-task5-tests-123456789abc"


@pytest.fixture
def actual(monkeypatch):
    monkeypatch.delenv("TAP_TEST_AZURITE_RECEIPT", raising=False)
    monkeypatch.setattr(support, "_local_context", lambda: "desktop-linux")
    value = {
        "Id": CONTAINER,
        "Image": IMAGE,
        "State": {"Running": True},
        "Config": {
            "Image": support.IMAGE,
            "Labels": {
                "com.docker.compose.project": PROJECT,
                "com.docker.compose.service": "azurite",
                "io.tap.test.owner": OWNER,
                "io.tap.test.purpose": support.PURPOSE,
            },
            "Env": [],
            "Cmd": [
                "azurite-blob",
                "--blobHost",
                "0.0.0.0",
                "--blobPort",
                "32987",
                "--silent",
                "--location",
                "/data",
            ],
        },
        "Mounts": [],
        "HostConfig": {"Tmpfs": {"/data": ""}, "Binds": None},
        "NetworkSettings": {"Ports": {"32987/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32987"}]}},
    }

    def inspect(context, kind, identity):
        assert context == "desktop-linux"
        if kind == "image":
            assert identity == support.IMAGE
            return {"Id": IMAGE}
        assert (kind, identity) == ("container", CONTAINER)
        return value

    monkeypatch.setattr(support, "_inspect", inspect)
    return value


def receipt(tmp_path):
    return support.write_owned_azurite_receipt(
        tmp_path / "azure.json",
        project=PROJECT,
        owner=OWNER,
        container_id=CONTAINER,
        docker_context="desktop-linux",
    )


def test_azure_raw_configuration_without_receipt_rejected_before_client(actual, monkeypatch):
    monkeypatch.setenv("TAP_RUN_AZURITE_INTEGRATION", "1")
    monkeypatch.setenv("AZURITE_CONNECTION_STRING", "shared-account-secret")
    monkeypatch.setattr(support, "_inspect", lambda *args: pytest.fail("no Docker before receipt"))
    with pytest.raises(support.OwnershipError):
        support.require_owned_azurite()


def test_private_receipt_derives_endpoint_and_ignores_ambient_connection(
    actual, monkeypatch, tmp_path
):
    path = receipt(tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv("AZURITE_CONNECTION_STRING", "shared-account-secret")
    owned = support.require_owned_azurite(path)
    assert owned.endpoint == "http://127.0.0.1:32987/devstoreaccount1"
    assert "shared-account" not in owned.connection_string
    assert "AccountName=devstoreaccount1" in owned.connection_string


@pytest.mark.parametrize(
    "change",
    ["image", "owner", "project", "service", "account", "port", "host", "mount", "stopped"],
)
def test_actual_resource_mismatch_fails_closed(actual, tmp_path, change):
    path = receipt(tmp_path)
    if change == "image":
        actual["Image"] = "sha256:" + "d" * 64
    elif change in {"owner", "project", "service"}:
        key = "io.tap.test.owner" if change == "owner" else "com.docker.compose." + change
        actual["Config"]["Labels"][key] = "default"
    elif change == "account":
        actual["Config"]["Env"] = ["AZURITE_ACCOUNTS=shared:key"]
    elif change == "port":
        actual["NetworkSettings"]["Ports"]["32987/tcp"][0]["HostPort"] = "10000"
    elif change == "host":
        actual["NetworkSettings"]["Ports"]["32987/tcp"][0]["HostIp"] = "0.0.0.0"
    elif change == "mount":
        actual["Mounts"] = [{"Type": "bind", "Source": "/shared", "Destination": "/data"}]
    else:
        actual["State"]["Running"] = False
    with pytest.raises(support.OwnershipError):
        support.require_owned_azurite(path)


@pytest.mark.parametrize(
    "change", ["mode", "symlink", "extra-field", "forged-endpoint", "forged-account"]
)
def test_bad_receipt_rejected(actual, tmp_path, change):
    path = receipt(tmp_path)
    if change == "mode":
        path.chmod(0o644)
    elif change == "symlink":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        path = link
    else:
        value = json.loads(path.read_text())
        value[
            {"extra-field": "extra", "forged-endpoint": "endpoint", "forged-account": "account"}[
                change
            ]
        ] = "shared"
        path.write_text(json.dumps(value))
    with pytest.raises(support.OwnershipError):
        support.require_owned_azurite(path)


@pytest.mark.asyncio
async def test_mixed_test_rejects_before_azure_constructor(actual, monkeypatch):
    from apps.backend.tests.integration.test_minio_artifacts import (
        test_owned_mixed_azure_recovery_and_minio_artifacts_preserve_legacy_refs as mixed,
    )

    from tap.modules.knowledge.adapters import blob_artifacts

    monkeypatch.setenv("TAP_RUN_AZURITE_INTEGRATION", "1")
    monkeypatch.setenv("AZURITE_CONNECTION_STRING", "shared-account-secret")
    monkeypatch.setattr(
        blob_artifacts,
        "AzureBlobArtifactStore",
        lambda *a, **k: pytest.fail("Azure constructed before ownership"),
    )
    with pytest.raises(support.OwnershipError):
        await mixed(None)
