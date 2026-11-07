"""Fail-closed OpenCode capability adjudication."""

import pytest
from scripts.opencode_probe.evaluate import (
    REQUIRED,
    evaluate_samples,
    ready_for_runtime,
    verified_provenance,
)
from scripts.opencode_probe.ledger import ProbeLedger
from scripts.opencode_probe.models import CapabilityResult
from scripts.opencode_probe.scenarios import _profiles, _safe_square_script, _square_output_matches


def test_unverified_event_identity_blocks_runtime() -> None:
    result = CapabilityResult("child_identity", "unverified", (), "未见子实例事件")
    assert not ready_for_runtime((result,), frozenset({"child_identity"}))


def test_missing_or_untrusted_evidence_blocks_runtime() -> None:
    missing = CapabilityResult("byok_all_calls", "supported", (), None)
    old = CapabilityResult("byok_all_calls", "supported", ("copilot:old",), None)
    assert not ready_for_runtime((missing,), frozenset({"byok_all_calls"}))
    assert not ready_for_runtime((old,), frozenset({"byok_all_calls"}))


def test_only_real_opencode_refs_can_satisfy_all_required() -> None:
    results = tuple(
        CapabilityResult(name, "supported", (f"opencode-real:sample:{name}",), None)
        for name in REQUIRED
    )
    assert ready_for_runtime(results, REQUIRED)
    assert not ready_for_runtime(results[:-1], REQUIRED)


def test_provider_provenance_needs_validated_calls_and_terminal() -> None:
    valid = {
        "source": "opencode-real",
        "cli_version": "1.19.5",
        "model": "litellm/qwen-plus",
        "config_digest": "a" * 64,
        "terminal": "complete",
        "gate": {
            "calls": 2,
            "records": [
                {"claimed_session_id": "ses_root", "usage_verified": True, "status": 200},
                {"claimed_session_id": "ses_child", "usage_verified": True, "status": 200},
            ],
        },
        "sessions": ["ses_root", "ses_child"],
    }
    assert verified_provenance(valid)
    assert not verified_provenance({**valid, "source": "copilot"})
    assert not verified_provenance({**valid, "terminal": "incomplete"})
    assert not verified_provenance({**valid, "gate": {**valid["gate"], "calls": 3}})


def test_ledger_preserves_cumulative_unknown_reservation(tmp_path) -> None:
    ledger = ProbeLedger(tmp_path / "ledger.jsonl")
    ledger.append("simple-question", 2, 0.04, 0.06)
    ledger.append("parallel", 1, 0.0, 0.06)
    assert ledger.totals() == (3, 0.16)
    assert len((tmp_path / "ledger.jsonl").read_text().splitlines()) == 2


def test_ledger_charges_inflight_full_cap_until_settlement(tmp_path) -> None:
    ledger = ProbeLedger(tmp_path / "ledger.jsonl")
    reservation = ledger.reserve("simple-question", 10, 0.50)
    assert ledger.totals() == (10, 0.50)
    ledger.settle(reservation, 2, 0.07)
    assert ledger.totals() == (2, 0.07)


def test_ledger_rejects_settlement_larger_than_reservation(tmp_path) -> None:
    ledger = ProbeLedger(tmp_path / "ledger.jsonl")
    reservation = ledger.reserve("simple-question", 2, 0.20)
    with pytest.raises(ValueError):
        ledger.settle(reservation, 3, 0.20)
    with pytest.raises(ValueError):
        ledger.settle(reservation, 2, 0.21)
    assert ledger.totals() == (2, 0.20)


def test_square_artifact_check_rejects_imports_and_accepts_bounded_prints() -> None:
    assert _safe_square_script("print(2 ** 2)\nprint(3 ** 2)\n")
    assert not _safe_square_script("import os\nprint(4)\n")


def test_roster_profiles_have_exact_allowed_sizes() -> None:
    for count in (5, 25, 100):
        profiles = _profiles(count)
        assert len(profiles) == count
        assert len({profile.agent_id for profile in profiles}) == count
        assert {profile.agent_id for profile in profiles} >= {"orchestrator", "sde", "sdet", "qae"}


def test_square_output_accepts_labelled_values_without_accepting_wrong_results() -> None:
    assert _square_output_matches("2 squared = 4\n3 squared = 9\n")
    assert _square_output_matches("4 9\n")
    assert not _square_output_matches("2 squared = 5\n3 squared = 9\n")


def test_real_fixture_matrix_requires_execution_side_proof() -> None:
    import json
    from pathlib import Path

    directory = Path(__file__).resolve().parents[2] / "fixtures/opencode/real"
    samples = {path.stem: json.loads(path.read_text()) for path in directory.glob("*.json")}
    results = {result.name: result for result in evaluate_samples(samples)}
    assert results["child_identity"].status == "supported"
    assert results["parallel"].status == "supported"
    assert results["control_receipt"].status == "supported"
    assert results["reconcile"].status == "supported"
    assert results["permissions"].status == "unverified"
    assert results["resume_workspace"].status == "unverified"
    assert results["budget_enforcement"].status == "unverified"
    assert not ready_for_runtime(tuple(results.values()), REQUIRED)
