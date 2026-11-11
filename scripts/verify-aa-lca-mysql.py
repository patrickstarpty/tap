#!/usr/bin/env python3
"""Explicit opt-in owned MySQL migration/persistence gate; never use a user DB.

Run from the repository root with an explicitly approved EXISTING network:
  PYTHONPATH=apps/backend/src uv run --no-sync --project apps/backend \
    python scripts/verify-aa-lca-mysql.py --run --network NETWORK_NAME

Requires the already-cached official mysql:8.4.6 image and Docker. Creates no
network, publishes only an ephemeral loopback port, and removes only its own
label-verified container plus anonymous volume. No .env is loaded. The seven LCA
tables are synthetic and disposable. Populated downgrade must refuse retention
loss; empty downgrade must succeed. No paid services are used.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text

from tap_platform.lca.store import LcaStore, drafts, metadata

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "mysql:8.4.6"
LABEL = "tap.owned-migration-test"


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def verify(url: str) -> None:
    os.environ["TAP_DATABASE_URL"] = url
    engine = create_engine(url)
    for i in range(60):
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError("owned mysql did not become ready")
    cfg = Config(str(ROOT / "apps/backend/alembic.ini"))
    command.upgrade(cfg, "0009_browser_control")
    assert not any(t.startswith("tap_lca_") for t in inspect(engine).get_table_names())
    command.upgrade(cfg, "0011_lca_releases")
    assert set(metadata.tables) <= set(inspect(engine).get_table_names())
    command.downgrade(cfg, "0009_browser_control")
    assert not any(t.startswith("tap_lca_") for t in inspect(engine).get_table_names())
    command.upgrade(cfg, "0011_lca_releases")
    store = LcaStore(engine)
    canonical = b'{"title":"' + b"x" * 70000 + b'","steps":[]}'
    f = store.create_flow(
        "synthetic-project", "synthetic-owner", "create", "Debug", canonical
    )
    store.save_draft(
        "synthetic-project", "synthetic-owner", f["id"], 1, b'{"steps":[]}'
    )
    s = store.freeze_snapshot(
        "synthetic-project",
        "synthetic-owner",
        f["id"],
        2,
        "lca-debug-v1",
        "generator-v1",
        [],
    )
    r = store.create_run(
        "synthetic-project", "synthetic-owner", "run", s["id"], "bundle", "a" * 64, {}
    )
    store.link_execution("synthetic-project", "synthetic-owner", r["id"], "execution")
    with engine.begin() as c:
        c.execute(
            metadata.tables["tap_lca_debug_recordings"]
            .insert()
            .values(
                session_id="session",
                project_id="synthetic-project",
                actor_id="synthetic-owner",
                flow_id=f["id"],
                draft_version=2,
                allowed_origins=["https://example.test"],
                created_at="2026-11-10T12:00:00+00:00",
            )
        )
    release = store.create_release(
        "synthetic-project",
        "synthetic-owner",
        "release",
        store.get_flow("synthetic-project", "synthetic-owner", f["id"]),
        s,
        r["id"],
    )
    engine.dispose()
    engine = create_engine(url)
    recovered = LcaStore(engine)
    assert (
        recovered.get_release("synthetic-project", "synthetic-owner", release["id"])[
            "snapshot_id"
        ]
        == s["id"]
    )
    assert (
        recovered.get_flow("synthetic-project", "synthetic-owner", f["id"])["version"]
        == 2
    )
    assert (
        recovered.get_snapshot("synthetic-project", "synthetic-owner", s["id"])[
            "canonical_bytes"
        ]
        == b'{"steps":[]}'
    )
    assert (
        recovered.get_run("synthetic-project", "synthetic-owner", r["id"])[
            "execution_id"
        ]
        == "execution"
    )
    with engine.connect() as c:
        assert (
            c.execute(
                select(drafts.c.canonical_bytes).where(drafts.c.version == 1)
            ).scalar_one()
            == canonical
        )
        assert (
            c.execute(
                text("SELECT actor_id FROM tap_lca_debug_recordings")
            ).scalar_one()
            == "synthetic-owner"
        )
    try:
        command.downgrade(cfg, "0009_browser_control")
    except RuntimeError as exc:
        assert "retention" in str(exc)
    else:
        raise AssertionError("populated downgrade must refuse")
    assert set(metadata.tables) <= set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert (
            c.execute(text("SELECT version_num FROM tap_alembic_version")).scalar_one()
            == "0011_lca_releases"
        )
    print(
        json.dumps(
            {
                "mysql": "8.4.6",
                "tables": 7,
                "empty_downgrade": "passed",
                "large_draft_bytes": len(canonical),
                "reconnect_persistence": "passed",
                "populated_downgrade": "refused before any DROP",
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Explicitly create the disposable test database",
    )
    parser.add_argument(
        "--network",
        required=True,
        help="Explicit approved existing Docker bridge network; no default or creation",
    )
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required; no container was created")
    network = json.loads(docker("network", "inspect", args.network))[0]
    if network.get("Driver") != "bridge":
        parser.error("An explicitly approved existing bridge network is required")
    docker("image", "inspect", IMAGE)  # Never pull/install an image implicitly.
    identity = uuid.uuid4().hex
    password = secrets.token_hex(24)
    container = ""
    try:
        container = docker(
            "run",
            "-d",
            "--pull=never",
            "--name",
            f"tap-lca-migration-{identity}",
            "--label",
            f"{LABEL}={identity}",
            "--network",
            network["Id"],
            "-p",
            "127.0.0.1::3306",
            "-e",
            f"MYSQL_ROOT_PASSWORD={password}",
            "-e",
            "MYSQL_DATABASE=tap_synthetic_migration",
            IMAGE,
        )
        binding = docker("port", container, "3306")
        if not binding.startswith("127.0.0.1:") or "\n" in binding:
            raise RuntimeError("Test database must have exactly one loopback binding")
        port = int(binding.rsplit(":", 1)[1])
        verify(
            f"mysql+pymysql://root:{password}@127.0.0.1:{port}/tap_synthetic_migration"
        )
    finally:
        if container:
            actual = json.loads(docker("inspect", container))[0]
            if (
                actual["Id"] != container
                or actual["Config"]["Labels"].get(LABEL) != identity
            ):
                raise RuntimeError(
                    "Cleanup ownership check failed; container not removed"
                )
            docker("rm", "-fv", container)
            print("Owned test container and anonymous volume removed.")


if __name__ == "__main__":
    main()
