"""The isolated acceptance supervisor must bind only its own site."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from scripts.browser_core_worker import verified_site_target


def test_verified_site_target_uses_inspected_owned_container_address():
    inspected = [
        {
            "Id": "a" * 64,
            "Name": "/tap-browser-core-site-abc",
            "Config": {"Labels": {"tap.owner": "tap-browser-core-abc"}},
            "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.4"}}},
        }
    ]
    assert (
        verified_site_target(
            inspected, "a" * 64, "tap-browser-core-site-abc", "tap-browser-core-abc"
        )
        == "http://172.17.0.4:8080"
    )


@pytest.mark.parametrize("mutation", ["id", "owner", "name", "address"])
def test_verified_site_target_rejects_unowned_or_invalid_address(mutation):
    inspected = [
        {
            "Id": "a" * 64,
            "Name": "/tap-browser-core-site-abc",
            "Config": {"Labels": {"tap.owner": "tap-browser-core-abc"}},
            "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.4"}}},
        }
    ]
    if mutation == "id":
        inspected[0]["Id"] = "b" * 64
    elif mutation == "owner":
        inspected[0]["Config"]["Labels"]["tap.owner"] = "foreign"
    elif mutation == "name":
        inspected[0]["Name"] = "/foreign"
    else:
        inspected[0]["NetworkSettings"]["Networks"]["bridge"]["IPAddress"] = "127.0.0.1"
    with pytest.raises(ValueError):
        verified_site_target(
            inspected, "a" * 64, "tap-browser-core-site-abc", "tap-browser-core-abc"
        )


def test_failure_accounting_requires_terminal_or_waiting_tasks_and_all_settlements(
    tmp_path,
):
    import importlib.util
    import json

    path = Path(__file__).resolve().parents[4] / "scripts/run-browser-core-e2e.py"
    spec = importlib.util.spec_from_file_location("browser_core_acceptance", path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    manifest = [
        {"task_id": "task-1", "status": "failed", "budget_used": {"model_calls": 1}}
    ]
    audit = {
        "events": [
            {"kind": "action.reserved", "payload": {"action_id": "a"}},
            {"kind": "action.settled", "payload": {"action_id": "a"}},
        ]
    }
    (tmp_path / "usage-manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "task-1-audit.json").write_text(json.dumps(audit))
    assert runner.failure_accounting_confirmed(tmp_path)

    manifest[0]["status"] = "running"
    (tmp_path / "usage-manifest.json").write_text(json.dumps(manifest))
    assert not runner.failure_accounting_confirmed(tmp_path)
    manifest[0]["status"] = "awaiting_input"
    (tmp_path / "usage-manifest.json").write_text(json.dumps(manifest))
    assert runner.failure_accounting_confirmed(tmp_path)
    audit["events"].pop()
    (tmp_path / "task-1-audit.json").write_text(json.dumps(audit))
    assert not runner.failure_accounting_confirmed(tmp_path)


def test_unconfirmed_accounting_stops_only_owned_compose_without_deleting_data(
    monkeypatch,
):
    import importlib.util

    path = Path(__file__).resolve().parents[4] / "scripts/run-browser-core-e2e.py"
    spec = importlib.util.spec_from_file_location("browser_core_acceptance", path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    called = []
    monkeypatch.setattr(
        runner.helpers, "run", lambda argv, env, **kwargs: called.append(argv)
    )
    compose = [
        "docker",
        "compose",
        "-p",
        "tap-browser-core-exact",
        "-f",
        "deploy/compose.yaml",
    ]
    runner.finish_owned_compose(compose, {}, preserve_accounting=True)
    assert called == [compose + ["stop"]]


@pytest.mark.parametrize(
    ("cost", "accepted"),
    [
        ("0.90", True),
        ("1.0", True),
        ("0", False),
        ("NaN", False),
        ("Infinity", False),
        ("1.01", False),
    ],
)
def test_real_supervisor_accepts_only_positive_finite_cost_up_to_one_dollar(
    tmp_path, monkeypatch, cost, accepted
):
    import importlib.util

    path = Path(__file__).resolve().parents[4] / "scripts/run-browser-core-e2e.py"
    spec = importlib.util.spec_from_file_location("browser_core_acceptance", path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    env_file = tmp_path / "host-env"
    env_file.write_text("")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-browser-core-e2e.py",
            "--mode",
            "real",
            "--site",
            "owned",
            "--goal",
            "Read page",
            "--start-url",
            "http://example.test/",
            "--input-notes",
            "sample",
            "--expected",
            "actual result",
            "--max-model-calls",
            "16",
            "--max-cost-usd",
            cost,
            "--deadline-seconds",
            "240",
            "--env-file",
            str(env_file),
        ],
    )
    if accepted:
        assert runner.parse_args().max_cost_usd.is_finite()
    else:
        with pytest.raises(SystemExit, match="2"):
            runner.parse_args()
