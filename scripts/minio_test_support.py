"""Owned disposable MinIO receipts. No S3 client or ambient service configuration."""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "tapper-test-objects"
PURPOSE = "minio-contract-tests"


class OwnershipError(RuntimeError):
    """Closed diagnostic: never include credentials or Docker provider output."""


def _require(condition: bool) -> None:
    if not condition:
        raise OwnershipError("owned MinIO receipt or resource mismatch")


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OwnershipError("owned MinIO operation failed") from error
    if result.returncode != 0:
        raise OwnershipError("owned MinIO operation failed")
    return result.stdout.strip()


def _local_context() -> str:
    _require(not os.environ.get("DOCKER_HOST"))
    context = _run(["docker", "context", "show"])
    _require(re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", context) is not None)
    endpoint = _run(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ]
    )
    _require(endpoint.startswith("unix://"))
    return context


def _verified_image() -> str:
    image = _run([str(ROOT / "scripts/build-tapper-object-store.sh"), "verify"])
    _require(re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None)
    return image


def _docker(context: str, *args: str) -> str:
    return _run(["docker", "--context", context, *args])


def _inspect(context: str, kind: str, identity: str) -> dict[str, Any]:
    try:
        value = json.loads(_docker(context, kind, "inspect", identity))
        _require(
            isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict)
        )
        return value[0]
    except (ValueError, KeyError, TypeError) as error:
        raise OwnershipError("owned MinIO inspection failed") from error


def _labels(owner: str) -> dict[str, str]:
    return {
        "io.tap.test.owner": owner,
        "io.tap.test.purpose": PURPOSE,
        "io.tap.test.bucket": BUCKET,
    }


def _check_labels(value: object, owner: str) -> None:
    _require(isinstance(value, dict))
    assert isinstance(value, dict)
    _require(
        all(value.get(key) == expected for key, expected in _labels(owner).items())
    )


def _check_volume(context: str, volume: str, owner: str) -> None:
    value = _inspect(context, "volume", volume)
    _require(value.get("Name") == volume and value.get("Driver") == "local")
    _check_labels(value.get("Labels"), owner)


def _check_container(
    context: str, container_id: str, image: str, volume: str, owner: str
) -> dict[str, Any]:
    value = _inspect(context, "container", container_id)
    _require(value.get("Id") == container_id and value.get("Image") == image)
    config = value.get("Config")
    _require(isinstance(config, dict))
    assert isinstance(config, dict)
    _check_labels(config.get("Labels"), owner)
    mounts = value.get("Mounts")
    _require(isinstance(mounts, list) and len(mounts) == 1)
    assert isinstance(mounts, list)
    _require(isinstance(mounts[0], dict))
    _require(mounts[0].get("Type") == "volume" and mounts[0].get("Name") == volume)
    _require(mounts[0].get("Destination") == "/data")
    return value


def _endpoint(value: dict[str, Any]) -> str:
    try:
        ports = value["NetworkSettings"]["Ports"]
        _require(set(ports) == {"9000/tcp"})
        bindings = ports["9000/tcp"]
        _require(isinstance(bindings, list) and len(bindings) == 1)
        binding = bindings[0]
        _require(binding["HostIp"] == "127.0.0.1")
        port = binding["HostPort"]
        _require(
            isinstance(port, str) and port.isdecimal() and 1024 <= int(port) <= 65535
        )
        return f"http://127.0.0.1:{port}"
    except (KeyError, TypeError, ValueError) as error:
        raise OwnershipError("owned MinIO port mismatch") from error


def _wait_ready(context: str, container_id: str) -> None:
    deadline = time.monotonic() + 60
    while True:
        try:
            _docker(context, "container", "exec", container_id, "/minio-health")
            return
        except OwnershipError:
            if time.monotonic() >= deadline:
                raise OwnershipError("owned MinIO readiness failed") from None
            time.sleep(0.2)


@dataclass(frozen=True)
class OwnedMinio:
    receipt_path: Path
    endpoint: str
    bucket: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    container_id: str
    image_id: str
    volume: str
    owner: str
    docker_context: str

    def restart(self) -> None:
        validated = require_owned_minio(self.receipt_path)
        _require(validated == self)
        _docker(self.docker_context, "container", "restart", self.container_id)
        _wait_ready(self.docker_context, self.container_id)
        require_owned_minio(self.receipt_path)


def require_owned_minio(receipt_path: str | Path | None = None) -> OwnedMinio:
    """Validate receipt and actual Docker ownership before callers construct S3 clients."""
    supplied = (
        receipt_path
        if receipt_path is not None
        else os.environ.get("TAP_TEST_MINIO_RECEIPT")
    )
    _require(supplied is not None and str(supplied) != "")
    path = Path(str(supplied)).absolute()
    try:
        # O_NOFOLLOW plus fstat binds permissions and content to the same regular file.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor) as stream:
            metadata = os.fstat(stream.fileno())
            _require(
                stat.S_ISREG(metadata.st_mode)
                and stat.S_IMODE(metadata.st_mode) == 0o600
            )
            _require(metadata.st_uid == os.getuid() and metadata.st_size <= 16384)
            value = json.load(stream)
        keys = {
            "schema_version",
            "endpoint",
            "bucket",
            "access_key_id",
            "secret_access_key",
            "container_id",
            "image_id",
            "volume",
            "owner",
            "docker_context",
        }
        _require(isinstance(value, dict) and set(value) == keys)
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1)
        _require(
            all(
                isinstance(item, str)
                for key, item in value.items()
                if key != "schema_version"
            )
        )
        _require(re.fullmatch(r"[0-9a-f]{32}", value["owner"]) is not None)
        _require(value["volume"] == f"tap-minio-test-{value['owner']}")
        _require(value["bucket"] == BUCKET)
        _require(re.fullmatch(r"[0-9a-f]{64}", value["container_id"]) is not None)
        _require(re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"]) is not None)
        _require(
            re.fullmatch(r"http://127\.0\.0\.1:[0-9]{4,5}", value["endpoint"])
            is not None
        )
        _require(1024 <= int(value["endpoint"].rsplit(":", 1)[1]) <= 65535)
        _require(re.fullmatch(r"[0-9a-f]{32}", value["access_key_id"]) is not None)
        _require(re.fullmatch(r"[0-9a-f]{64}", value["secret_access_key"]) is not None)
        _require(
            re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", value["docker_context"]) is not None
        )
        owned = OwnedMinio(
            receipt_path=path,
            **{key: item for key, item in value.items() if key != "schema_version"},
        )
        _require(_local_context() == owned.docker_context)
        _require(_verified_image() == owned.image_id)
        actual = _check_container(
            owned.docker_context,
            owned.container_id,
            owned.image_id,
            owned.volume,
            owned.owner,
        )
        _check_volume(owned.docker_context, owned.volume, owned.owner)
        _require(actual["State"]["Running"] is True)
        _require(_endpoint(actual) == owned.endpoint)
        environment = actual["Config"]["Env"]
        _require(isinstance(environment, list))
        for name, expected in (
            ("MINIO_ROOT_USER", owned.access_key_id),
            ("MINIO_ROOT_PASSWORD", owned.secret_access_key),
        ):
            _require(
                [item for item in environment if item.startswith(name + "=")]
                == [name + "=" + expected]
            )
        return owned
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
    ) as error:
        raise OwnershipError("owned MinIO receipt or resource mismatch") from error


@contextmanager
def isolated_minio() -> Iterator[OwnedMinio]:
    """Create an owned empty server. Callers may create BUCKET only after receipt validation."""
    context = ""
    owner = uuid.uuid4().hex
    volume = f"tap-minio-test-{owner}"
    container_id = ""
    image = ""
    volume_created = False
    container_removed = False
    volume_removed = False
    try:
        context = _local_context()
        image = _verified_image()
        with tempfile.TemporaryDirectory(prefix="tap-minio-owned-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            access_key = secrets.token_hex(16)
            secret_key = secrets.token_hex(32)
            env_file = root / "server.env"
            env_file.touch(mode=0o600)
            env_file.write_text(
                f"MINIO_ROOT_USER={access_key}\nMINIO_ROOT_PASSWORD={secret_key}\n"
            )
            label_args = [
                part
                for key, value in _labels(owner).items()
                for part in ("--label", f"{key}={value}")
            ]
            _docker(context, "volume", "create", *label_args, volume)
            volume_created = True
            _check_volume(context, volume, owner)
            # Pin the selected random port so Docker restart preserves the receipt.
            # Closing before create admits a race; Docker then fails safely and
            # the ownership-checked finally block removes only this run's resources.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            _require(1024 <= port <= 65535)
            container_id = _docker(
                context,
                "container",
                "create",
                "--name",
                volume,
                "--pull",
                "never",
                *label_args,
                "--env-file",
                str(env_file),
                "--publish",
                f"127.0.0.1:{port}:9000",
                "--mount",
                f"type=volume,source={volume},target=/data",
                image,
                "server",
                "/data",
                "--address",
                ":9000",
            )
            _require(re.fullmatch(r"[0-9a-f]{64}", container_id) is not None)
            _check_container(context, container_id, image, volume, owner)
            _docker(context, "container", "start", container_id)
            actual = _check_container(context, container_id, image, volume, owner)
            owned = OwnedMinio(
                root / "receipt.json",
                _endpoint(actual),
                BUCKET,
                access_key,
                secret_key,
                container_id,
                image,
                volume,
                owner,
                context,
            )
            receipt = asdict(owned)
            del receipt["receipt_path"]
            owned.receipt_path.touch(mode=0o600)
            owned.receipt_path.write_text(json.dumps({"schema_version": 1, **receipt}))
            require_owned_minio(owned.receipt_path)
            _wait_ready(context, container_id)
            yield owned
    finally:
        failed = False
        if container_id:
            try:
                _check_container(context, container_id, image, volume, owner)
                _docker(context, "container", "rm", "--force", container_id)
                container_removed = True
            except OwnershipError:
                failed = True
        if volume_created:
            try:
                _check_volume(context, volume, owner)
                _docker(context, "volume", "rm", volume)
                volume_removed = True
            except OwnershipError:
                failed = True
        print(
            json.dumps(
                {
                    "event": "owned-minio-cleanup",
                    "container_removed": container_removed,
                    "volume_removed": volume_removed,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if failed:
            raise OwnershipError("owned MinIO cleanup refused or failed")
