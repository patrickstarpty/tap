"""The isolated browser runner must wait for healthy dependencies, not HTTP alone."""

import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path


def _runner():
    path = Path(__file__).resolve().parents[4] / "scripts/run-workflow-e2e.py"
    spec = importlib.util.spec_from_file_location("workflow_e2e_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_readiness_requires_ready_body_and_reports_safe_components() -> None:
    runner = _runner()
    unready = json.dumps(
        {
            "status": "unready",
            "components": [
                {
                    "name": "blob",
                    "state": "failed",
                    "remediationCode": "start-blob",
                    "detail": "private provider detail",
                }
            ],
        }
    ).encode()
    accepted, summary = runner.api_readiness(unready)
    assert accepted is False
    assert "blob:failed" in summary
    assert "private provider detail" not in summary
    assert runner.api_readiness(b'{"status":"ready","components":[]}')[0] is True
    assert runner.api_readiness(b"not json")[0] is False


def test_owned_data_override_binds_only_six_expected_targets(tmp_path: Path) -> None:
    runner = _runner()
    override = runner.write_owned_data_override(tmp_path)
    payload = json.loads(override.read_text())
    assert set(payload["services"]) == {
        "mysql",
        "redis",
        "tap-minio",
        "milvus-etcd",
        "milvus-minio",
        "milvus",
    }
    for service, target in runner.OWNED_DATA_MOUNTS.items():
        mount = payload["services"][service]["volumes"][0]
        assert mount == {
            "type": "bind",
            "source": str(tmp_path / service),
            "target": target,
        }
        assert (tmp_path / service).is_dir()
    effective = {
        "services": {
            service: {"volumes": [payload["services"][service]["volumes"][0]]}
            for service in runner.OWNED_DATA_MOUNTS
        }
    }
    runner.verify_owned_data_mounts(json.dumps(effective), tmp_path)
    effective["services"]["tap-minio"]["volumes"][0]["source"] = "shared-volume"
    try:
        runner.verify_owned_data_mounts(json.dumps(effective), tmp_path)
    except RuntimeError as error:
        assert "tap-minio" in str(error)
    else:
        raise AssertionError("shared storage was accepted")


def test_owned_database_marker_binds_url_container_and_live_runner(tmp_path: Path) -> None:
    runner = _runner()
    tmp_path.chmod(0o700)
    env = {
        "TAP_TAPPER_COMPOSE_PROJECT": "tap-workflow-e2e-abc123def456",
        "TAP_DATABASE_URL": "mysql+asyncmy://tap:owned@127.0.0.1:13307/tap",
        "MYSQL_PORT": "13307",
    }
    marker = runner.write_owned_database_marker(env, tmp_path, "a" * 64, "default")
    receipt = json.loads(marker.read_text())
    assert receipt["project"] == env["TAP_TAPPER_COMPOSE_PROJECT"]
    assert (
        receipt["databaseUrlSha256"] == hashlib.sha256(env["TAP_DATABASE_URL"].encode()).hexdigest()
    )
    assert receipt["mysqlContainerId"] == "a" * 64
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert env["TAPPER_E2E_OWNERSHIP_FILE"] == str(marker)


def _orphaning_parent_script(ready: Path, stopped: Path, exit_code: int) -> str:
    child = (
        "import pathlib,signal,time; "
        f"ready=pathlib.Path({str(ready)!r}); stopped=pathlib.Path({str(stopped)!r}); "
        "signal.signal(signal.SIGTERM, lambda *_: (stopped.write_text('stopped'), exit(0))); "
        "ready.write_text('ready'); time.sleep(2)"
    )
    return (
        "import pathlib,subprocess,sys,time; "
        f"child={child!r}; ready=pathlib.Path({str(ready)!r}); "
        "subprocess.Popen([sys.executable,'-c',child],stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL); "
        "deadline=time.monotonic()+5; "
        "exec('while not ready.exists() and time.monotonic()<deadline: time.sleep(0.01)'); "
        f"sys.exit({exit_code})"
    )


def test_supervisor_cleanup_reaps_owned_group_after_parent_exits(tmp_path: Path) -> None:
    runner = _runner()
    ready, stopped = tmp_path / "ready", tmp_path / "stopped"
    parent = subprocess.Popen(
        [sys.executable, "-c", _orphaning_parent_script(ready, stopped, 0)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    parent.wait(timeout=6)
    assert ready.exists()
    runner.stop_supervisor(parent)
    assert stopped.exists()


def test_browser_failure_reaps_owned_group_after_parent_exits(tmp_path: Path) -> None:
    runner = _runner()
    ready, stopped = tmp_path / "ready", tmp_path / "stopped"
    try:
        runner.run_browser(
            [sys.executable, "-c", _orphaning_parent_script(ready, stopped, 2)],
            dict(__import__("os").environ),
            timeout=6,
        )
    except RuntimeError as error:
        assert "workflow browser failed" in str(error)
    else:
        raise AssertionError("browser failure was accepted")
    assert ready.exists()
    assert stopped.exists()


def test_failed_or_unverified_compose_down_preserves_owned_data_and_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _runner()
    project = "tap-workflow-e2e-abc123def456"
    context = "default"
    compose = ["docker", "--context", context, "compose", "-p", project]
    state = tmp_path / "owned"
    state.mkdir()
    (state / "database-owner.json").write_text("owned receipt")
    (state / "mysql").mkdir()
    (state / "mysql" / "rows").write_text("durable data")

    def failed_down(command, env, *, timeout=300):
        if "down" in command:
            raise RuntimeError("Docker unavailable")
        raise AssertionError("container state must not be guessed after failed down")

    monkeypatch.setattr(runner, "run", failed_down)
    errors = runner.cleanup_owned_state(state, None, True, compose, {}, context, project)
    assert errors and "Docker unavailable" in errors[0]
    assert (state / "database-owner.json").read_text() == "owned receipt"
    assert (state / "mysql" / "rows").read_text() == "durable data"

    def unknown_state(command, env, *, timeout=300):
        if "down" in command:
            return ""
        raise RuntimeError("Docker status unavailable")

    monkeypatch.setattr(runner, "run", unknown_state)
    errors = runner.cleanup_owned_state(state, None, True, compose, {}, context, project)
    assert errors and "Docker status unavailable" in errors[0]
    assert (state / "database-owner.json").exists()

    def still_attached(command, env, *, timeout=300):
        return "" if "down" in command else "a" * 64

    monkeypatch.setattr(runner, "run", still_attached)
    errors = runner.cleanup_owned_state(state, None, True, compose, {}, context, project)
    assert errors and "containers remain" in errors[0]
    assert (state / "database-owner.json").exists()

    def clean_state(command, env, *, timeout=300):
        assert "down" in command or f"label=com.docker.compose.project={project}" in command
        return ""

    monkeypatch.setattr(runner, "run", clean_state)
    assert runner.cleanup_owned_state(state, None, True, compose, {}, context, project) == []
    assert not state.exists()
