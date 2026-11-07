"""Build and record the pinned linux/arm64 Tapper browser image."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "deploy/browser"


def main() -> None:
    inputs = json.loads((CONTEXT / "build-inputs.json").read_text())
    lock = json.loads((CONTEXT / "package-lock.json").read_text())
    assert (
        lock["packages"][""]["dependencies"]["@playwright/test"] == inputs["playwright"]
    )
    tag = "tapper-browser-runtime:1.62.1-arm64"
    subprocess.run(
        ["docker", "build", "--platform", inputs["platform"], "-t", tag, str(CONTEXT)],
        check=True,
    )
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"], text=True
    ).strip()
    report = {
        "image": tag,
        "image_id": image_id,
        "platform": inputs["platform"],
        "base_manifest_digest": inputs["base_manifest_digest"],
        "package_lock_sha256": hashlib.sha256(
            (CONTEXT / "package-lock.json").read_bytes()
        ).hexdigest(),
        "npm_integrity": lock["packages"]["node_modules/@playwright/test"]["integrity"],
        "browser": inputs["browser"],
        "chromium_revision": inputs["chromium_revision"],
        "chromium_version": inputs["chromium_version"],
        "playwright": inputs["playwright"],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
