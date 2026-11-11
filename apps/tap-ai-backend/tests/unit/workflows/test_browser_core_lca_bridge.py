"""Bridge acceptance is an explicit zero-provider-cost mode."""

import importlib.util
from pathlib import Path

import pytest


def runner():
    script = Path(__file__).resolve().parents[5] / "scripts/run-browser-core-e2e.py"
    spec = importlib.util.spec_from_file_location("lca_bridge_runner", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bridge_acceptance_is_explicit_synthetic_only(monkeypatch, tmp_path):
    module = runner()
    binary = tmp_path / "opencode"
    binary.touch()
    monkeypatch.setattr(
        "sys.argv",
        [
            "runner",
            "--mode",
            "synthetic",
            "--execution-mode",
            "local",
            "--opencode-binary",
            str(binary),
            "--lca-bridge",
        ],
    )
    assert module.parse_args().lca_bridge
    monkeypatch.setattr("sys.argv", ["runner", "--mode", "real", "--lca-bridge"])
    with pytest.raises(SystemExit):
        module.parse_args()


def test_bridge_credentials_are_scoped_to_browser_services():
    env = {
        "TAP_LCA_GATEWAY_TOKEN": "lca-test-token",
        "TAP_BROWSER_GATEWAY_TOKEN": "browser-test-token",
        "TAP_BROWSER_WORKER_TOKEN": "worker-test-token",
        "TAP_BROWSER_CONTROL_ENABLED": "true",
        "TAP_BROWSER_API_ORIGIN": "http://127.0.0.1:19090",
        "LITELLM_MASTER_KEY": "not-for-browser-service",
    }
    assert runner().selected_browser_env(env) == {
        key: value for key, value in env.items() if key != "LITELLM_MASTER_KEY"
    }
