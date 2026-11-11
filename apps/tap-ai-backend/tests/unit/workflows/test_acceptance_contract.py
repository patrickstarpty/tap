"""User-reviewed inputs cannot be rewritten or omitted by the planner."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from tap.modules.workflows.application.bdd_executor import BDDPlan
from tap.modules.workflows.domain.acceptance_contract import BrowserAcceptanceContract

from .test_bdd_executor import plan_data


def contract_data():
    return {
        "version": 1,
        "reviewed": True,
        "fields": [
            {
                "id": "age",
                "value": "30",
                "readback_value": "30",
                "attribute": "value",
                "target_texts": ["Age"],
            }
        ],
    }


def test_valid_plan_binds_stable_ids_without_requiring_identical_prose():
    contract = BrowserAcceptanceContract.model_validate(contract_data())
    plan = BDDPlan.model_validate(plan_data())
    plan.validate_acceptance(contract)
    changed = contract_data()
    changed["fields"][0]["value"] = "31"
    assert contract.fields[0].value == "30"
    with pytest.raises(ValidationError):
        contract.fields[0].value = "31"


@pytest.mark.parametrize("change", ["value", "omitted", "name", "target", "scope", "attribute"])
def test_planner_cannot_replace_reviewed_requirements(change):
    data = deepcopy(plan_data())
    if change == "value":
        data["requirements"][0]["expected"] = "31"
        data["steps"][1]["value"] = "31"
        data["steps"][2]["assertion"]["expected"] = "31"
    elif change == "omitted":
        data["requirements"].pop(0)
        del data["steps"][1:3]
    elif change == "name":
        data["requirements"][0]["id"] = "guessed-age"
        for step in data["steps"][1:3]:
            step["requirement"] = "guessed-age"
    elif change == "target":
        data["steps"][2]["target_texts"].append("Premium")
    elif change == "scope":
        data["steps"][2]["scope_text"] = "Another person"
    else:
        data["steps"][2]["attribute"] = "text"
    with pytest.raises(ValueError, match="reviewed"):
        BDDPlan.model_validate(data).validate_acceptance(
            BrowserAcceptanceContract.model_validate(contract_data())
        )


def test_explicit_readback_format_is_user_owned_not_a_site_specific_coercion():
    data = contract_data()
    data["fields"][0].update(value="1996-11-08", readback_value="08/11/1996")
    plan = plan_data()
    plan["requirements"][0].update(expected="1996-11-08", readback_expected="08/11/1996")
    plan["steps"][1]["value"] = "1996-11-08"
    plan["steps"][2]["assertion"]["expected"] = "08/11/1996"
    BDDPlan.model_validate(plan).validate_acceptance(BrowserAcceptanceContract.model_validate(data))


@pytest.mark.parametrize("change", ["unreviewed", "empty", "duplicate", "boolean", "unknown"])
def test_contract_requires_explicit_review_and_closed_valid_fields(change):
    data = contract_data()
    if change == "unreviewed":
        data["reviewed"] = False
    elif change == "boolean":
        data["reviewed"] = 1
    elif change == "empty":
        data["fields"] = []
    elif change == "duplicate":
        data["fields"] *= 2
    else:
        data["fields"][0]["guessed"] = True
    with pytest.raises(ValueError):
        BrowserAcceptanceContract.model_validate(data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "omitted",
        "changed_expected",
        "unreadable",
        "unknown",
        "stale",
        "wrong_source",
        "wrong_target",
        "wrong_attribute",
        "mutated",
    ],
)
async def test_reviewed_input_cannot_pass_without_current_exact_read_proof(fault):
    progress, execute, read, state = await proof_fixture()
    if fault != "omitted":
        if fault == "changed_expected":
            read["equals"] = "31"
            state["value"] = "31"
        elif fault == "wrong_target":
            read["target_texts"] = ["Age", "Quote"]
        elif fault == "wrong_attribute":
            read["attribute"] = "text"
        else:
            state["fault"] = fault
        await progress.handle(read, "input", execute)
    if fault == "mutated":
        await progress.handle(
            {"type": "fill", "selector": "#age", "value": "31", "step": "act"}, "mutate", execute
        )
    state["fault"] = ""
    await progress.handle(
        {"type": "read", "selector": "#quote", "name": "quote", "positive": True, "step": "quote"},
        "quote",
        execute,
    )
    assert not progress.live_complete
    assert not progress.required_inputs_complete


async def proof_fixture(contract=None):
    from tap.modules.workflows.application.browser_steps import BrowserProgress

    async def emit(*_args):
        pass

    state = {"sequence": 0, "page": "https://example.test/form", "value": "30", "fault": ""}

    async def execute(action):
        state["sequence"] += 1
        if action["type"] == "navigate":
            state["page"] = action["url"]
            state["document_id"] = f"navigation-{state['sequence']}"
        if action["type"] == "fill":
            state["value"] = action["value"]
        seq = state["sequence"]
        fact = None
        if action["type"] == "read":
            fact = {
                "name": action["name"],
                "value": state["value"] if action["name"] == "age" else "42",
                "source_url": state.get("source_url", state["page"]),
                "source_frame_id": state.get("frame_id", "main"),
                "source_document_id": state.get("document_id", state["page"]),
                "observation_sequence": seq,
            }
            if state["fault"] == "wrong_source":
                fact["source_url"] = "https://example.test/previous"
            if state["fault"] == "stale":
                fact["observation_sequence"] = 0
            if state["fault"] == "unreadable":
                fact["value"] = None
        return {
            "status": "unknown" if state["fault"] == "unknown" else "completed",
            "action_id": f"receipt-{seq}",
            "observation": {
                "sequence": seq,
                "url": state["page"],
                "extracted": fact,
                "documents": [
                    {
                        "frame_id": state.get("frame_id", "main"),
                        "document_id": state.get("document_id", state["page"]),
                        "url": state.get("source_url", state["page"]),
                    }
                ],
            },
        }

    progress = BrowserProgress(
        emit, BrowserAcceptanceContract.model_validate(contract or contract_data())
    )
    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {
                    "id": "input",
                    "keyword": "Then",
                    "title": "Verify input",
                    "verification_kind": "input",
                },
                {"id": "act", "keyword": "When", "title": "Act"},
                {"id": "quote", "keyword": "Then", "title": "Result"},
            ],
        },
        "plan",
        execute,
    )
    read = {
        "type": "read",
        "step": "input",
        "selector": "#age",
        "name": "age",
        "attribute": "value",
        "equals": "30",
        "target_texts": ["Age"],
    }
    return progress, execute, read, state


@pytest.mark.asyncio
async def test_contract_proof_survives_successful_navigation_not_return_or_failed_read():
    progress, execute, read, state = await proof_fixture()
    await progress.handle(read, "input", execute)
    assert progress.required_inputs_complete
    await progress.handle(
        {"type": "navigate", "url": "https://example.test/result", "step": "act"}, "next", execute
    )
    await progress.handle(
        {"type": "read", "selector": "#quote", "name": "quote", "positive": True, "step": "quote"},
        "quote",
        execute,
    )
    assert progress.live_complete
    assert progress.input_proofs["input"]["observation_sequence"] == 1
    await progress.handle(
        {"type": "navigate", "url": "https://example.test/form", "step": "act"}, "back", execute
    )
    assert not progress.required_inputs_complete
    state["value"] = "31"
    assert await progress.refresh_required_inputs(execute, "final") == []
    assert not progress.required_inputs_complete


@pytest.mark.asyncio
async def test_input_recheck_after_other_field_write_is_preserved_before_leaving_page():
    progress, execute, read, state = await proof_fixture()
    await progress.handle(read, "input", execute)
    await progress.handle(
        {"type": "fill", "selector": "#other", "value": "30", "step": "act"}, "other", execute
    )
    assert not progress.required_inputs_complete
    actions = await progress.refresh_required_inputs(execute, "before-next")
    assert len(actions) == 1 and actions[0]["name"] == "age"
    assert progress.required_inputs_complete
    assert progress.input_proofs["input"]["observation_sequence"] == 3


@pytest.mark.asyncio
async def test_finish_must_match_the_exact_verified_receipt_not_only_value():
    progress, execute, read, state = await proof_fixture()
    await progress.handle(read, "input", execute)
    result = {
        "outputs": [
            {
                "age": "30",
                "provenance": {
                    "age": {
                        "source_url": state.get("source_url", state["page"]),
                        "source_frame_id": state.get("frame_id", "main"),
                        "source_document_id": state.get("document_id", state["page"]),
                        "observation_sequence": 1,
                    }
                },
            }
        ]
    }
    progress.verify_finished_result(
        result, {"documents": list(progress.documents.values()), "sequence": 2}
    )
    for key, wrong in [("observation_sequence", 0), ("source_url", "https://example.test/other")]:
        changed = deepcopy(result)
        changed["outputs"][0]["provenance"]["age"][key] = wrong
        with pytest.raises(ValueError, match="evidence"):
            progress.verify_finished_result(
                changed, {"documents": list(progress.documents.values()), "sequence": 2}
            )
    with pytest.raises(ValueError, match="provenance"):
        progress.verify_finished_result(
            {"outputs": [{"premium": "42"}]},
            {"documents": list(progress.documents.values()), "sequence": 2},
        )


def test_http_review_contract_and_frozen_replay_keep_original_values_and_digest():
    import hashlib
    import json
    from datetime import UTC, datetime

    from tap.contracts.http import BrowserTaskRequest
    from tap.contracts.workflows import WorkflowBrowserIntent
    from tap.modules.chat.domain.conversations import FrozenBrowserIntent
    from tap.modules.workflows.adapters.mysql import _frozen_browser_replay
    from tap.modules.workflows.domain.models import WorkflowEvent

    contract = BrowserAcceptanceContract.model_validate(contract_data())
    body = BrowserTaskRequest.model_validate(
        {
            "startUrl": "https://example.test/form",
            "acceptanceContract": contract.model_dump(mode="json", by_alias=True),
        }
    )
    assert body.acceptance_contract == contract
    data = {"acceptance_contract": body.acceptance_contract.model_dump(mode="json")}
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    frozen = FrozenBrowserIntent(
        "https://example.test/form", "Get quote", ("https://example.test",), canonical, digest
    )
    request = {"kind": "browser", "browser": {"input_data": data, "input_sha256": digest}}
    event = WorkflowEvent(
        "e",
        "task",
        1,
        "node",
        1,
        "browser.coding_result",
        {"bundle_ref": "bundle", "execution_ref": "execution"},
        datetime.now(UTC),
    )
    replay = _frozen_browser_replay(request, (event,))
    assert replay["browser"]["input_data"] == data
    assert replay["browser"]["input_sha256"] == frozen.input_sha256
    projected = WorkflowBrowserIntent(
        goal="Get quote",
        start_url=frozen.start_url,
        expected_result="Get quote",
        acceptance_contract=data["acceptance_contract"],
    )
    assert (
        projected.model_dump(by_alias=True)["acceptanceContract"]["fields"][0]["readbackValue"]
        == "30"
    )
    data["acceptance_contract"]["fields"][0]["readback_value"] = "31"
    with pytest.raises(ValueError, match="digest"):
        FrozenBrowserIntent(
            frozen.start_url,
            frozen.expected_result,
            frozen.allowed_origins,
            json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            digest,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["project_id", "task_id"])
async def test_reviewed_contract_rejects_foreign_session_before_actions(wrong):
    progress, _, _, _ = await proof_fixture()
    session = {
        "status": "active",
        "session_id": "session",
        "project_id": "project",
        "task_id": "task",
    }
    session[wrong] = "foreign"
    with pytest.raises(ValueError, match="provenance"):
        progress.bind_session(session, "project", "task")
    assert progress.session_id is None


@pytest.mark.asyncio
async def test_success_receipt_from_different_session_cannot_prove_reviewed_input():
    progress, execute, read, _ = await proof_fixture()
    progress.bind_session(
        {"status": "active", "session_id": "session", "project_id": "project", "task_id": "task"},
        "project",
        "task",
    )

    async def wrong(action):
        receipt = await execute(action)
        receipt["session_id"] = "another"
        receipt["observation"]["session_id"] = "another"
        return receipt

    await progress.handle(read, "read", wrong)
    assert progress.unknown_action
    assert not progress.required_inputs_complete
    assert not progress.input_proofs


@pytest.mark.parametrize("reviewed", [False, 1, "true", None])
def test_http_review_acknowledgment_must_be_literal_true(reviewed):
    from tap.contracts.http import BrowserTaskRequest

    data = contract_data()
    data["reviewed"] = reviewed
    with pytest.raises(ValidationError):
        BrowserTaskRequest.model_validate(
            {"startUrl": "https://example.test", "acceptanceContract": data}
        )


def test_large_review_contract_is_rejected_at_the_http_boundary():
    from tap.contracts.http import BrowserTaskRequest

    data = contract_data()
    data["fields"] = [
        {
            **data["fields"][0],
            "id": f"field_{i}",
            "value": "中" * 4000,
            "readback_value": "中" * 4000,
        }
        for i in range(20)
    ]
    with pytest.raises(ValidationError, match="32768"):
        BrowserTaskRequest.model_validate(
            {"startUrl": "https://example.test", "acceptanceContract": data}
        )


def test_readback_coercion_cannot_be_added_by_an_unreviewed_model_plan():
    data = plan_data()
    data["requirements"][0]["readback_expected"] = "31"
    data["steps"][2]["assertion"]["expected"] = "31"
    with pytest.raises(ValueError, match="reviewed"):
        BDDPlan.model_validate(data).validate_acceptance(None)


@pytest.mark.asyncio
async def test_data_active_proves_empty_present_attribute_but_not_missing_state():
    data = contract_data()
    data["fields"][0].update(attribute="data-active", readback_value="")
    progress, execute, read, state = await proof_fixture(data)
    read.update(attribute="data-active", equals="")
    state["value"] = ""
    await progress.handle(read, "selected", execute)
    assert progress.required_inputs_complete
    state["fault"] = "unreadable"  # Missing DOM attribute returns null, not empty string.
    await progress.handle(read, "not-selected", execute)
    assert not progress.required_inputs_complete


@pytest.mark.parametrize("name", ["source_frame_id", "source_document_id"])
def test_document_provenance_names_cannot_be_reviewed_output_ids(name):
    data = contract_data()
    data["fields"][0]["id"] = name
    with pytest.raises(ValueError, match="reserved"):
        BrowserAcceptanceContract.model_validate(data)
