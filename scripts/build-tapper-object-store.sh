#!/bin/bash
set -euo pipefail
tapper_build_script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 - "$tapper_build_script_dir/.." "$@" <<'PY'
"""Build and attest the worktree-local MinIO image; never publish or start a service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
parser = argparse.ArgumentParser(description="Build or verify the pinned local object-store image")
parser.add_argument("command", choices=("build", "verify", "verify-container"))
parser.add_argument("container", nargs="?")
parser.add_argument("--platform", choices=("linux/arm64", "linux/amd64"))
args = parser.parse_args(sys.argv[2:])
receipt_path = root / ".tapper/object-store-build.json"
recipe = root / "deploy/minio/Dockerfile"
inputs_path = root / "deploy/minio/build-inputs.json"

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require(condition, message):
    if not condition:
        raise ValueError(message)

def run(arguments, *, visible=False):
    result = subprocess.run(arguments, check=False, text=True,
                            stdout=sys.stderr if visible else subprocess.PIPE,
                            stderr=None if visible else subprocess.PIPE)
    require(result.returncode == 0, "object-store Docker operation failed")
    return "" if visible else result.stdout.strip()

def image_record(image_id):
    records = json.loads(run([*docker, "image", "inspect", image_id]))
    require(len(records) == 1, "object-store image is unavailable")
    return records[0]

def binary_digest(image_id):
    container = run([*docker, "create", "--network", "none", "--entrypoint", "/minio", image_id])
    require(re.fullmatch(r"[0-9a-f]{64}", container), "invalid temporary image container")
    try:
        with tempfile.TemporaryDirectory(prefix="tap-object-image-") as directory:
            binary = Path(directory) / "minio"
            run([*docker, "cp", container + ":/minio", str(binary)])
            return digest(binary)
    finally:
        run([*docker, "rm", "-v", container])

def validate_image(receipt, labels):
    image_id = receipt["image_id"]
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id), "invalid object-store image ID")
    actual = image_record(image_id)
    require(actual["Id"] == image_id, "object-store image ID mismatch")
    require(actual["Os"] + "/" + actual["Architecture"] == receipt["platform"], "object-store platform mismatch")
    require(actual["Config"]["User"] == "65532:65532", "object-store image must run as nonroot")
    require(all(actual["Config"].get("Labels", {}).get(key) == value for key, value in labels.items()),
            "object-store image provenance mismatch")
    require(binary_digest(image_id) == receipt["binary_sha256"], "object-store binary digest mismatch")

docker_configuration = None
try:
    if args.command != "build":
        require(receipt_path.is_file() and not receipt_path.is_symlink()
                and not receipt_path.parent.is_symlink(),
                "object-store build receipt is missing or invalid")
        receipt = json.loads(receipt_path.read_text())
    else:
        require(args.platform is not None, "build requires an explicit --platform")
        receipt = {"platform": args.platform}
    inputs = json.loads(inputs_path.read_text())
    platform = receipt["platform"]
    require(platform in inputs["platforms"], "unsupported object-store platform")
    pinned = inputs["platforms"][platform]
    expected = {
        "schema_version": 1,
        "platform": platform,
        "source_url": inputs["source_url"],
        "source_commit": inputs["source_commit"],
        "source_release": inputs["source_release"],
        "builder_index_digest": inputs["builder_index_digest"],
        "runtime_index_digest": inputs["runtime_index_digest"],
        "builder_manifest_digest": pinned["builder_manifest_digest"],
        "runtime_manifest_digest": pinned["runtime_manifest_digest"],
        "dockerfile_sha256": digest(recipe),
        "build_inputs_sha256": digest(inputs_path),
    }
    labels = {"io.tap.minio." + key: str(value) for key, value in expected.items()}
    if args.command != "build":
        require(all(receipt.get(key) == value for key, value in expected.items()),
                "object-store receipt inputs mismatch; rebuild the image")
    require(not os.environ.get("DOCKER_HOST"), "use a local Docker context without DOCKER_HOST")
    context = run(["docker", "context", "show"])
    endpoint = run(["docker", "context", "inspect", context, "--format", '{{(index .Endpoints "docker").Host}}'])
    require(endpoint.startswith("unix://"), "object-store build requires a local Docker socket")
    # The build inputs are public. Avoid unrelated credential-helper sessions;
    # use the socket already resolved from the caller's explicit local context.
    docker_configuration = tempfile.TemporaryDirectory(prefix="tap-object-client-")
    (Path(docker_configuration.name) / "config.json").write_text('{"auths": {}}\n')
    docker = ["docker", "--config", docker_configuration.name, "--host", endpoint]
    if args.command == "build":
        with tempfile.TemporaryDirectory(prefix="tap-object-build-") as directory:
            iidfile = Path(directory) / "image-id"
            command = [*docker, "build", "--platform", platform, "--iidfile", str(iidfile), "--file", str(recipe)]
            build_args = {
                "TARGETARCH": platform.split("/", 1)[1],
                "BUILDER_IMAGE": inputs["builder_repository"] + "@" + pinned["builder_manifest_digest"],
                "RUNTIME_IMAGE": inputs["runtime_repository"] + "@" + pinned["runtime_manifest_digest"],
                "SOURCE_URL": inputs["source_url"], "SOURCE_COMMIT": inputs["source_commit"],
                "SOURCE_RELEASE": inputs["source_release"],
            }
            for key, value in build_args.items():
                command.extend(["--build-arg", key + "=" + value])
            for key, value in labels.items():
                command.extend(["--label", key + "=" + value])
            command.append(str(inputs_path.parent))
            run(command, visible=True)
            image_id = iidfile.read_text().strip()
        receipt = {**expected, "image_id": image_id, "binary_sha256": binary_digest(image_id)}
        validate_image(receipt, labels)
        receipt_path.parent.mkdir(exist_ok=True)
        require(not receipt_path.parent.is_symlink(), "object-store receipt directory must be local")
        with tempfile.NamedTemporaryFile(mode="w", prefix=".object-store-", dir=receipt_path.parent, delete=False) as output:
            json.dump(receipt, output, sort_keys=True, indent=2)
            output.write("\n")
            temporary_receipt = Path(output.name)
        temporary_receipt.replace(receipt_path)
    else:
        validate_image(receipt, labels)
    if args.command == "verify-container":
        require(args.container and re.fullmatch(r"[0-9a-f]{12,64}", args.container), "an exact container ID is required")
        container = json.loads(run([*docker, "container", "inspect", args.container]))
        require(len(container) == 1 and container[0]["Image"] == receipt["image_id"],
                "object-store container image mismatch")
    print(receipt["image_id"])
except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
    message = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else "object-store build receipt is missing or invalid"
    print(message, file=sys.stderr)
    sys.exit(2)
finally:
    if docker_configuration is not None:
        docker_configuration.cleanup()
PY
