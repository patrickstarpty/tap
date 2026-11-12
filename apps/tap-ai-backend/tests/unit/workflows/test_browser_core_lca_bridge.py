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


def real_inline_args(tmp_path):
    binary = tmp_path / "opencode"
    binary.touch()
    env = tmp_path / "model.env"
    env.touch()
    contract = tmp_path / "inputs.json"
    contract.write_text(
        '{"version":1,"reviewed":true,"fields":[{"id":"age","value":"30","readback_value":"30","attribute":"value","target_texts":["Age"]}]}'
    )
    return [
        "runner",
        "--mode",
        "real",
        "--site",
        "public",
        "--execution-mode",
        "local",
        "--opencode-binary",
        str(binary),
        "--lca-inline-real",
        "--goal",
        "Request a synthetic quote only",
        "--start-url",
        "https://example.org/quote",
        "--input-notes",
        "Synthetic adult age 30",
        "--expected",
        "Visible quote",
        "--max-model-calls",
        "30",
        "--max-cost-usd",
        "1",
        "--deadline-seconds",
        "300",
        "--env-file",
        str(env),
        "--acceptance-contract-file",
        str(contract),
    ]


def test_inline_real_requires_explicit_authorization_switch(monkeypatch, tmp_path):
    args = real_inline_args(tmp_path)
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit):
        runner().parse_args()
    monkeypatch.setattr("sys.argv", args + ["--authorize-real-run"])
    assert runner().parse_args().lca_inline_real


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--max-model-calls", "31"),
        ("--max-cost-usd", "1.01"),
        ("--site", "owned"),
        ("--mode", "synthetic"),
    ],
)
def test_inline_real_rejects_wrong_mode_or_excess_bounds(monkeypatch, tmp_path, flag, value):
    args = real_inline_args(tmp_path) + ["--authorize-real-run"]
    args[args.index(flag) + 1] = value
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit):
        runner().parse_args()


def test_inline_real_requires_reviewed_contract(monkeypatch, tmp_path):
    args = real_inline_args(tmp_path) + ["--authorize-real-run"]
    offset = args.index("--acceptance-contract-file")
    del args[offset : offset + 2]
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit):
        runner().parse_args()


def test_inline_real_denial_precedes_resource_creation(monkeypatch, tmp_path):
    module = runner()
    monkeypatch.setattr("sys.argv", real_inline_args(tmp_path))
    monkeypatch.setattr(
        module.tempfile, "mkdtemp", lambda **kwargs: pytest.fail("must not create resources")
    )
    with pytest.raises(SystemExit):
        module.main()


def test_inline_real_rejects_non_qwen_max_before_resources(monkeypatch, tmp_path):
    module = runner()
    monkeypatch.setattr("sys.argv", real_inline_args(tmp_path) + ["--authorize-real-run"])
    monkeypatch.setattr(
        module,
        "model_execution_settings",
        lambda *args: {"TAP_BROWSER_CODING_MODEL_ALIAS": "qwen-plus"},
    )
    monkeypatch.setattr(
        module.tempfile, "mkdtemp", lambda **kwargs: pytest.fail("must not create resources")
    )
    with pytest.raises(ValueError, match="qwen-max"):
        module.main()
