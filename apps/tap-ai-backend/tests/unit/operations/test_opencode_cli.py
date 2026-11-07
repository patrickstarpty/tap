"""Deterministic command admission without model calls."""

import json
from pathlib import Path

import pytest
from scripts.opencode_probe.cli import main


def test_help_lists_bounded_admission_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    for option in ("--scenario", "--output", "--max-seconds", "--max-model-calls"):
        assert option in help_text


def test_missing_opt_in_writes_unverified_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TAP_RUN_TAPPER_REAL_MODEL_SMOKE", raising=False)
    monkeypatch.delenv("TAP_RUN_OPENCODE_PROBE", raising=False)
    output = tmp_path / "report.json"
    code = main(
        [
            "--scenario",
            "simple-question",
            "--output",
            str(output),
            "--max-seconds",
            "10",
            "--max-model-calls",
            "2",
        ]
    )
    assert code == 0
    report = json.loads(output.read_text())
    assert report["status"] == "unverified"
    assert report["reason"] == "opt_in_required"
    assert report["model_calls"] == 0


def test_invalid_caps_are_rejected_before_output(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    with pytest.raises(SystemExit):
        main(
            [
                "--scenario",
                "simple-question",
                "--output",
                str(output),
                "--max-seconds",
                "0",
                "--max-model-calls",
                "2",
            ]
        )
    assert not output.exists()


def test_child_env_is_isolated_and_uses_scoped_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.opencode_probe.cli import make_child_env

    monkeypatch.setenv("OPENAI_API_KEY", "upstream-secret")
    env = make_child_env(tmp_path, "http://127.0.0.1:54321", "scoped-token", "server-password")
    assert "OPENAI_API_KEY" not in env
    assert "upstream-secret" not in json.dumps(env)
    assert env["HOME"].startswith(str(tmp_path))
    assert env["OPENCODE_SERVER_PASSWORD"] == "server-password"
    assert env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] == "1"
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["enabled_providers"] == ["litellm"]
    assert config["provider"]["litellm"]["options"]["baseURL"] == "http://127.0.0.1:54321/v1"
    assert config["provider"]["litellm"]["options"]["apiKey"] == "scoped-token"
