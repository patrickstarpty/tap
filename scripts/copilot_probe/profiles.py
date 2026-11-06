"""Export task-scoped Copilot CLI agent profiles without broad defaults."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from scripts.copilot_probe.models import ProbeAgent

_AGENT_ID = re.compile(r"[a-z0-9][a-z0-9_-]*\Z", re.ASCII)
_TOOL_GROUPS = frozenset({"read", "grep", "glob", "edit", "execute"})


def _render(agent: ProbeAgent) -> bytes:
    if not _AGENT_ID.fullmatch(agent.agent_id):
        raise ValueError(f"invalid agent ID: {agent.agent_id!r}")
    if not agent.description.strip():
        raise ValueError(f"agent {agent.agent_id!r} has no description")
    unknown = set(agent.tools) - _TOOL_GROUPS
    if unknown:
        raise ValueError(
            f"agent {agent.agent_id!r} has unknown tools: {sorted(unknown)!r}"
        )

    description = json.dumps(agent.description, ensure_ascii=False)
    revision = json.dumps(agent.revision_id, ensure_ascii=False)
    tools = json.dumps(list(agent.tools), ensure_ascii=False)
    content = (
        f"---\nname: {agent.agent_id}\ndescription: {description}\n"
        f"tools: {tools}\nstrict-tools-list: true\n# revision: {revision}\n"
        f"---\n\n{agent.instruction}\n"
    )
    return content.encode("utf-8")


def _verify_existing(directory: Path, expected: dict[str, bytes]) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("existing profile snapshot is not a directory")
    actual_names = {entry.name for entry in directory.iterdir()}
    if actual_names != set(expected):
        raise ValueError("existing profile snapshot has a different file set")
    for name, content in expected.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"existing profile snapshot differs: {name}")


def export_profiles(
    root: Path, agents: tuple[ProbeAgent, ...], allowed_ids: frozenset[str]
) -> dict[str, str]:
    """Freeze allowed agent revisions under ``root/.github/agents``.

    An existing snapshot is verified byte for byte and never replaced. The
    caller must treat ``root`` as a task-owned, isolated workspace.
    """
    if root.is_symlink():
        raise ValueError("profile root must not be a symlink")

    seen: set[str] = set()
    rendered: dict[str, bytes] = {}
    for agent in agents:
        if agent.agent_id in seen:
            raise ValueError(f"duplicate agent ID: {agent.agent_id!r}")
        seen.add(agent.agent_id)
        content = _render(agent)
        if agent.enabled and agent.agent_id in allowed_ids:
            rendered[f"{agent.agent_id}.agent.md"] = content

    parent = root / ".github"
    if parent.is_symlink():
        raise ValueError("profile parent must not be a symlink")
    directory = parent / "agents"
    result = {
        f".github/agents/{name}": hashlib.sha256(content).hexdigest()
        for name, content in sorted(rendered.items())
    }
    if directory.exists() or directory.is_symlink():
        _verify_existing(directory, rendered)
        return result
    if not rendered:
        return result

    with tempfile.TemporaryDirectory(prefix="copilot-profiles-", dir=root) as temporary:
        staged = Path(temporary) / "agents"
        staged.mkdir()
        for name, content in rendered.items():
            (staged / name).write_bytes(content)
        directory.parent.mkdir(exist_ok=True)
        if directory.exists() or directory.is_symlink():
            _verify_existing(directory, rendered)
        else:
            os.replace(staged, directory)
    return result
