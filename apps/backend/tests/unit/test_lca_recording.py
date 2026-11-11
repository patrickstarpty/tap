"""Only acknowledged, ordered human actions may become replay steps."""

from copy import deepcopy

import pytest

from tap_platform.lca.recording import project_recording


def receipt(sequence=1, operation="fill", status="completed"):
    return {
        "schema_version": "browser-control-v1",
        "command_id": f"cmd-{sequence}",
        "action_id": f"act-{sequence}",
        "sequence": sequence,
        "actor_kind": "human",
        "control_epoch": 1,
        "status": status,
        "operation": operation,
        "pre_observation_sequence": sequence - 1,
        "target": {"kind": "test_id", "value": "name", "fallback": False},
        "args": {"value": "Alice"} if operation == "fill" else {},
        "post_observation_sequence": sequence,
        "evidence_refs": [],
    }


def test_orders_deduplicates_and_keeps_recorded_action_identity():
    first, second = receipt(), receipt(2, "click")
    projected = project_recording(
        [second, first, deepcopy(first)], ["https://fixture.example"], 2
    )
    assert projected.ready
    assert [s.operation for s in projected.definition.steps] == ["fill", "click"]
    assert [s.provenance["action_id"] for s in projected.definition.steps] == [
        "act-1",
        "act-2",
    ]
    assert len(projected.definition.targets) == 2
    assert projected.excluded == ()


def test_pending_before_stop_watermark_blocks_freezing():
    projected = project_recording([receipt()], ["https://fixture.example"], 2)
    assert not projected.ready
    assert projected.missing_sequences == (2,)


def test_unknown_failure_and_unsupported_actions_remain_explicit():
    projected = project_recording(
        [receipt(status="unknown"), receipt(2, status="failed"), receipt(3, "press")],
        ["https://fixture.example"],
        3,
    )
    assert projected.ready
    assert projected.definition is None
    assert [item.reason for item in projected.excluded] == [
        "unknown",
        "failed",
        "unsupported_operation",
    ]


def test_late_receipt_past_watermark_is_not_added_to_frozen_projection():
    projected = project_recording(
        [receipt(), receipt(2, "click")], ["https://fixture.example"], 1
    )
    assert len(projected.definition.steps) == 1


def test_changed_duplicate_receipt_is_rejected():
    changed = receipt()
    changed["args"] = {"value": "Mallory"}
    with pytest.raises(ValueError, match="conflicting"):
        project_recording([receipt(), changed], ["https://fixture.example"], 1)


@pytest.mark.parametrize(
    "field,value",
    [("actor_kind", "agent"), ("sequence", True), ("schema_version", "other")],
)
def test_rejects_untrusted_receipt_shape(field, value):
    invalid = receipt()
    invalid[field] = value
    with pytest.raises(ValueError):
        project_recording([invalid], ["https://fixture.example"], 1)


@pytest.mark.parametrize(
    "field",
    [
        "command_id",
        "control_epoch",
        "pre_observation_sequence",
        "post_observation_sequence",
        "evidence_refs",
    ],
)
def test_rejects_incomplete_terminal_contract(field):
    incomplete = receipt()
    incomplete.pop(field)
    with pytest.raises(ValueError):
        project_recording([incomplete], ["https://fixture.example"], 1)


def test_preserves_receipt_control_and_observation_provenance():
    projected = project_recording([receipt()], ["https://fixture.example"], 1)
    assert projected.definition.steps[0].provenance == {
        "action_id": "act-1",
        "sequence": "1",
        "command_id": "cmd-1",
        "control_epoch": "1",
        "pre_observation_sequence": "0",
        "post_observation_sequence": "1",
        "evidence_refs": "[]",
    }


def test_shared_receipt_evidence_objects_preserve_lineage():
    acknowledged = receipt()
    acknowledged["evidence_refs"] = [
        {
            "kind": "screenshot",
            "object_ref": "evidence/screen.png",
            "sha256": "a" * 64,
            "size_bytes": 12,
        }
    ]
    projected = project_recording([acknowledged], ["https://fixture.example"], 1)
    assert (
        '"object_ref":"evidence/screen.png"'
        in projected.definition.steps[0].provenance["evidence_refs"]
    )


def test_failed_receipt_without_observation_is_excluded_not_executable():
    failed = receipt(status="failed")
    failed.update(
        pre_observation_sequence=None, post_observation_sequence=None, target=None
    )
    projected = project_recording([failed], ["https://fixture.example"], 1)
    assert projected.ready and projected.definition is None
    assert projected.excluded[0].reason == "failed"


def test_read_only_preflight_is_explicitly_excluded_without_action_provenance():
    read = receipt(operation="preflight")
    read.update(
        pre_observation_sequence=None, post_observation_sequence=None, target=None
    )
    projected = project_recording([read], ["https://fixture.example"], 1)
    assert projected.ready and projected.definition is None
    assert projected.excluded[0].reason == "unsupported_operation"


@pytest.mark.parametrize(
    "evidence",
    [
        ["object-ref"],
        [{"kind": "screenshot"}],
        [
            {
                "kind": "screenshot",
                "object_ref": "evidence/x",
                "sha256": "z" * 64,
                "size_bytes": 1,
            }
        ],
    ],
)
def test_completed_action_rejects_invalid_shared_evidence(evidence):
    acknowledged = receipt()
    acknowledged["evidence_refs"] = evidence
    with pytest.raises(ValueError, match="provenance"):
        project_recording([acknowledged], ["https://fixture.example"], 1)


def test_zero_action_stop_is_empty_completed_projection():
    projected = project_recording([], ["https://fixture.example"], 3, start_sequence=4)
    assert projected.ready and projected.definition is None
    assert projected.excluded == ()
