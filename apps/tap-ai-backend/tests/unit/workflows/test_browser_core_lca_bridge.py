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
        "--required-upstream-proxy",
        "http://127.0.0.1:12345",
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
    monkeypatch.setattr(module, "verify_required_upstream", lambda value: value)
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


@pytest.mark.parametrize(
    "proxy",
    [
        None,
        "http://example.org:7897",
        "http://user:secret@127.0.0.1:7897",
        "http://127.0.0.1:7897/path",
        "http://127.0.0.1",
        "http://[::1]:12345",
    ],
)
def test_required_upstream_rejects_missing_or_nonlocal_config(proxy):
    with pytest.raises(ValueError):
        runner().verify_required_upstream(proxy)


def test_required_upstream_unreachable_fails_before_models(monkeypatch, tmp_path):
    module = runner()
    args = real_inline_args(tmp_path) + [
        "--authorize-real-run",
        "--required-upstream-proxy",
        "http://127.0.0.1:12345",
    ]
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.setattr(
        module.socket,
        "create_connection",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("unreachable")),
    )
    monkeypatch.setattr(
        module,
        "model_execution_settings",
        lambda *a: pytest.fail("must not reach model configuration"),
    )
    with pytest.raises(ValueError, match="unreachable"):
        module.main()


def test_required_upstream_verified_and_in_actual_worker_environment(monkeypatch):
    module = runner()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    calls = []
    monkeypatch.setattr(
        module.socket,
        "create_connection",
        lambda address, timeout: (calls.append(address), Connection())[1],
    )
    proxy = module.verify_required_upstream("http://127.0.0.1:12345")
    assert calls == [("127.0.0.1", 12345)]
    env = module.selected_browser_env(
        {"TAP_BROWSER_EXECUTION_MODE": "local", "TAP_BROWSER_LOCAL_UPSTREAM_PROXY": proxy}
    )
    assert env["TAP_BROWSER_LOCAL_UPSTREAM_PROXY"] == proxy


def test_reviewed_bridge_fixture_observes_before_plan_and_never_writes_script():
    import io
    import json

    module = runner()
    model = module.SyntheticModel
    model.reviewed_bridge = True
    model.stages, model.calls, model.observed = {}, {}, set()
    emitted = []
    for _ in range(15):
        body = json.dumps(
            {
                "messages": [{"role": "user", "content": "Core form: account=AC-42 amount=120"}],
                "tools": [{"function": {"name": "tap_browser"}}],
            }
        ).encode()
        handler = object.__new__(model)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler.send_response = lambda *_args: None
        handler.send_header = lambda *_args: None
        handler.end_headers = lambda: None
        handler.do_POST()
        chunk = json.loads(handler.wfile.getvalue().decode().split("\n")[0][6:])
        tools = chunk["choices"][0]["delta"].get("tool_calls", [])
        if not tools:
            break
        function = tools[0]["function"]
        assert function["name"] == "tap_browser"
        emitted.append(json.loads(function["arguments"])["action"])
    else:
        raise AssertionError("fixture never completed")
    assert emitted[0]["type"] == "navigate" and "step" not in emitted[0]
    assert emitted[1]["type"] == "plan"
    assert sum(action["type"] == "navigate" for action in emitted) == 1
    assert sum(action["type"] == "click" for action in emitted) == 1
