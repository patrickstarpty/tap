"""Build the task-owned coding image from a verified local OpenCode binary."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

BINARY_SHA256 = "dab85b46f4ea4813da5367997f99a60aab64a2118d58ebf552980e792747436c"
BROWSER_IMAGE = "tapper-browser-runtime:1.62.1-arm64"
BROWSER_IMAGE_ID = (
    "sha256:cd0fa3b71dbced58e621d08a98fd3d9ee0f749a51c1c0ba73d5cb2bd273857d5"
)
CODING_IMAGE = "tapper-coding-runtime:1.19.5-arm64"
FILES = ("model-shim.mjs", "browserctl.mjs", "opencode-client.mjs")


def validate_binary(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("OpenCode binary must be a real file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != BINARY_SHA256:
        raise ValueError("OpenCode binary digest mismatch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--opencode-binary", type=Path, required=True)
    args = parser.parse_args()
    validate_binary(args.opencode_binary)
    root = Path(__file__).resolve().parents[1]
    inspected = subprocess.run(
        ["docker", "image", "inspect", BROWSER_IMAGE, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    if inspected.stdout.strip() != BROWSER_IMAGE_ID:
        raise ValueError("browser base image digest mismatch")
    with tempfile.TemporaryDirectory(prefix="tapper-coding-build-") as temporary:
        context = Path(temporary)
        shutil.copyfile(args.opencode_binary, context / "opencode")
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
            }
        )
    )


if __name__ == "__main__":
    main()
