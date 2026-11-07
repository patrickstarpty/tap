"""Frozen OpenCode V1 agent profile export."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
import yaml
from scripts.opencode_probe.ledger import ProbeLedger
from scripts.opencode_probe.models import ProbeAgent
from scripts.opencode_probe.profiles import export_probe_config, export_profiles


def agent(
    agent_id: str,
    *,
    revision_id: str = "r1",
    description: str = "负责审查",
    instruction: str = "核对证据",
    tools: tuple[str, ...] = ("read",),
    enabled: bool = True,
) -> ProbeAgent:
    return ProbeAgent(agent_id, revision_id, description, instruction, tools, enabled)


def profile(root: Path, agent_id: str) -> tuple[dict[str, object], str]:
    content = (root / f".opencode/agents/{agent_id}.md").read_text()
    frontmatter, body = content.split("\n---\n\n", 1)
    return yaml.safe_load(frontmatter.removeprefix("---\n")), body


def test_continuation_grant_is_append_only_idempotent_and_preserves_history(tmp_path: Path) -> None:
    ledger = ProbeLedger(tmp_path / "ledger.jsonl")
    ledger.append("prior", 117, 0.1496368, 0.0)
    with pytest.raises(ValueError, match="budget"):
        ledger.reserve("old-cap", 4, 0.01)
    before = ledger.path.read_bytes()
    assert ledger.authorize_continuation("browser-e2e-2026-11-07", 120, 2.0) == (
        237,
        2.1496368,
    )
    granted = ledger.path.read_bytes()
    assert granted.startswith(before)
    assert ledger.authorize_continuation("browser-e2e-2026-11-07", 120, 2.0) == (
        237,
        2.1496368,
    )
    assert ledger.path.read_bytes() == granted
    assert ledger.totals() == (117, 0.1496368)
    assert ledger.reserve("next", 120, 2.0)
    with pytest.raises(ValueError, match="budget"):
        ledger.reserve("over", 1, 0.01)


def test_continuation_rejects_conflict_pending_and_invalid_grants(tmp_path: Path) -> None:
    ledger = ProbeLedger(tmp_path / "ledger.jsonl")
    ledger.append("prior", 117, 0.1496368, 0.0)
    ledger.authorize_continuation("first", 120, 2.0)
    with pytest.raises(ValueError, match="conflict"):
        ledger.authorize_continuation("first", 121, 2.0)
    reservation = ledger.reserve("inflight", 1, 0.1)
    with pytest.raises(ValueError, match="pending"):
        ledger.authorize_continuation("second", 120, 2.0)
    ledger.settle(reservation, 0, 0.0)
    with pytest.raises(ValueError, match="invalid"):
        ledger.authorize_continuation("", 120, 2.0)
    with pytest.raises(ValueError, match="invalid"):
        ledger.authorize_continuation("second", 0, 2.0)


def test_continuation_replaces_old_balance_and_serializes_reservations(tmp_path: Path) -> None:
    ledger = ProbeLedger(tmp_path / "ledger.jsonl")
    ledger.append("prior", 117, 0.1496368, 0.0)
    assert ledger.authorize_continuation("small", 1, 0.1) == (118, 0.2496368)

    start = Barrier(2)

    def reserve_one(_: int) -> str:
        start.wait(timeout=2)
        try:
            return ledger.reserve("parallel", 1, 0.1)
        except ValueError:
            return "ValueError"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve_one, range(2)))
    assert sum(outcome.startswith("res_") for outcome in outcomes) == 1
    assert outcomes.count("ValueError") == 1
    assert ledger.totals() == (118, 0.2496368)


def test_exports_all_enabled_allowed_agents_without_prefix(tmp_path: Path) -> None:
    agents = tuple(agent(x) for x in ("orchestrator", "qae", "sdet"))
    files = export_profiles(tmp_path, agents, frozenset({"orchestrator", "qae"}))
    assert set(files) == {".opencode/agents/orchestrator.md", ".opencode/agents/qae.md"}
    for relative, digest in files.items():
        assert hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest() == digest
    assert profile(tmp_path, "orchestrator")[0]["mode"] == "primary"
    assert profile(tmp_path, "qae")[0]["mode"] == "subagent"


def test_disabled_and_unauthorized_agents_are_not_exported(tmp_path: Path) -> None:
    files = export_profiles(tmp_path, (agent("ba", enabled=False), agent("sde")), frozenset({"ba"}))
    assert files == {}
    assert not (tmp_path / ".opencode/agents").exists()


@pytest.mark.parametrize("agent_id", ("../escape", "nested/agent", "Bad", "_bad", "", "a.b"))
def test_invalid_agent_id_is_rejected(tmp_path: Path, agent_id: str) -> None:
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent(agent_id),), frozenset({agent_id}))
    assert not (tmp_path / ".opencode/agents").exists()


@pytest.mark.parametrize(
    "agent_id", ("build", "plan", "general", "explore", "compaction", "summary", "title")
)
def test_builtin_agent_id_collision_is_rejected(tmp_path: Path, agent_id: str) -> None:
    with pytest.raises(ValueError, match="reserved"):
        export_profiles(tmp_path, (agent(agent_id),), frozenset({agent_id}))
    assert not (tmp_path / ".opencode/agents").exists()


def test_duplicate_or_blank_description_is_rejected_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        export_profiles(tmp_path, (agent("qae"), agent("qae")), frozenset({"qae"}))
    with pytest.raises(ValueError, match="description"):
        export_profiles(tmp_path, (agent("qae", description="  \n"),), frozenset({"qae"}))
    assert not (tmp_path / ".opencode/agents").exists()


def test_description_revision_and_body_are_safe_and_preserved(tmp_path: Path) -> None:
    description = 'review: "evidence"\npermission: allow'
    revision = "r1\npermission: allow"
    instruction = "first\n---\nlast"
    export_profiles(
        tmp_path,
        (agent("qae", description=description, revision_id=revision, instruction=instruction),),
        frozenset({"qae"}),
    )
    metadata, body = profile(tmp_path, "qae")
    assert metadata["description"] == description
    assert metadata["permission"]["*"] == "deny"
    assert body == instruction + "\n"


@pytest.mark.parametrize("agent_id", ("123", "true", "null"))
def test_yaml_sensitive_ids_remain_strings(tmp_path: Path, agent_id: str) -> None:
    export_profiles(tmp_path, (agent(agent_id),), frozenset({agent_id}))
    metadata, _ = profile(tmp_path, agent_id)
    assert metadata["name"] == agent_id


def test_permission_allowlist_and_readonly_qae(tmp_path: Path) -> None:
    export_profiles(
        tmp_path,
        (
            agent("orchestrator", tools=("read", "task")),
            agent("qae", tools=("read", "grep", "glob")),
            agent("sde", tools=("read", "edit", "bash")),
        ),
        frozenset({"orchestrator", "qae", "sde"}),
    )
    root_permission = profile(tmp_path, "orchestrator")[0]["permission"]
    assert root_permission["*"] == "deny"
    assert root_permission["read"] == {"*": "allow", "*.env": "deny", "*.env.*": "deny"}
    assert root_permission["task"] == {"*": "deny", "qae": "allow", "sde": "allow"}
    qae_permission = profile(tmp_path, "qae")[0]["permission"]
    assert qae_permission["*"] == "deny"
    assert qae_permission["edit"] == "deny"
    assert qae_permission["bash"] == "deny"
    assert qae_permission["task"] == "deny"
    assert profile(tmp_path, "sde")[0]["permission"]["edit"] == "allow"


@pytest.mark.parametrize("bad_tools", (("execute",), ("shell",), ("read", "unknown"), ("bash",)))
def test_unknown_or_qae_write_tool_fails_closed(tmp_path: Path, bad_tools: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent("qae", tools=bad_tools),), frozenset({"qae"}))
    assert not (tmp_path / ".opencode/agents").exists()


def test_export_is_deterministic_and_existing_snapshot_is_never_rewritten(tmp_path: Path) -> None:
    agents = (agent("qae", revision_id="r1"), agent("orchestrator", tools=("task",)))
    allowed = frozenset({"qae", "orchestrator"})
    first = export_profiles(tmp_path, agents, allowed)
    existing = tmp_path / ".opencode/agents/qae.md"
    before = existing.stat().st_mtime_ns
    second = export_profiles(tmp_path, tuple(reversed(agents)), allowed)
    assert second == first
    assert existing.stat().st_mtime_ns == before
    with pytest.raises(ValueError, match="differs"):
        export_profiles(
            tmp_path,
            (agent("qae", revision_id="r2"), agent("orchestrator", tools=("task",))),
            allowed,
        )
    assert existing.stat().st_mtime_ns == before


def test_validation_failure_does_not_leave_partial_snapshot(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_profiles(
            tmp_path, (agent("qae"), agent("sde", tools=("execute",))), frozenset({"qae", "sde"})
        )
    assert not (tmp_path / ".opencode/agents").exists()


def test_symlink_parent_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".opencode").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        export_profiles(tmp_path, (agent("qae"),), frozenset({"qae"}))
    assert not list(outside.iterdir())


def test_probe_config_disables_unapproved_builtins_and_routes_helpers(tmp_path: Path) -> None:
    digest = export_probe_config(tmp_path)
    path = tmp_path / ".opencode/opencode.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    config = json.loads(path.read_text())
    assert config["autoupdate"] is False
    assert config["default_agent"] == "orchestrator"
    assert config["model"] == "litellm/qwen-plus"
    assert config["small_model"] == "litellm/qwen-plus"
    assert config["enabled_providers"] == ["litellm"]
    assert {name for name, value in config["agent"].items() if value.get("disable")} == {
        "build",
        "plan",
        "general",
        "explore",
    }
    assert {config["agent"][name]["model"] for name in ("compaction", "summary", "title")} == {
        "litellm/qwen-plus"
    }


def test_probe_config_is_frozen_and_never_rewritten(tmp_path: Path) -> None:
    first = export_probe_config(tmp_path)
    path = tmp_path / ".opencode/opencode.json"
    before = path.stat().st_mtime_ns
    assert export_probe_config(tmp_path) == first
    assert path.stat().st_mtime_ns == before
    path.write_text("{}")
    with pytest.raises(ValueError, match="differs"):
        export_probe_config(tmp_path)
