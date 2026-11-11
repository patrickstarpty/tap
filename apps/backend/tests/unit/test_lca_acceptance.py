"""Current LCA acceptance must prove every immutable reviewed requirement."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from tap_platform.lca.acceptance import evaluate_contract
from tap_platform.lca.agent_export import export_agent_draft
from tap_platform.lca.service import LcaService
from tap_platform.lca.store import LcaStore, metadata
from test_lca_agent_export import fixture

CONTRACT = {
    "version": 1,
    "reviewed": True,
    "fields": [
        {
            "id": "Age-Years",
            "value": "30",
            "readback_value": "30",
            "attribute": "value",
            "target_texts": ["Age", "Applicant age"],
            "scope_text": "Applicant",
        }
    ],
}


def reviewed(tmp_path):
    browser, principal, body = fixture(tmp_path, {"acceptance_contract": CONTRACT})
    browser.store.update(
        "action",
        "project",
        "a1",
        request={
            "type": "read",
            "selector": "#age",
            "name": "Age-Years",
            "attribute": "value",
            "equals": "30",
            "target_texts": ["Age"],
            "scope_text": "Applicant",
        },
        observation={
            "sequence": 1,
            "url": "https://example.com",
            "error": None,
            "fields": [],
            "extracted": {
                "name": "Age-Years",
                "value": "30",
                "source_url": "https://example.com",
                "observation_sequence": 1,
            },
        },
    )
    exported = export_agent_draft(browser, principal, "run", body)
    assert exported["complete"]
    definition = exported["definition"]
    current = [
        {
            "step_id": definition["steps"][0]["id"],
            "status": "passed",
            "output": {"name": "Age-Years", "value": "30"},
        }
    ]
    return browser, principal, definition, current


def test_reviewed_contract_is_copied_from_frozen_inputs_without_changing_it(tmp_path):
    browser, principal, definition, current = reviewed(tmp_path)
    source = definition["source_acceptance"]
    assert source["acceptance_contract"] == CONTRACT
    assert source["source"]["execution_id"] == "run"
    assert source["input_sha256"] == source["source"]["input_sha256"]
    result = evaluate_contract(
        browser, "project", definition, {"status": "passed"}, current
    )
    assert result["status"] == "passed"
    assert result["fields"] == [{"id": "Age-Years", "status": "passed", "reason": ""}]


@pytest.mark.parametrize(
    "change",
    [
        "delete_read",
        "equals",
        "target",
        "guard",
        "missing_output",
        "wrong_value",
        "old_proof_only",
        "unknown",
        "forged_review",
        "other_bundle",
    ],
)
def test_technical_pass_cannot_upgrade_unproven_requirements(tmp_path, change):
    browser, _, definition, current = reviewed(tmp_path)
    execution = {"status": "passed"}
    if change == "delete_read":
        definition["steps"] = []
    if change == "equals":
        definition["steps"][0]["arguments"]["equals"] = "31"
    if change == "target":
        definition["targets"][0]["value"] = "#other-element-with-same-text"
    if change == "guard":
        definition["steps"][0].pop("target_contract")
    if change == "missing_output":
        current[0].pop("output")
    if change == "wrong_value":
        current[0]["output"]["value"] = "31"
    if change == "old_proof_only":
        current = None
    if change == "unknown":
        execution["status"] = "unknown"
    if change == "forged_review":
        definition["source_acceptance"]["acceptance_contract"]["fields"][0]["value"] = (
            "31"
        )
    if change == "other_bundle":
        definition["source_acceptance"]["source"]["bundle_id"] = "another"
    assert (
        evaluate_contract(browser, "project", definition, execution, current)["status"]
        == "not_proven"
    )


def test_legacy_is_unreviewed_not_contract_passed(tmp_path):
    browser, principal, body = fixture(tmp_path)
    projected = export_agent_draft(browser, principal, "run", body)
    assert projected["contract_review_status"] == "legacy_unreviewed"
    assert projected["acceptance_contract"] is None
    assert (
        evaluate_contract(
            browser, "project", projected["definition"], {"status": "passed"}, []
        )["status"]
        == "legacy_unreviewed"
    )


def test_saving_edits_cannot_erase_replace_or_forge_original_source_contract(tmp_path):
    browser, principal, definition, _ = reviewed(tmp_path)
    metadata.create_all(browser.store.engine)
    service = LcaService(LcaStore(browser.store.engine), browser)
    actor = replace(
        principal,
        principal_type="user",
        principal_id="alice",
        audience="tap-lca",
        actions=frozenset({"lca.flows.create", "lca.drafts.save", "lca.flows.read"}),
    )
    flow = service.create_flow(actor, "create", "Imported", definition)
    edited = deepcopy(definition)
    edited.pop("source_acceptance")
    edited["steps"][0]["arguments"]["equals"] = "31"
    saved = service.save_draft(actor, flow["id"], 1, edited)
    persisted = json.loads(saved["canonical_bytes"])
    assert persisted["source_acceptance"] == definition["source_acceptance"]
    assert persisted["steps"][0]["arguments"]["equals"] == "31"
    forged = deepcopy(persisted)
    forged["source_acceptance"]["acceptance_contract"]["fields"][0][
        "readback_value"
    ] = "31"
    with pytest.raises(ValueError):
        service.save_draft(actor, flow["id"], 2, forged)
    with pytest.raises(ValueError):
        service.create_flow(actor, "fake", "Forged", forged)
    with pytest.raises(ValueError):
        service.create_flow(
            replace(actor, project_id="foreign"), "foreign", "Foreign", definition
        )


def test_malformed_frozen_contract_never_becomes_a_reviewed_executable_import(tmp_path):
    browser, principal, body = fixture(
        tmp_path, {"acceptance_contract": {**CONTRACT, "reviewed": 1}}
    )
    result = export_agent_draft(browser, principal, "run", body)
    assert result["contract_review_status"] == "invalid"
    assert result["definition"] is None and not result["complete"]


@pytest.mark.parametrize("position", [0, 1])
def test_added_or_reordered_writes_cannot_reuse_obsolete_readback(tmp_path, position):
    browser, _, definition, current = reviewed(tmp_path)
    definition["steps"].insert(
        position,
        {
            "id": "new_write",
            "operation": "fill",
            "target_id": definition["steps"][0]["target_id"],
            "arguments": {"value": "31"},
            "timeout_ms": 1000,
        },
    )
    result = evaluate_contract(
        browser, "project", definition, {"status": "passed"}, current
    )
    assert result["status"] == "not_proven"
    assert "order changed" in result["fields"][0]["reason"]


def test_boolean_observation_sequence_cannot_prove_reviewed_field(tmp_path):
    browser, principal, definition, current = reviewed(tmp_path)
    row = browser.store.get("action", "project", "a1")
    observation = deepcopy(row["observation"])
    observation["extracted"]["observation_sequence"] = True
    browser.store.update("action", "project", "a1", observation=observation)
    assert (
        evaluate_contract(
            browser, "project", definition, {"status": "passed"}, current
        )["status"]
        == "not_proven"
    )
