"""Receipt verification for the new owned mixed-provider test, never ambient Azure."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.minio_test_support import OwnershipError, _inspect, _local_context

IMAGE = "mcr.microsoft.com/azure-storage/azurite:3.35.0"
PURPOSE = "azurite-compat-contract-tests"
_ACCOUNT = "devstoreaccount1"
_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="


def _require(condition: bool) -> None:
    if not condition:
        raise OwnershipError("owned Azurite receipt or resource mismatch")


@dataclass(frozen=True)
class OwnedAzurite:
    endpoint: str

    @property
    def connection_string(self) -> str:
        return (
            f"DefaultEndpointsProtocol=http;AccountName={_ACCOUNT};AccountKey={_KEY};"
            f"BlobEndpoint={self.endpoint};"
        )


def _actual(
    *, project: str, owner: str, container_id: str, docker_context: str
) -> dict[str, Any]:
    _require(re.fullmatch(r"tap-task5-tests-[0-9a-f]{12}", project) is not None)
    _require(re.fullmatch(r"[0-9a-f]{32}", owner) is not None)
    _require(re.fullmatch(r"[0-9a-f]{64}", container_id) is not None)
    _require(re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", docker_context) is not None)
    _require(_local_context() == docker_context)
    value = _inspect(docker_context, "container", container_id)
    _require(value["Id"] == container_id and value["State"]["Running"] is True)
    config = value["Config"]
    _require(config["Image"] == IMAGE)
    image = _inspect(docker_context, "image", IMAGE)["Id"]
    _require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None
        and value["Image"] == image
    )
    for key, expected in {
        "com.docker.compose.project": project,
        "com.docker.compose.service": "azurite",
        "io.tap.test.owner": owner,
        "io.tap.test.purpose": PURPOSE,
    }.items():
        _require(config["Labels"].get(key) == expected)
    # The only allowed account is Azurite's built-in development account.
    # Any configured account override invalidates the receipt.
    _require(isinstance(config["Env"], list))
    _require(not any(item.startswith("AZURITE_ACCOUNTS=") for item in config["Env"]))
    _require(value["Mounts"] == [])
    host = value["HostConfig"]
    _require(host.get("Tmpfs") == {"/data": ""})
    _require(host.get("Binds") in (None, []) and host.get("Mounts") in (None, []))
    command = config["Cmd"]
    _require(isinstance(command, list) and len(command) == 8)
    port = command[4]
    _require(isinstance(port, str) and port.isdecimal() and 1024 <= int(port) <= 65535)
    _require(
        command
        == [
            "azurite-blob",
            "--blobHost",
            "0.0.0.0",
            "--blobPort",
            port,
            "--silent",
            "--location",
            "/data",
        ]
    )
    ports = value["NetworkSettings"]["Ports"]
    # The image may declare unbound standard ports; no other host binding is allowed.
    bound = {key: item for key, item in ports.items() if item is not None}
    _require(bound == {port + "/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]})
    return {
        "schema_version": 1,
        "project": project,
        "owner": owner,
        "container_id": container_id,
        "docker_context": docker_context,
        "image_id": image,
        "endpoint": f"http://127.0.0.1:{port}/{_ACCOUNT}",
        "account": _ACCOUNT,
    }


def write_owned_azurite_receipt(
    path: Path, *, project: str, owner: str, container_id: str, docker_context: str
) -> Path:
    """Owned wrapper calls this after creation; derive, never accept endpoint/account."""
    value = _actual(
        project=project,
        owner=owner,
        container_id=container_id,
        docker_context=docker_context,
    )
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream)
    require_owned_azurite(path)
    return path


def require_owned_azurite(receipt_path: str | Path | None = None) -> OwnedAzurite:
    """Reject before Azure client construction; ignores raw connection-string variables."""
    supplied = (
        receipt_path
        if receipt_path is not None
        else os.environ.get("TAP_TEST_AZURITE_RECEIPT")
    )
    _require(supplied is not None and str(supplied) != "")
    try:
        path = Path(str(supplied)).absolute()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor) as stream:
            metadata = os.fstat(stream.fileno())
            _require(
                stat.S_ISREG(metadata.st_mode)
                and stat.S_IMODE(metadata.st_mode) == 0o600
            )
            _require(metadata.st_uid == os.getuid() and metadata.st_size <= 16384)
            value = json.load(stream)
        _require(
            isinstance(value, dict)
            and set(value)
            == {
                "schema_version",
                "project",
                "owner",
                "container_id",
                "docker_context",
                "image_id",
                "endpoint",
                "account",
            }
        )
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1)
        _require(
            all(
                isinstance(item, str)
                for key, item in value.items()
                if key != "schema_version"
            )
        )
        actual = _actual(
            **{
                key: value[key]
                for key in ("project", "owner", "container_id", "docker_context")
            }
        )
        _require(value == actual)
        return OwnedAzurite(actual["endpoint"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        raise OwnershipError("owned Azurite receipt or resource mismatch") from None
