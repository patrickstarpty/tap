"""Frozen, permission-bounded Copilot profile export."""

import hashlib
from pathlib import Path

import pytest
from scripts.copilot_probe.models import ProbeAgent
from scripts.copilot_probe.profiles import export_profiles


def agent(
    agent_id: str,
    *,
    description: str = "负责审查",
    tools: tuple[str, ...] = ("read",),
    enabled: bool = True,
    revision_id: str = "r1",
    instruction: str = "核对证据",
) -> ProbeAgent:
    return ProbeAgent(agent_id, revision_id, description, instruction, tools, enabled)


def test_profiles_include_all_allowed_agents_without_prefix(tmp_path: Path) -> None:
    agents = tuple(agent(x) for x in ("orchestrator", "qae", "sdet"))
    files = export_profiles(tmp_path, agents, frozenset({"orchestrator", "qae"}))
    assert set(files) == {".github/agents/orchestrator.agent.md", ".github/agents/qae.agent.md"}
    for relative, digest in files.items():
        data = (tmp_path / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest


def test_disabled_and_unauthorized_agents_are_not_exported(tmp_path: Path) -> None:
    files = export_profiles(tmp_path, (agent("ba", enabled=False), agent("sde")), frozenset({"ba"}))
    assert files == {}
    assert not (tmp_path / ".github/agents").exists()


@pytest.mark.parametrize("agent_id", ("../escape", "nested/agent", "Bad", "_bad", "", "a.b"))
def test_invalid_agent_id_is_rejected(tmp_path: Path, agent_id: str) -> None:
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent(agent_id),), frozenset({agent_id}))
    assert not (tmp_path / ".github/agents").exists()


def test_duplicate_agent_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent("qae"), agent("qae")), frozenset({"qae"}))
    assert not (tmp_path / ".github/agents").exists()


def test_blank_description_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent("qae", description="  \n"),), frozenset({"qae"}))


def test_yaml_description_and_body_newlines_are_encoded_safely(tmp_path: Path) -> None:
    export_profiles(
        tmp_path,
        (agent("qae", description='review: "evidence"\nnext', instruction="first\n---\nlast"),),
        frozenset({"qae"}),
    )
    content = (tmp_path / ".github/agents/qae.agent.md").read_text()
    assert 'description: "review: \\"evidence\\"\\nnext"' in content
    assert content.startswith("---\n")
    assert content.count("\n---\n") == 2
    assert "first\n---\nlast" in content


def test_empty_tools_are_explicitly_denied(tmp_path: Path) -> None:
    export_profiles(tmp_path, (agent("qae", tools=()),), frozenset({"qae"}))
    content = (tmp_path / ".github/agents/qae.agent.md").read_text()
    assert "tools: []\n" in content
    assert "strict-tools-list: true\n" in content


def test_unknown_tool_is_rejected_without_partial_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_profiles(
            tmp_path, (agent("qae"), agent("sdet", tools=("*",))), frozenset({"qae", "sdet"})
        )
    assert not (tmp_path / ".github/agents").exists()


def test_existing_snapshot_is_verified_without_rewrite(tmp_path: Path) -> None:
    agents = (agent("qae"),)
    first = export_profiles(tmp_path, agents, frozenset({"qae"}))
    profile = tmp_path / ".github/agents/qae.agent.md"
    before = profile.stat().st_mtime_ns
    bytes_before = profile.read_bytes()
    second = export_profiles(tmp_path, agents, frozenset({"qae"}))
    assert first == second
    assert profile.stat().st_mtime_ns == before
    assert profile.read_bytes() == bytes_before


def test_changed_revision_cannot_replace_frozen_snapshot(tmp_path: Path) -> None:
    export_profiles(tmp_path, (agent("qae"),), frozenset({"qae"}))
    profile = tmp_path / ".github/agents/qae.agent.md"
    before = profile.read_bytes()
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent("qae", revision_id="r2"),), frozenset({"qae"}))
    assert profile.read_bytes() == before


def test_symlinked_agents_parent_cannot_escape_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "work"
    root.mkdir()
    (root / ".github").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        export_profiles(root, (agent("qae"),), frozenset({"qae"}))
    assert list(outside.iterdir()) == []


def test_symlinked_root_cannot_export_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "linked-workspace"
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        export_profiles(root, (agent("qae"),), frozenset({"qae"}))

    assert list(outside.iterdir()) == []
