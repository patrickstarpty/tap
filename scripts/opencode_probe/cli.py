"""Dual opt-in and bounded options for OpenCode capability probes."""

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

import yaml

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
    "roster-5",
    "roster-25",
    "roster-100",
    "config-freeze",
)


def _positive(value: str) -> int:
    parsed = int(value)
    if not 0 < parsed <= 3600:
        raise argparse.ArgumentTypeError("must be between 1 and 3600")
    return parsed


def _cost_cap(value: str) -> float:
    parsed = float(value)
    if not 0 < parsed <= 0.60:
        raise argparse.ArgumentTypeError("must be above zero and at most USD 0.60")
    return parsed


def make_child_env(
    root: Path,
    gate_url: str,
    scoped_token: str,
    server_password: str,
    *,
    model_input_limit: int = 129024,
    model_output_limit: int = 512,
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
        or model_input_limit <= 0
        or not 0 < model_output_limit <= 512
    ):
        raise ValueError("invalid isolated probe environment")
    resolved = root.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        resolved != root
        or not resolved.is_relative_to(temp_root)
        or root.stat().st_uid != os.getuid()
    ):
        raise ValueError("probe workspace must be supervisor-owned temporary root")
    for ancestor in root.parents:
        if ancestor == temp_root:
            break
        if any(
            (ancestor / name).exists()
            for name in (".opencode", "opencode.json", "opencode.jsonc", ".git")
        ):
            raise ValueError("ancestor project config is not isolated")
    if any(
        (root / name).exists()
        for name in (
            "opencode.json",
            "opencode.jsonc",
            ".claude",
            ".agents",
            ".git",
            "AGENTS.md",
            "CLAUDE.md",
        )
    ):
        raise ValueError("workspace contains unapproved discovery input")
    project_config = root / ".opencode"
    if project_config.exists():
        if (
            project_config.is_symlink()
            or not project_config.is_dir()
            or {p.name for p in project_config.iterdir()} - {"agents", "opencode.json"}
        ):
            raise ValueError("workspace contains unapproved OpenCode config")
    directories = {
        "HOME": "home",
        "XDG_CONFIG_HOME": "xdg-config",
        "XDG_DATA_HOME": "xdg-data",
        "XDG_CACHE_HOME": "xdg-cache",
        "XDG_STATE_HOME": "xdg-state",
        "OPENCODE_CONFIG_DIR": "opencode-config",
    }
    if any(
        (root / relative).exists() or (root / relative).is_symlink()
        for relative in directories.values()
    ):
        raise ValueError("isolated directories must be fresh")
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8"}
    for name, relative in directories.items():
        path = root / relative
        path.mkdir(mode=0o700)
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
                            "models": {
                                "qwen-plus": {
                                    "name": "qwen-plus",
                                    "limit": {
                                        "context": model_input_limit,
                                        "input": model_input_limit,
                                        "output": model_output_limit,
                                    },
                                }
                            },
                        }
                    },
                },
                separators=(",", ":"),
            ),
        }
    )
    return env


def validate_snapshot(
    root: Path,
    profile_hashes: dict[str, str],
    config_digest: str,
) -> dict[str, dict[str, object]]:
    """Reject altered discovery inputs before OpenCode loads configuration."""
    if root.is_symlink() or (root / ".opencode").is_symlink():
        raise ValueError("effective config snapshot is symlinked")
    config_path = root / ".opencode/opencode.json"
    agent_dir = root / ".opencode/agents"
    if (
        config_path.is_symlink()
        or not config_path.is_file()
        or hashlib.sha256(config_path.read_bytes()).hexdigest() != config_digest
        or agent_dir.is_symlink()
        or not agent_dir.is_dir()
        or not profile_hashes
        or ".opencode/agents/orchestrator.md" not in profile_hashes
        or {p.name for p in agent_dir.iterdir()}
        != {Path(relative).name for relative in profile_hashes}
    ):
        raise ValueError("effective config snapshot mismatch")
    expected_profiles: dict[str, dict[str, object]] = {}
    for relative, digest in profile_hashes.items():
        path = root / relative
        if (
            path.parent != agent_dir
            or path.suffix != ".md"
            or path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("effective profile snapshot mismatch")
        frontmatter = path.read_text().split("\n---\n", 1)[0].removeprefix("---\n")
        profile = yaml.safe_load(frontmatter)
        if not isinstance(profile, dict) or profile.get("name") != path.stem:
            raise ValueError("effective profile snapshot mismatch")
        expected_profiles[path.stem] = profile
    return expected_profiles


def validate_effective_config(
    root: Path,
    env: dict[str, str],
    effective: object,
    profile_hashes: dict[str, str],
    config_digest: str,
) -> None:
    """Check frozen files again and validate the CLI-resolved V1 config."""
    expected_profiles = validate_snapshot(root, profile_hashes, config_digest)
    if not isinstance(effective, dict):
        raise ValueError("effective config is not an object")
    inline = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    provider = effective.get("provider")
    agents = effective.get("agent")
    litellm = provider.get("litellm") if isinstance(provider, dict) else None
    expected_provider = inline["provider"]["litellm"]
    if (
        effective.get("model") != "litellm/qwen-plus"
        or effective.get("small_model") != "litellm/qwen-plus"
        or effective.get("default_agent") != "orchestrator"
        or effective.get("enabled_providers") != ["litellm"]
        or effective.get("autoupdate") is not False
        or effective.get("plugin") != []
        or not isinstance(provider, dict)
        or set(provider) != {"litellm"}
        or not isinstance(litellm, dict)
        or litellm.get("npm") != expected_provider["npm"]
        or litellm.get("models") != expected_provider["models"]
        or litellm.get("options")
        != {
            "baseURL": expected_provider["options"]["baseURL"],
            "apiKey": "***",
        }
        or not isinstance(agents, dict)
        or set(agents)
        != set(expected_profiles)
        | {"build", "plan", "general", "explore", "compaction", "summary", "title"}
        or effective.get("mcp")
        or effective.get("instructions")
        or effective.get("tools")
    ):
        raise ValueError("effective config includes unapproved provider or discovery")
    for name in ("build", "plan", "general", "explore"):
        if agents[name].get("disable") is not True:
            raise ValueError("effective delegate is enabled")
    for name in ("compaction", "summary", "title"):
        if agents[name].get("model") != "litellm/qwen-plus":
            raise ValueError("effective helper model differs")
    for name, profile in expected_profiles.items():
        resolved = agents[name]
        if any(
            resolved.get(field) != profile.get(field)
            for field in ("mode", "model", "permission")
        ):
            raise ValueError("effective agent permission differs")
        permission = resolved.get("permission")
        if not isinstance(permission, dict) or permission.get("*") != "deny":
            raise ValueError("effective agent permission is broad")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded OpenCode V1 capability probe admission"
    )
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=_positive, required=True)
    parser.add_argument("--max-model-calls", type=_positive, required=True)
    parser.add_argument("--max-cost-usd", type=_cost_cap, default=0.50)
    parser.add_argument("--gateway-env-file", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument(
        "--binary",
        type=Path,
        default=Path(".superpowers/tools/opencode-1.19.5/opencode"),
    )
    args = parser.parse_args(argv)

    opted_in = all(
        os.environ.get(name) == "1"
        for name in ("TAP_RUN_TAPPER_REAL_MODEL_SMOKE", "TAP_RUN_OPENCODE_PROBE")
    )
    report: dict[str, object] = {
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
    if opted_in and (args.gateway_env_file is None or args.ledger is None):
        parser.error("real probe requires --gateway-env-file and --ledger")
    destination: Path = args.output
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        parser.error("output must be a new writable regular path")
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        if opted_in:
            from scripts.opencode_probe.scenarios import (
                run_freeze_experiment,
                run_scenario,
            )

            try:
                runner = (
                    run_freeze_experiment
                    if args.scenario == "config-freeze"
                    else run_scenario
                )
                report = asyncio.run(
                    runner(
                        **(
                            {}
                            if args.scenario == "config-freeze"
                            else {"scenario": args.scenario}
                        ),
                        binary=args.binary,
                        gateway_env_file=args.gateway_env_file,
                        ledger_path=args.ledger,
                        max_calls=args.max_model_calls,
                        max_seconds=args.max_seconds,
                        max_cost_usd=args.max_cost_usd,
                    )
                )
            except Exception as error:
                report = {
                    **report,
                    "reason": "probe_preflight_error:" + type(error).__name__,
                }
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
