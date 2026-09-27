#!/usr/bin/env python3
"""Bind local Insights data, credentials and resource cleanup to one owned run."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPOSITORY_ROOT / ".superpowers/runtime/insights"
OWNED_PROJECT = re.compile(r"tap-insights-[a-z0-9][a-z0-9_-]{2,48}")


def runtime_path(project: str) -> Path:
    if OWNED_PROJECT.fullmatch(project) is None or project == "tap-insights-local":
        raise ValueError("requires an owned isolated Insights project")
    path = RUNTIME_ROOT / project
    if path.resolve() != path:
        raise ValueError("Insights runtime must not traverse a symbolic link")
    return path


def initialize(project: str) -> None:
    root = runtime_path(project)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    objects = root / "objects"
    objects.mkdir(mode=0o700)
    authority = {
        "schema": "tap-insights-owned-runtime-v1",
        "composeProject": project,
        "repositoryRoot": str(REPOSITORY_ROOT),
        "objectRoot": str(objects),
        "mysqlDatabase": os.environ["MYSQL_DATABASE"],
        "mysqlPort": int(os.environ["MYSQL_PORT"]),
        "clickhousePort": int(os.environ["CLICKHOUSE_HTTP_PORT"]),
    }
    _write_private(root / "authority.json", authority)
    fields = (
        "MYSQL_ROOT_PASSWORD",
        "MYSQL_DATABASE",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_PORT",
        "CLICKHOUSE_HTTP_PORT",
        "CLICKHOUSE_ADMIN_USER",
        "CLICKHOUSE_ADMIN_PASSWORD",
        "TAP_CLICKHOUSE_WRITER_USER",
        "TAP_CLICKHOUSE_WRITER_PASSWORD",
        "TAP_CLICKHOUSE_READER_USER",
        "TAP_CLICKHOUSE_READER_PASSWORD",
        "TAP_REPORT_ACCESS_TOKEN",
    )
    _write_private(
        root / "credentials.json", {name: os.environ[name] for name in fields}
    )


def _write_private(path: Path, value: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_object_root(
    project: str,
    object_root: Path,
    database: str,
    mysql_port: int,
    clickhouse_port: int,
) -> None:
    root = runtime_path(project)
    if object_root.resolve() != root / "objects" or not object_root.is_dir():
        raise ValueError("raw object root is not bound to this owned run")
    authority = json.loads((root / "authority.json").read_text())
    expected = {
        "schema": "tap-insights-owned-runtime-v1",
        "composeProject": project,
        "repositoryRoot": str(REPOSITORY_ROOT),
        "objectRoot": str(object_root.resolve()),
        "mysqlDatabase": database,
        "mysqlPort": mysql_port,
        "clickhousePort": clickhouse_port,
    }
    if authority != expected:
        raise ValueError("raw object authority does not match both database targets")


def verify_service(
    project: str, service: str, host_port: int, *, database: str | None = None
) -> None:
    runtime_path(project)
    container_id = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(REPOSITORY_ROOT / "compose.yaml"),
            "ps",
            "-q",
            service,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not container_id or "\n" in container_id:
        raise ValueError("owned service is not uniquely running")
    container = json.loads(
        subprocess.run(
            ["docker", "inspect", container_id],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )[0]
    labels = container["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != service
        or labels.get("com.docker.compose.project.working_dir") != str(REPOSITORY_ROOT)
    ):
        raise ValueError("database container ownership mismatch")
    port = "3306/tcp" if service == "mysql" else "8123/tcp"
    bindings = container["NetworkSettings"]["Ports"].get(port) or []
    if (
        len(bindings) != 1
        or bindings[0].get("HostIp") != "127.0.0.1"
        or int(bindings[0].get("HostPort", 0)) != host_port
    ):
        raise ValueError("database URL is not the owned loopback binding")
    if database is not None and f"MYSQL_DATABASE={database}" not in container[
        "Config"
    ].get("Env", []):
        raise ValueError("MySQL database is not owned by this run")


def verify_cleanup(project: str) -> None:
    root = runtime_path(project)
    authority = json.loads((root / "authority.json").read_text())
    if authority["composeProject"] != project or authority["repositoryRoot"] != str(
        REPOSITORY_ROOT
    ):
        raise ValueError("cleanup authority does not match")
    for kind, listing in (
        ("container", ["ps", "-aq"]),
        ("volume", ["volume", "ls", "-q"]),
        ("network", ["network", "ls", "-q"]),
    ):
        ids = subprocess.run(
            [
                "docker",
                *listing,
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        if not ids:
            continue
        command = (
            ["docker", "inspect", *ids]
            if kind == "container"
            else ["docker", kind, "inspect", *ids]
        )
        for resource in json.loads(
            subprocess.run(command, check=True, capture_output=True, text=True).stdout
        ):
            labels = (
                resource["Config"]["Labels"]
                if kind == "container"
                else resource.get("Labels", {})
            )
            if labels.get("com.docker.compose.project") != project:
                raise ValueError("cleanup resource is not owned")
            if kind == "container" and (
                labels.get("com.docker.compose.project.working_dir")
                != str(REPOSITORY_ROOT)
                or labels.get("com.docker.compose.service")
                not in {"mysql", "clickhouse"}
            ):
                raise ValueError("cleanup container is not an owned Insights service")


def _secrets() -> list[str]:
    return sorted(
        {
            value
            for name, value in os.environ.items()
            if len(value) >= 8
            and any(
                token in name
                for token in (
                    "PASSWORD",
                    "TOKEN",
                    "API_KEY",
                    "MASTER_KEY",
                    "CONNECTION_STRING",
                )
            )
        },
        key=len,
        reverse=True,
    )


def redact() -> None:
    values = _secrets()
    for line in sys.stdin:
        for secret in values:
            line = line.replace(secret, "[REDACTED]")
        print(line, end="", flush=True)


def run_redacted() -> int:
    for name in (
        "MYSQL_ROOT_PASSWORD",
        "MYSQL_PASSWORD",
        "CLICKHOUSE_ADMIN_PASSWORD",
        "TAP_CLICKHOUSE_WRITER_PASSWORD",
        "TAP_CLICKHOUSE_READER_PASSWORD",
        "TAP_REPORT_ACCESS_TOKEN",
    ):
        if not os.environ.get(name):
            os.environ[name] = secrets.token_hex(24)
    values = _secrets()
    with subprocess.Popen(
        [
            "bash",
            str(REPOSITORY_ROOT / "scripts/run-tap-insights-e2e.sh"),
            "--redacted-child",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            for secret in values:
                line = line.replace(secret, "[REDACTED]")
            print(line, end="", flush=True)
        return process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("initialize", "verify-cleanup", "redact", "run-redacted")
    )
    parser.add_argument("--compose-project")
    args = parser.parse_args()
    try:
        if args.action == "run-redacted":
            return run_redacted()
        if args.action == "redact":
            redact()
        elif args.action == "initialize":
            initialize(args.compose_project)
        else:
            verify_cleanup(args.compose_project)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("Insights owned runtime validation failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
