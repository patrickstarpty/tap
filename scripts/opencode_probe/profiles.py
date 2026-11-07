"""Export byte-stable, task-scoped OpenCode V1 agent profiles."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from scripts.opencode_probe.models import PROBE_MODEL, ProbeAgent

_AGENT_ID = re.compile(r"[a-z0-9][a-z0-9_-]*\Z", re.ASCII)
_PERMISSIONS = frozenset(
    {
        "read",
        "glob",
        "grep",
        "list",
        "edit",
        "bash",
        "task",
        "webfetch",
        "websearch",
        "lsp",
        "skill",
        "todowrite",
        "question",
    }
)
_ALIASES = {"write": "edit", "patch": "edit"}
_BUILTIN_DELEGATES = ("build", "plan", "general", "explore")
_BUILTIN_HELPERS = ("compaction", "summary", "title")


def _quote(value: str) -> str:
    """JSON strings are valid YAML scalars and escape injected line breaks."""
    return json.dumps(value, ensure_ascii=True)


def _validate_agent(agent: ProbeAgent) -> tuple[str, ...]:
    if not _AGENT_ID.fullmatch(agent.agent_id):
        raise ValueError(f"invalid agent ID: {agent.agent_id!r}")
    if agent.agent_id in {*_BUILTIN_DELEGATES, *_BUILTIN_HELPERS}:
        raise ValueError(f"reserved OpenCode agent ID: {agent.agent_id!r}")
    if not agent.description.strip():
        raise ValueError(f"agent {agent.agent_id!r} has no description")
    mapped = tuple(_ALIASES.get(tool, tool) for tool in agent.tools)
    unknown = set(mapped) - _PERMISSIONS
    if unknown:
        raise ValueError(
            f"agent {agent.agent_id!r} has unknown tools: {sorted(unknown)!r}"
        )
    if agent.agent_id == "qae" and set(mapped) & {"edit", "bash"}:
        raise ValueError("QAE is read-only")
    if agent.agent_id != "orchestrator" and "task" in mapped:
        raise ValueError("only the orchestrator may delegate")
    return tuple(sorted(set(mapped)))


def _render(
    agent: ProbeAgent, tools: tuple[str, ...], delegated: tuple[str, ...]
) -> bytes:
    lines = [
        "---",
        f"name: {_quote(agent.agent_id)}",
        f"description: {_quote(agent.description)}",
        f"mode: {'primary' if agent.agent_id == 'orchestrator' else 'subagent'}",
        f"model: {_quote(PROBE_MODEL)}",
        "permission:",
        '  "*": deny',
    ]
    for tool in tools:
        if tool == "read":
            lines.extend(
                (
                    "  read:",
                    '    "*": allow',
                    '    "*.env": deny',
                    '    "*.env.*": deny',
                )
            )
        elif tool != "task":
            lines.append(f"  {tool}: allow")
    if agent.agent_id == "orchestrator" and "task" in tools and delegated:
        lines.extend(("  task:", '    "*": deny'))
        lines.extend(f"    {_quote(agent_id)}: allow" for agent_id in delegated)
    else:
        lines.append("  task: deny")
    if agent.agent_id == "qae":
        lines.extend(("  edit: deny", "  bash: deny"))
    lines.extend(
        (f"# revision: {_quote(agent.revision_id)}", "---", "", agent.instruction, "")
    )
    return "\n".join(lines).encode("utf-8")


def _verify_existing(directory: Path, expected: dict[str, bytes]) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("existing profile snapshot is not a directory")
    if {entry.name for entry in directory.iterdir()} != set(expected):
        raise ValueError("existing profile snapshot differs in file set")
    for name, content in expected.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"existing profile snapshot differs: {name}")


def export_profiles(
    root: Path, agents: tuple[ProbeAgent, ...], allowed_ids: frozenset[str]
) -> dict[str, str]:
    """Freeze authorized profiles in ``root/.opencode/agents``.

    The caller owns an isolated workspace. An existing snapshot is verified
    byte for byte and never rewritten; a changed revision needs a new root.
    """
    if root.is_symlink() or not root.is_dir():
        raise ValueError("profile root must be a real directory")
    if any(not _AGENT_ID.fullmatch(agent_id) for agent_id in allowed_ids):
        raise ValueError("invalid allowed agent ID")

    seen: set[str] = set()
    selected: dict[str, tuple[ProbeAgent, tuple[str, ...]]] = {}
    for agent in agents:
        if agent.agent_id in seen:
            raise ValueError(f"duplicate agent ID: {agent.agent_id!r}")
        seen.add(agent.agent_id)
        tools = _validate_agent(agent)
        if agent.enabled and agent.agent_id in allowed_ids:
            selected[agent.agent_id] = (agent, tools)

    delegated = tuple(sorted(selected.keys() - {"orchestrator"}))
    rendered = {
        f"{agent_id}.md": _render(agent, tools, delegated)
        for agent_id, (agent, tools) in sorted(selected.items())
    }
    result = {
        f".opencode/agents/{name}": hashlib.sha256(content).hexdigest()
        for name, content in rendered.items()
    }
    parent = root / ".opencode"
    if parent.is_symlink():
        raise ValueError("profile parent must not be a symlink")
    directory = parent / "agents"
    if directory.exists() or directory.is_symlink():
        _verify_existing(directory, rendered)
        return result
    if not rendered:
        return result

    with tempfile.TemporaryDirectory(
        prefix="opencode-profiles-", dir=root
    ) as temporary:
        staged = Path(temporary) / "agents"
        staged.mkdir()
        for name, content in rendered.items():
            (staged / name).write_bytes(content)
        parent.mkdir(exist_ok=True)
        if directory.exists() or directory.is_symlink():
            _verify_existing(directory, rendered)
        else:
            os.replace(staged, directory)
    return result


def export_probe_config(root: Path) -> str:
    """Freeze the V1 task config without credentials or provider endpoints."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("profile root must be a real directory")
    parent = root / ".opencode"
    if parent.is_symlink():
        raise ValueError("profile parent must not be a symlink")
    config = {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "default_agent": "orchestrator",
        "model": PROBE_MODEL,
        "small_model": PROBE_MODEL,
        "enabled_providers": ["litellm"],
        "agent": {
            **{name: {"disable": True} for name in _BUILTIN_DELEGATES},
            **{name: {"model": PROBE_MODEL} for name in _BUILTIN_HELPERS},
        },
    }
    content = (json.dumps(config, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    destination = parent / "opencode.json"
    if destination.exists() or destination.is_symlink():
        if (
            destination.is_symlink()
            or not destination.is_file()
            or destination.read_bytes() != content
        ):
            raise ValueError("existing probe config differs")
        return digest

    with tempfile.TemporaryDirectory(prefix="opencode-config-", dir=root) as temporary:
        staged = Path(temporary) / "opencode.json"
        staged.write_bytes(content)
        parent.mkdir(exist_ok=True)
        if destination.exists() or destination.is_symlink():
            if (
                destination.is_symlink()
                or not destination.is_file()
                or destination.read_bytes() != content
            ):
                raise ValueError("existing probe config differs")
        else:
            os.replace(staged, destination)
    return digest
