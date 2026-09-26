#!/usr/bin/env python3
"""Rebuild an owned isolated Insights projection without deleting any volume."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

from sqlalchemy import create_engine

from tap_platform.insights.adapters.clickhouse import ClickHouseInsightsStore
from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger
from tap_platform.insights.adapters.objects import FileReportObjectStore
from tap_platform.insights.application.projection import ProjectionRebuilder


_OWNED_PROJECT = re.compile(r"tap-insights-rebuild-[a-z0-9][a-z0-9_-]{2,39}")
_TARGET = re.compile(r"rebuild-[a-z0-9][a-z0-9._-]{2,119}")


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
    } or not mysql_database.startswith("tap_task10"):
        return _reject("rebuild refuses a shared, default, or non-loopback MySQL")
    if clickhouse.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    } or not clickhouse_database.startswith("tap_task10"):
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
