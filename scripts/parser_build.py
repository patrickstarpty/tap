"""Explicit parser context and lock-derived wheels; never install the Backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(argv: list[str], *, timeout: int = 60) -> str:
    result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise ValueError(
            "parser image operation failed: "
            + result.stderr.decode(errors="replace")[-2000:]
        )
    return result.stdout.decode().strip()


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "verify"])
    parser.add_argument("--platform", choices=["linux/arm64", "linux/amd64"])
    parser.add_argument(
        "--receipt", type=Path, default=root / ".tapper/parser-build.json"
    )
    args = parser.parse_args(sys.argv[2:])
    if args.command == "verify" and not args.receipt.is_file():
        raise ValueError("parser build receipt is missing")
    receipt = json.loads(args.receipt.read_text()) if args.command == "verify" else {}
    platform = args.platform or receipt.get("platform")
    inputs_path = root / "deploy/parser/build-inputs.json"
    inputs = json.loads(inputs_path.read_text())
    if platform not in inputs["platforms"]:
        raise ValueError("parser build requires explicit supported platform")
    if (root / ".python-version").read_text().strip() != inputs["python_version"]:
        raise ValueError("parser Python pin mismatch")
    lock = tomllib.loads((root / "uv.lock").read_text())
    selected = []
    for name in inputs["packages"]:
        packages = [p for p in lock["package"] if p["name"] == name]
        if len(packages) != 1:
            raise ValueError("parser lock package mismatch")
        package = packages[0]
        if any(
            dep["name"] not in inputs["packages"]
            for dep in package.get("dependencies", [])
        ):
            raise ValueError("parser dependency closure changed")
        wheels = [
            w
            for w in package["wheels"]
            if "none-any.whl" in w["url"]
            or ("cp313-cp313-" + inputs["platforms"][platform]["wheel_tag"]) in w["url"]
        ]
        if len(wheels) != 1:
            raise ValueError("parser wheel selection is ambiguous")
        selected.append({"name": name, "version": package["version"], **wheels[0]})
    source_names = inputs["sources"] + [
        "deploy/parser/worker.py",
        "deploy/parser/Dockerfile",
        "deploy/parser/build-inputs.json",
        "scripts/build-tapper-parser.sh",
        "scripts/parser_build.py",
        "uv.lock",
        "apps/backend/pyproject.toml",
        ".python-version",
    ]
    hashes = {}
    for name in source_names:
        path = root / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root)
            or not path.is_file()
        ):
            raise ValueError("parser source allowlist violation")
        hashes[name] = sha(path.read_bytes())
    expected = {
        "schema_version": 1,
        "platform": platform,
        "base_manifest": inputs["platforms"][platform]["manifest_digest"],
        "base_index": inputs["index_digest"],
        "source_hashes": hashes,
        "wheels": selected,
    }
    if args.command == "verify" and any(
        receipt.get(k) != v for k, v in expected.items()
    ):
        raise ValueError("parser build receipt is stale; rebuild required")
    if os.getenv("DOCKER_HOST"):
        raise ValueError("parser requires local Docker context")
    context = run(["docker", "context", "show"])
    endpoint = run(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ]
    )
    if not endpoint.startswith("unix://"):
        raise ValueError("parser requires local Docker context")
    with tempfile.TemporaryDirectory(prefix="tap-parser-build-") as directory:
        work = Path(directory)
        config = work / "docker"
        config.mkdir()
        (config / "config.json").write_text('{"auths":{}}')
        docker = ["docker", "--config", str(config), "--host", endpoint]
        if args.command == "build":
            build = work / "context"
            build.mkdir()
            for name in inputs["sources"]:
                dest = build / "src" / Path(name).relative_to("apps/backend/src")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, dest)
            shutil.copyfile(root / "deploy/parser/worker.py", build / "worker.py")
            shutil.copyfile(root / "deploy/parser/Dockerfile", build / "Dockerfile")
            wheelhouse = build / "wheelhouse"
            wheelhouse.mkdir()
            requirements = []
            for wheel in selected:
                url = wheel["url"]
                if not url.startswith("https://files.pythonhosted.org/packages/"):
                    raise ValueError("parser wheel source rejected")
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = response.read(wheel["size"] + 1)
                if len(data) != wheel["size"] or "sha256:" + sha(data) != wheel["hash"]:
                    raise ValueError("parser wheel integrity failure")
                (wheelhouse / url.rsplit("/", 1)[1]).write_bytes(data)
                requirements.append(
                    f"{wheel['name']}=={wheel['version']} --hash={wheel['hash']}"
                )
            (build / "requirements.txt").write_text("\n".join(requirements) + "\n")
            (build / "provenance.json").write_text(
                json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n"
            )
            iid = work / "image-id"
            # --network=none supports the installed legacy builder too; install
            # has an offline local wheelhouse and cannot access package networks.
            run(
                [
                    *docker,
                    "build",
                    "--network=none",
                    "--platform",
                    platform,
                    "--iidfile",
                    str(iid),
                    "--build-arg",
                    "PYTHON_IMAGE="
                    + inputs["repository"]
                    + "@"
                    + expected["base_manifest"],
                    str(build),
                ],
                timeout=600,
            )
            image_id = iid.read_text().strip()
            receipt = {**expected, "image_id": image_id}
        image_id = receipt["image_id"]
        if not re.fullmatch("sha256:[0-9a-f]{64}", image_id):
            raise ValueError("parser receipt image identity invalid")
        image = json.loads(run([*docker, "image", "inspect", image_id]))[0]
        if (
            image["Os"] + "/" + image["Architecture"] != platform
            or image["Config"]["User"] != "65532:65532"
        ):
            raise ValueError("parser image platform or user mismatch")
        cid = run([*docker, "create", "--network=none", image_id])
        try:
            extracted = work / "payload"
            run([*docker, "cp", cid + ":/opt/parser", str(extracted)])
            baked = json.loads((extracted / "provenance.json").read_text())
            if baked != expected:
                raise ValueError("parser image provenance mismatch")
            for name in inputs["sources"]:
                actual = extracted / "src" / Path(name).relative_to("apps/backend/src")
                if sha(actual.read_bytes()) != hashes[name]:
                    raise ValueError("parser image source digest mismatch")
            if (
                sha((extracted / "worker.py").read_bytes())
                != hashes["deploy/parser/worker.py"]
            ):
                raise ValueError("parser launcher digest mismatch")
            files = {
                str(p.relative_to(extracted)): sha(p.read_bytes())
                for p in sorted(extracted.rglob("*"))
                if p.is_file()
            }
            payload_hash = sha(
                json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
            )
            if (
                args.command == "verify"
                and receipt.get("payload_sha256") != payload_hash
            ):
                raise ValueError("parser installed payload digest mismatch")
            receipt["payload_sha256"] = payload_hash
        finally:
            run([*docker, "rm", "-v", cid])
        if any(
            sha((root / name).read_bytes()) != digest for name, digest in hashes.items()
        ):
            raise ValueError("parser source changed during build")
        if args.command == "build":
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.receipt.with_suffix(".tmp")
            temporary.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
            temporary.replace(args.receipt)
        print(image_id)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(
            "parser build or receipt verification failed: " + str(error),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
