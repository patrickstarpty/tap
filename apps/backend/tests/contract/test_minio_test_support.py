"""Ownership boundaries use Docker command doubles; never start a service."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts.minio_test_support import OwnershipError, isolated_minio, require_owned_minio

IMAGE = "sha256:" + "a" * 64
CONTAINER = "b" * 64


class DockerDouble:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.container: dict[str, Any] = {}
        self.volume: dict[str, Any] = {}
        self.fail_start = False

    def run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(args)
        if args[-1] == "verify":
            return subprocess.CompletedProcess(args, 0, IMAGE + "\n", "")
        cmd = args[1:] if args[1] == "context" else args[3:]
        output = ""
        if cmd == ["context", "show"]:
            output = "desktop-linux"
        elif cmd[:2] == ["context", "inspect"]:
            output = "unix:///tmp/docker.sock"
        elif cmd[:2] == ["volume", "create"]:
            labels = dict(args[i + 1].split("=", 1) for i, v in enumerate(args) if v == "--label")
            self.volume = {"Name": args[-1], "Labels": labels, "Driver": "local"}
            output = args[-1]
        elif cmd[:2] == ["container", "create"]:
            labels = dict(args[i + 1].split("=", 1) for i, v in enumerate(args) if v == "--label")
            env_file = Path(args[args.index("--env-file") + 1])
            self.container = {
                "Id": CONTAINER,
                "Image": IMAGE,
                "Config": {"Labels": labels, "Env": env_file.read_text().splitlines()},
                "State": {"Running": False},
                "Mounts": [{"Type": "volume", "Name": self.volume["Name"], "Destination": "/data"}],
                "NetworkSettings": {
                    "Ports": {"9000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32987"}]}
                },
            }
            output = CONTAINER
        elif cmd[:2] == ["container", "start"]:
            if self.fail_start:
                return subprocess.CompletedProcess(
                    args, 1, "", "private credential provider failure"
                )
            self.container["State"]["Running"] = True
        elif cmd[:2] == ["container", "inspect"]:
            output = json.dumps([self.container])
        elif cmd[:2] == ["volume", "inspect"]:
            output = json.dumps([self.volume])
        elif cmd[:2] == ["container", "restart"]:
            assert cmd[-1] == CONTAINER
        elif cmd[:2] in (["container", "rm"], ["volume", "rm"]):
            pass
        elif cmd[:2] == ["container", "exec"]:
            assert cmd[-1] == "/minio-health"
        else:
            raise AssertionError(f"unexpected command: {cmd[:2]}")
        assert kwargs.get("timeout", 0) > 0
        return subprocess.CompletedProcess(args, 0, output, "")


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> DockerDouble:
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("TAP_TEST_MINIO_RECEIPT", raising=False)
    result = DockerDouble()
    monkeypatch.setattr(subprocess, "run", result.run)

    class PortReservation:
        def __enter__(self) -> PortReservation:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def bind(self, address: tuple[str, int]) -> None:
            assert address == ("127.0.0.1", 0)

        def getsockname(self) -> tuple[str, int]:
            return ("127.0.0.1", 32987)

    monkeypatch.setattr(socket, "socket", lambda *_args: PortReservation())
    return result


def test_missing_receipt_rejects_before_docker(docker: DockerDouble) -> None:
    with pytest.raises(OwnershipError):
        require_owned_minio()
    assert docker.commands == []


def test_owned_lifecycle_uses_verified_image_private_receipt_and_exact_cleanup(
    docker: DockerDouble, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with isolated_minio() as owned:
        assert owned.receipt_path.stat().st_mode & 0o777 == 0o600
        assert owned.receipt_path.parent.stat().st_mode & 0o777 == 0o700
        assert owned.endpoint == "http://127.0.0.1:32987"
        assert owned.bucket == "tapper-test-objects"
        assert owned.container_id == CONTAINER
        monkeypatch.setenv("TAP_TEST_MINIO_RECEIPT", str(owned.receipt_path))
        monkeypatch.setenv("TAP_TEST_MINIO_ENDPOINT", "https://shared.invalid")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "shared-secret")
        assert require_owned_minio().access_key_id == owned.access_key_id
        receipt_before_restart = owned.receipt_path.read_bytes()
        owned.restart()
        assert owned.receipt_path.read_bytes() == receipt_before_restart
        assert require_owned_minio(owned.receipt_path).endpoint == "http://127.0.0.1:32987"
        create = next(cmd for cmd in docker.commands if "create" in cmd and "container" in cmd)
        assert IMAGE in create
        assert create[create.index("--publish") + 1] == "127.0.0.1:32987:9000"
    assert not owned.receipt_path.exists()
    removals = [cmd for cmd in docker.commands if "rm" in cmd]
    assert len(removals) == 2
    assert removals[0][-1] == CONTAINER
    assert removals[1][-1] == owned.volume
    assert '"container_removed": true' in capsys.readouterr().out


@pytest.mark.parametrize(
    "change", ["image", "owner", "port", "host", "mount", "credentials", "stopped", "volume-owner"]
)
def test_inspect_mismatch_rejected_before_restart(docker: DockerDouble, change: str) -> None:
    with isolated_minio() as owned:
        original = json.loads(json.dumps(docker.container))
        original_volume = json.loads(json.dumps(docker.volume))
        if change == "image":
            docker.container["Image"] = "sha256:" + "c" * 64
        elif change == "owner":
            docker.container["Config"]["Labels"]["io.tap.test.owner"] = "forged"
        elif change == "port":
            docker.container["NetworkSettings"]["Ports"]["9000/tcp"][0]["HostPort"] = "9000"
        elif change == "host":
            docker.container["NetworkSettings"]["Ports"]["9000/tcp"][0]["HostIp"] = "0.0.0.0"
        elif change == "mount":
            docker.container["Mounts"][0]["Name"] = "default-minio"
        elif change == "credentials":
            docker.container["Config"]["Env"] = ["MINIO_ROOT_USER=shared"]
        elif change == "stopped":
            docker.container["State"]["Running"] = False
        else:
            docker.volume["Labels"]["io.tap.test.owner"] = "forged"
        before = len(docker.commands)
        with pytest.raises(OwnershipError):
            owned.restart()
        assert not any("restart" in cmd for cmd in docker.commands[before:])
        docker.container = original
        docker.volume = original_volume


@pytest.mark.parametrize(
    "change", ["mode", "symlink", "unknown", "schema", "bucket", "endpoint", "owner", "credential"]
)
def test_invalid_receipt_rejected_before_docker(
    docker: DockerDouble, change: str, tmp_path: Path
) -> None:
    with isolated_minio() as owned:
        path = tmp_path / "receipt.json"
        value = json.loads(owned.receipt_path.read_text())
        if change == "unknown":
            value["extra"] = "secret"
        elif change == "schema":
            value["schema_version"] = 2
        elif change == "bucket":
            value["bucket"] = "shared"
        elif change == "endpoint":
            value["endpoint"] = "http://localhost:9000"
        elif change == "owner":
            value["owner"] = "shared"
        elif change == "credential":
            value["secret_access_key"] = "short"
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        if change == "mode":
            path.chmod(0o644)
        if change == "symlink":
            path.unlink()
            path.symlink_to(owned.receipt_path)
        before = len(docker.commands)
        with pytest.raises(OwnershipError):
            require_owned_minio(path)
        assert len(docker.commands) == before


def test_start_failure_cleans_only_owned_resources_and_redacts_error(
    docker: DockerDouble, capsys: pytest.CaptureFixture[str]
) -> None:
    docker.fail_start = True
    with pytest.raises(OwnershipError, match="operation failed") as error:
        with isolated_minio():
            pytest.fail("failed server must not yield")
    assert "private" not in str(error.value)
    assert len([cmd for cmd in docker.commands if "rm" in cmd]) == 2
    assert '"volume_removed": true' in capsys.readouterr().out


def test_cleanup_refuses_relabelled_container_and_volume_and_emits_fact(
    docker: DockerDouble, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(OwnershipError):
        with isolated_minio():
            docker.container["Config"]["Labels"]["io.tap.test.owner"] = "other"
            docker.volume["Labels"]["io.tap.test.owner"] = "other"
    assert not any("rm" in cmd for cmd in docker.commands)
    output = capsys.readouterr().out
    assert '"container_removed": false' in output
    assert '"volume_removed": false' in output


def test_remote_docker_environment_is_rejected_without_commands(
    docker: DockerDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.invalid")
    with pytest.raises(OwnershipError):
        with isolated_minio():
            pytest.fail("remote Docker must not yield")
    assert docker.commands == []


def test_malformed_inspect_refuses_cleanup_and_still_emits_fact(
    docker: DockerDouble, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(OwnershipError):
        with isolated_minio():
            docker.container["Mounts"] = ["malformed"]
    assert not any("container" in cmd and "rm" in cmd for cmd in docker.commands)
    assert '"container_removed": false' in capsys.readouterr().out


def test_unverified_image_cannot_create_resources(
    docker: DockerDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = docker.run

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[-1] == "verify":
            return subprocess.CompletedProcess(args, 1, "", "untrusted-image")
        return original(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(OwnershipError):
        with isolated_minio():
            pytest.fail("unverified image must not yield")
    assert not any("create" in cmd for cmd in docker.commands)
