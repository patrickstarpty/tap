"""Local image receipts must bind startup to the built immutable MinIO artifact."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
IMAGE = "sha256:" + "a" * 64
BINARY = b"fixed-minio-binary"


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "deploy/minio").mkdir(parents=True)
    for name in (
        "scripts/build-tapper-object-store.sh",
        "deploy/minio/Dockerfile",
        "deploy/minio/build-inputs.json",
    ):
        source = ROOT / name
        if source.exists():
            shutil.copyfile(source, root / name)
    stubs = root / "bin"
    stubs.mkdir()
    docker = stubs / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        + """import hashlib, json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ["TEST_BUILD_ROOT"])
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\\n")
image = "sha256:" + "a" * 64
if args[:2] == ["context", "show"]:
    print("default")
elif args[:2] == ["context", "inspect"]:
    print("unix:///tmp/owned-test-docker.sock")
elif args[:1] == ["--config"]:
    assert json.loads((pathlib.Path(args[1]) / "config.json").read_text()) == {"auths": {}}
    assert args[2:4] == ["--host", "unix:///tmp/owned-test-docker.sock"]
    args = args[4:]
    if args[:1] == ["build"]:
        labels = {}
        for offset, value in enumerate(args):
            if value == "--label":
                key, text = args[offset + 1].split("=", 1)
                labels[key] = text
        (root / "labels.json").write_text(json.dumps(labels))
        pathlib.Path(args[args.index("--iidfile") + 1]).write_text(image)
    elif args[:2] == ["image", "inspect"]:
        labels = json.loads((root / "labels.json").read_text())
        print(json.dumps([{"Id": os.environ.get("TEST_IMAGE_ID", image), "Os": "linux",
            "Architecture": os.environ.get("TEST_IMAGE_ARCH", "arm64"),
            "Config": {"User": "65532:65532", "Labels": labels}}]))
    elif args[:1] == ["create"]:
        print("b" * 64)
    elif args[:1] == ["cp"]:
        pathlib.Path(args[2]).write_bytes(b"fixed-minio-binary")
    elif args[:1] == ["rm"]:
        pass
    elif args[:2] == ["container", "inspect"]:
        print(json.dumps([{"Image": os.environ.get("TEST_CONTAINER_IMAGE", image)}]))
    else:
        sys.exit(91)
else:
    sys.exit(92)
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith("DOCKER_")}
    env.update(PATH=f"{stubs}:{env['PATH']}", TEST_BUILD_ROOT=str(root))
    return root, env


def _run(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(root / "scripts/build-tapper-object-store.sh"), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_receipt_fails_before_docker(tmp_path: Path) -> None:
    root, env = _fixture(tmp_path)
    result = _run(root, env, "verify")
    assert result.returncode == 2
    assert "object-store build receipt is missing or invalid" in result.stderr
    assert not (root / "calls.jsonl").exists()


def test_build_records_real_artifact_and_verify_prints_only_immutable_id(tmp_path: Path) -> None:
    root, env = _fixture(tmp_path)
    result = _run(root, env, "build", "--platform", "linux/arm64")
    assert result.returncode == 0, result.stderr
    receipt = json.loads((root / ".tapper/object-store-build.json").read_text())
    assert receipt["image_id"] == IMAGE
    assert receipt["binary_sha256"] == hashlib.sha256(BINARY).hexdigest()
    assert receipt["source_commit"] == "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a"
    assert receipt["platform"] == "linux/arm64"
    assert (
        receipt["dockerfile_sha256"]
        == hashlib.sha256((root / "deploy/minio/Dockerfile").read_bytes()).hexdigest()
    )
    verified = _run(root, env, "verify")
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout == IMAGE + "\n"
    calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
    build = next(call for call in calls if "build" in call)
    assert build[build.index("--platform") + 1] == "linux/arm64"
    assert "TARGETARCH=arm64" in build
    assert not any("push" in call or "pull" in call for call in calls)


@pytest.mark.parametrize(
    "change", ["dockerfile", "inputs", "image", "platform", "binary", "container", "receipt-parent"]
)
def test_receipt_rejects_changed_input_or_artifact_before_startup(
    tmp_path: Path, change: str
) -> None:
    root, env = _fixture(tmp_path)
    built = _run(root, env, "build", "--platform", "linux/arm64")
    assert built.returncode == 0, built.stderr
    command = ["verify"]
    if change in {"dockerfile", "inputs"}:
        path = root / (
            "deploy/minio/Dockerfile"
            if change == "dockerfile"
            else "deploy/minio/build-inputs.json"
        )
        path.write_text(path.read_text() + "\n")
    elif change == "image":
        env["TEST_IMAGE_ID"] = "sha256:" + "c" * 64
    elif change == "platform":
        env["TEST_IMAGE_ARCH"] = "amd64"
    elif change == "receipt-parent":
        original = root / ".tapper"
        outside = root.parent / "external-receipt"
        original.rename(outside)
        original.symlink_to(outside, target_is_directory=True)
    elif change == "binary":
        path = root / ".tapper/object-store-build.json"
        receipt = json.loads(path.read_text())
        receipt["binary_sha256"] = "c" * 64
        path.write_text(json.dumps(receipt))
    else:
        env["TEST_CONTAINER_IMAGE"] = "sha256:" + "c" * 64
        command = ["verify-container", "d" * 64]
    rejected = _run(root, env, *command)
    assert rejected.returncode == 2
    assert rejected.stdout == ""
