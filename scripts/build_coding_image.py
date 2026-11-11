"""Build the task-owned coding image from a verified local OpenCode binary."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

BINARY_SHA256 = "dab85b46f4ea4813da5367997f99a60aab64a2118d58ebf552980e792747436c"
RIPGREP_VERSION = "15.1.0"
RIPGREP_ARCHIVE_SHA256 = (
    "2b661c6ef508e902f388e9098d9c4c5aca72c87b55922d94abdba830b4dc885e"
)
RIPGREP_MEMBER = "ripgrep-15.1.0-aarch64-unknown-linux-gnu/rg"
BROWSER_IMAGE = "tapper-browser-runtime:1.62.1-arm64"
BROWSER_IMAGE_ID = (
    "sha256:7b8133ca22e829241c4d405c4146ae6b000150bf9b1e61bdd462b05c4a812117"
)
CODING_IMAGE = "tapper-coding-runtime:1.19.5-arm64"
FILES = ("model-shim.mjs", "browserctl.mjs", "browser-tools.mjs", "opencode-client.mjs")


def validate_binary(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("OpenCode binary must be a real file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != BINARY_SHA256:
        raise ValueError("OpenCode binary digest mismatch")


def validate_ripgrep_archive(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("ripgrep archive must be a real file")
    if hashlib.sha256(path.read_bytes()).hexdigest() != RIPGREP_ARCHIVE_SHA256:
        raise ValueError("ripgrep archive digest mismatch")
    with tarfile.open(path, "r:gz") as archive:
        member = archive.getmember(RIPGREP_MEMBER)
        if not member.isfile() or not 0 < member.size <= 8000000:
            raise ValueError("ripgrep binary archive member invalid")
        binary = archive.extractfile(member)
        if binary is None:
            raise ValueError("ripgrep binary archive member unavailable")
        return binary.read()


def verify_browser_image_sources(root: Path, image: str) -> None:
    """Reject stale runtime scripts before creating tasks or spending model calls."""
    folder = root / "deploy/browser"
    sources = sorted((*folder.glob("*.mjs"), *folder.glob("*.sh")))
    if not sources:
        raise ValueError("browser source files missing")
    container_name = "tapper-image-check-" + secrets.token_hex(8)
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--entrypoint",
                "sha256sum",
                image,
                *(f"/opt/browser/{path.name}" for path in sources),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        # Killing a docker CLI does not cancel the engine-side container creation.
        # Remove only this probe's resource; never restart the shared engine here.
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise
    actual = {}
    for line in result.stdout.splitlines():
        digest, name = line.split(maxsplit=1)
        actual[name.strip()] = digest
    stale = [
        path.name
        for path in sources
        if actual.get(f"/opt/browser/{path.name}")
        != hashlib.sha256(path.read_bytes()).hexdigest()
    ]
    if stale:
        raise ValueError("browser image source mismatch: " + ", ".join(stale))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--opencode-binary", type=Path, required=True)
    parser.add_argument("--ripgrep-archive", type=Path, required=True)
    args = parser.parse_args()
    validate_binary(args.opencode_binary)
    ripgrep_binary = validate_ripgrep_archive(args.ripgrep_archive)
    root = Path(__file__).resolve().parents[1]
    inspected = subprocess.run(
        ["docker", "image", "inspect", BROWSER_IMAGE, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    if inspected.stdout.strip() != BROWSER_IMAGE_ID:
        raise ValueError("browser base image digest mismatch")
    verify_browser_image_sources(root, inspected.stdout.strip())
    with tempfile.TemporaryDirectory(prefix="tapper-coding-build-") as temporary:
        context = Path(temporary)
        shutil.copyfile(args.opencode_binary, context / "opencode")
        (context / "rg").write_bytes(ripgrep_binary)
        shutil.copyfile(
            root / "deploy/browser/Dockerfile.coding", context / "Dockerfile"
        )
        for name in FILES:
            shutil.copyfile(root / "deploy/browser" / name, context / name)
        subprocess.run(
            [
                "docker",
                "build",
                "--platform",
                "linux/arm64",
                "-t",
                CODING_IMAGE,
                str(context),
            ],
            check=True,
        )
    image_id = subprocess.run(
        ["docker", "image", "inspect", CODING_IMAGE, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    print(
        json.dumps(
            {
                "image": CODING_IMAGE,
                "image_id": image_id,
                "base_image_id": BROWSER_IMAGE_ID,
                "opencode_binary_sha256": BINARY_SHA256,
                "ripgrep_version": RIPGREP_VERSION,
                "ripgrep_archive_sha256": RIPGREP_ARCHIVE_SHA256,
            }
        )
    )


if __name__ == "__main__":
    main()
