from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[4]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    return module


def test_confirmed_rebuild_rejects_unowned_mysql_before_opening_ledger(
    tmp_path, monkeypatch
):
    module = load_script("rebuild-insights")
    monkeypatch.setattr(
        module,
        "_arguments",
        lambda: argparse.Namespace(
            compose_project="tap-insights-finalfix-owned",
            target_version="rebuild-owned-v2",
            database_url="mysql+pymysql://tap:secret@127.0.0.1:33319/tap",
            clickhouse_url="http://127.0.0.1:38123/?database=tap_insights",
            object_root=tmp_path,
            dry_run=False,
            confirm_target="rebuild-owned-v2",
        ),
    )
    monkeypatch.setenv("TAP_CLICKHOUSE_WRITER_USER", "writer")
    monkeypatch.setenv("TAP_CLICKHOUSE_WRITER_PASSWORD", "secret-password")
    monkeypatch.setattr(module, "_verify_owned_clickhouse", lambda *_: None)

    def forbid_ledger(*_args, **_kwargs):
        pytest.fail(
            "unowned MySQL must be rejected before creating a ledger connection"
        )

    monkeypatch.setattr(module, "create_engine", forbid_ledger)
    assert module.main() == 2


def test_preserve_runner_keeps_raw_authority_and_redacts_logs(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    nonce = os.urandom(6).hex()
    project = f"tap-insights-task14-{nonce}-journey"
    artifacts = tmp_path / "tap-task14.logs"
    artifacts.mkdir()
    commands = tmp_path / "commands"
    docker = tools / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\nexit 0\n')
    uv = tools / "uv"
    uv.write_text("""#!/bin/sh
mkdir -p "$TAP_REPORT_OBJECT_ROOT/raw/ab"
printf 'retained junit artifact' > "$TAP_REPORT_OBJECT_ROOT/raw/ab/original.xml"
printf 'private %s\\n' "$MYSQL_PASSWORD"
exit 9
""")
    lsof = tools / "lsof"
    lsof.write_text("#!/bin/sh\nexit 1\n")
    for tool in (docker, uv, lsof):
        tool.chmod(0o755)
    environment = dict(
        os.environ,
        PATH=f"{tools}:{os.environ['PATH']}",
        TMPDIR=str(tmp_path),
        COMMAND_LOG=str(commands),
        TAP_INSIGHTS_E2E_PROJECT=project,
        TAP_INSIGHTS_E2E_PRESERVE_VOLUMES="1",
        TAP_INSIGHTS_E2E_ARTIFACTS=str(artifacts),
        MYSQL_PASSWORD="redaction-secret-for-test",
    )
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run-tap-insights-e2e.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 9
    retained = (
        ROOT / ".superpowers/runtime/insights" / project / "objects/raw/ab/original.xml"
    )
    assert retained.read_bytes() == b"retained junit artifact"
    owner = json.loads((retained.parents[3] / "authority.json").read_text())
    assert owner["composeProject"] == project
    credentials = retained.parents[3] / "credentials.json"
    assert credentials.stat().st_mode & 0o777 == 0o600
    assert "redaction-secret-for-test" not in result.stdout + result.stderr
    assert "down -v" not in commands.read_text()


@pytest.mark.parametrize(
    "mutation",
    ["project", "service", "working-dir", "port", "host", "database", "multiple"],
)
def test_rebuild_verifies_mysql_container_and_database_ownership(monkeypatch, mutation):
    module = load_script("insights_runtime")
    project = "tap-insights-finalfix-owned"
    labels = {
        "com.docker.compose.project": project,
        "com.docker.compose.service": "mysql",
        "com.docker.compose.project.working_dir": str(ROOT),
    }
    value = {
        "Config": {"Labels": labels, "Env": ["MYSQL_DATABASE=tap_task10_owned"]},
        "NetworkSettings": {
            "Ports": {"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": "33319"}]}
        },
    }
    if mutation == "project":
        labels["com.docker.compose.project"] = "shared"
    elif mutation == "service":
        labels["com.docker.compose.service"] = "other"
    elif mutation == "working-dir":
        labels["com.docker.compose.project.working_dir"] = "/shared"
    elif mutation in {"port", "host"}:
        value["NetworkSettings"]["Ports"]["3306/tcp"][0][
            "HostPort" if mutation == "port" else "HostIp"
        ] = "3306" if mutation == "port" else "0.0.0.0"
    elif mutation == "database":
        value["Config"]["Env"] = ["MYSQL_DATABASE=tap"]

    def docker(command, **kwargs):
        output = (
            json.dumps([value])
            if "inspect" in command
            else "owned-id\nother-id"
            if mutation == "multiple"
            else "owned-id"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(module.subprocess, "run", docker)
    with pytest.raises(ValueError):
        module.verify_service(project, "mysql", 33319, database="tap_task10_owned")


def test_rebuild_raw_object_root_is_bound_to_both_databases(tmp_path, monkeypatch):
    module = load_script("insights_runtime")
    monkeypatch.setattr(module, "RUNTIME_ROOT", tmp_path)
    project = "tap-insights-finalfix-owned"
    root = tmp_path / project
    objects = root / "objects"
    objects.mkdir(parents=True)
    authority = {
        "schema": "tap-insights-owned-runtime-v1",
        "composeProject": project,
        "repositoryRoot": str(ROOT),
        "objectRoot": str(objects),
        "mysqlDatabase": "tap_task10_owned",
        "mysqlPort": 33319,
        "clickhousePort": 38123,
    }
    (root / "authority.json").write_text(json.dumps(authority))
    module.verify_object_root(project, objects, "tap_task10_owned", 33319, 38123)
    with pytest.raises(ValueError):
        module.verify_object_root(project, objects, "tap_task10_shared", 33319, 38123)
    with pytest.raises(ValueError):
        module.verify_object_root(project, objects, "tap_task10_owned", 33319, 38124)
