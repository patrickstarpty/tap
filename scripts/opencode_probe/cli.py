"""Admission and bounded evidence options for OpenCode capability probes.

Task 0.2 does not send model prompts; Task 0.3 supplies scenario execution.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

SCENARIOS = (
    "simple-question",
    "auto-delegation",
    "explicit-delegation",
    "parallel",
    "permissions",
    "control-receipt",
    "resume-workspace",
    "reconcile",
    "budget-enforcement",
)


def _positive(value: str) -> int:
    parsed = int(value)
    if not 0 < parsed <= 3600:
        raise argparse.ArgumentTypeError("must be between 1 and 3600")
    return parsed


def make_child_env(
    root: Path, gate_url: str, scoped_token: str, server_password: str
) -> dict[str, str]:
    """Construct a clean OpenCode environment without inherited credentials."""
    parsed = urlsplit(gate_url)
    if (
        root.is_symlink()
        or not root.is_dir()
        or parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not scoped_token
        or not server_password
    ):
        raise ValueError("invalid isolated probe environment")
    directories = {
        "HOME": "home",
        "XDG_CONFIG_HOME": "xdg-config",
        "XDG_DATA_HOME": "xdg-data",
        "XDG_CACHE_HOME": "xdg-cache",
        "XDG_STATE_HOME": "xdg-state",
        "OPENCODE_CONFIG_DIR": "opencode-config",
    }
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8"}
    for name, relative in directories.items():
        path = root / relative
        if path.is_symlink():
            raise ValueError("isolated directory is a symlink")
        path.mkdir(mode=0o700, exist_ok=True)
        env[name] = str(path)
    env.update(
        {
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
            "OPENCODE_SERVER_PASSWORD": server_password,
            "OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS": "1",
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                {
                    "autoupdate": False,
                    "plugin": [],
                    "enabled_providers": ["litellm"],
                    "provider": {
                        "litellm": {
                            "npm": "@ai-sdk/openai-compatible",
                            "options": {
                                "baseURL": gate_url + "/v1",
                                "apiKey": scoped_token,
                            },
                            "models": {"qwen-plus": {"name": "qwen-plus"}},
                        }
                    },
                },
                separators=(",", ":"),
            ),
        }
    )
    return env


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded OpenCode V1 capability probe admission"
    )
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=_positive, required=True)
    parser.add_argument("--max-model-calls", type=_positive, required=True)
    args = parser.parse_args(argv)

    opted_in = all(
        os.environ.get(name) == "1"
        for name in ("TAP_RUN_TAPPER_REAL_MODEL_SMOKE", "TAP_RUN_OPENCODE_PROBE")
    )
    report = {
        "scenario": args.scenario,
        "status": "unverified",
        "reason": "scenario_execution_pending_task_0_3"
        if opted_in
        else "opt_in_required",
        "max_seconds": args.max_seconds,
        "max_model_calls": args.max_model_calls,
        "model_calls": 0,
        "budget_exceeded": False,
    }
    destination: Path = args.output
    if destination.is_symlink() or destination.exists():
        parser.error("output must be a new regular path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
