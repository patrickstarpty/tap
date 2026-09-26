#!/usr/bin/env python3
"""Rebuild an owned isolated Insights projection without deleting any volume."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

from sqlalchemy import create_engine

from tap_platform.insights.adapters.clickhouse import ClickHouseInsightsStore
from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger
from tap_platform.insights.adapters.objects import FileReportObjectStore
from tap_platform.insights.application.projection import ProjectionRebuilder


_OWNED_PROJECT = re.compile(r"tap-insights-[a-z0-9][a-z0-9_-]{2,48}")
_TARGET = re.compile(r"rebuild-[a-z0-9][a-z0-9._-]{2,119}")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--object-root", type=Path, required=True)
    parser.add_argument("--clickhouse-url", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm-target")
    return parser.parse_args()


def _reject(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _database_name(url: str) -> tuple[str | None, str]:
    parsed = urllib.parse.urlsplit(url)
    return parsed.hostname, parsed.path.lstrip("/").split("?", 1)[0]


def _verify_owned_clickhouse(compose_project: str, clickhouse_url: str) -> None:
    """Bind a confirmed rebuild to the named repository-owned Compose service."""
    parsed = urllib.parse.urlsplit(clickhouse_url)
    container_id = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            compose_project,
            "-f",
            str(_REPOSITORY_ROOT / "compose.yaml"),
            "--profile",
            "insights",
            "ps",
            "-q",
            "clickhouse",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not container_id or "\n" in container_id:
        raise RuntimeError("owned ClickHouse Compose service is not uniquely running")
    container = json.loads(
        subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )[0]
    labels = container["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != compose_project
        or labels.get("com.docker.compose.service") != "clickhouse"
        or labels.get("com.docker.compose.project.working_dir") != str(_REPOSITORY_ROOT)
    ):
        raise RuntimeError("ClickHouse container is not owned by this Compose project")
    bindings = container["NetworkSettings"]["Ports"].get("8123/tcp") or []
    if not any(
        binding.get("HostIp") == "127.0.0.1"
        and int(binding.get("HostPort", 0)) == parsed.port
        for binding in bindings
    ):
        raise RuntimeError("ClickHouse URL does not match the owned Compose service")


def main() -> int:
    args = _arguments()
    if _OWNED_PROJECT.fullmatch(args.compose_project) is None:
        return _reject("rebuild requires an owned isolated Compose project")
    if _TARGET.fullmatch(args.target_version) is None:
        return _reject("rebuild target must be an explicit rebuild-* version")
    mysql_host, mysql_database = _database_name(args.database_url)
    clickhouse = urllib.parse.urlsplit(args.clickhouse_url)
    clickhouse_database = urllib.parse.parse_qs(clickhouse.query).get("database", [""])[
        0
    ]
    if mysql_host not in {
        "127.0.0.1",
        "localhost",
        "::1",
    } or not (mysql_database == "tap" or mysql_database.startswith("tap_task10")):
        return _reject("rebuild refuses a shared, default, or non-loopback MySQL")
    if (
        clickhouse.hostname
        not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        or clickhouse_database != "tap_insights"
    ):
        return _reject("rebuild refuses a shared, default, or non-loopback ClickHouse")
    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "dry-run",
                    "composeProject": args.compose_project,
                    "targetVersion": args.target_version,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.confirm_target != args.target_version:
        return _reject("--confirm-target must exactly equal --target-version")
    if not args.object_root.is_dir():
        return _reject("object root must already exist for a confirmed rebuild")
    writer_user = os.getenv("TAP_CLICKHOUSE_WRITER_USER")
    writer_password = os.getenv("TAP_CLICKHOUSE_WRITER_PASSWORD")
    if not writer_user or not writer_password:
        return _reject("confirmed rebuild requires ClickHouse writer credentials")
    try:
        _verify_owned_clickhouse(args.compose_project, args.clickhouse_url)
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        return _reject(f"owned ClickHouse verification failed: {exc}")
    engine = create_engine(args.database_url, pool_pre_ping=True)
    try:
        ledger = SqlAlchemyReportLedger(engine)
        result = ProjectionRebuilder(
            ledger=ledger,
            objects=FileReportObjectStore(args.object_root),
            store=ClickHouseInsightsStore(
                args.clickhouse_url,
                username=writer_user,
                password=writer_password,
            ),
        ).rebuild(target_version=args.target_version)
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "action": "rebuilt",
                "projectionVersion": result.projection_version,
                "rowCount": result.row_count,
                "checksum": result.oracle_checksum,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
