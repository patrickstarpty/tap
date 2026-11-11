"""Reviewed inputs require actual frame/document identity, not outer page URLs."""

from copy import deepcopy

import pytest

from .test_acceptance_contract import proof_fixture


async def frame_fixture():
    progress, execute, read, state = await proof_fixture()
    state.update(frame_id="child", document_id="form-doc", source_url="https://frame.test/form")
    await progress.handle(read, "read", execute)
    assert progress.required_inputs_complete
    return progress, execute, read, state


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed", [False, True])
async def test_pre_navigation_input_proof_survives_real_frame_document_transfer(delayed):
    progress, execute, _, state = await frame_fixture()

    async def click(action):
        if not delayed:
            state.update(document_id="result-doc", source_url="https://frame.test/result")
        return await execute(action)

    await progress.handle({"type": "click", "selector": "#submit", "step": "act"}, "click", click)
    if delayed:
        # The automatic post-click read is fresh; old pre-click proof alone never passes.
        assert progress.required_inputs_complete
        assert progress.input_proofs["input"]["observation_sequence"] == 3
        assert progress.pending_proofs
        state.update(document_id="result-doc", source_url="https://frame.test/result")
        await progress.handle(
            {"type": "wait", "selector": "#result", "step": "act"}, "wait", execute
        )
    assert progress.required_inputs_complete
    # Old iframe references must not be reread on result page with the same outer URL.
    assert await progress.refresh_required_inputs(execute, "finish") == []
    assert progress.input_proofs["input"]["source_document_id"] == "form-doc"
    assert progress.input_proofs["input"]["departure_document_id"] == "result-doc"


@pytest.mark.asyncio
@pytest.mark.parametrize("write", ["fill", "select", "press", "navigate"])
async def test_another_write_cannot_resolve_pending_or_archive_stale_input(write):
    progress, execute, _, state = await frame_fixture()
    await progress.handle({"type": "click", "selector": "#submit", "step": "act"}, "click", execute)
    await progress.handle(
        {"type": write, "selector": "#age", "value": "31", "url": state["page"], "step": "act"},
        "write",
        execute,
    )
    state.update(document_id="result-doc", source_url="https://frame.test/result")
    await progress.handle({"type": "wait", "selector": "#result", "step": "act"}, "wait", execute)
    assert not progress.required_inputs_complete
    assert not progress.input_proofs


@pytest.mark.asyncio
async def test_same_document_requires_fresh_read_and_changed_read_cannot_be_revived():
    progress, execute, read, state = await frame_fixture()
    await progress.handle({"type": "click", "selector": "#reset", "step": "act"}, "reset", execute)
    await progress.handle({"type": "wait", "selector": "#age", "step": "act"}, "wait", execute)
    assert progress.required_inputs_complete
    assert progress.input_proofs["input"]["observation_sequence"] == 3
    state["value"] = "31"
    await progress.handle(read, "changed-read", execute)
    state.update(document_id="result-doc", source_url="https://frame.test/result")
    await progress.handle(
        {"type": "wait", "selector": "#result", "step": "act"}, "navigate", execute
    )
    assert not progress.required_inputs_complete


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["missing", "detached", "wrong_frame", "wrong_doc", "wrong_url", "duplicate"]
)
async def test_missing_or_changed_document_identity_cannot_prove_a_read(fault):
    progress, execute, read, _ = await frame_fixture()

    async def corrupt(action):
        receipt = await execute(action)
        observation = receipt["observation"]
        if fault == "missing":
            observation.pop("documents")
        elif fault == "detached":
            observation["documents"] = []
        elif fault == "duplicate":
            observation["documents"] *= 2
        else:
            observation["extracted"][
                {
                    "wrong_frame": "source_frame_id",
                    "wrong_doc": "source_document_id",
                    "wrong_url": "source_url",
                }[fault]
            ] = "forged"
        return receipt

    await progress.handle(read, "read", corrupt)
    assert not progress.required_inputs_complete


@pytest.mark.asyncio
async def test_frame_loss_is_not_navigation_and_unrelated_passive_navigation_is_not_submission():
    for pending in (False, True):
        progress, execute, _, state = await frame_fixture()
        if pending:
            await progress.handle(
                {"type": "click", "selector": "#submit", "step": "act"}, "click", execute
            )
            state["frame_id"] = "replacement-frame"
        state.update(document_id="result-doc", source_url="https://frame.test/result")
        await progress.handle(
            {"type": "wait", "selector": "#result", "step": "act"}, "wait", execute
        )
        assert not progress.required_inputs_complete


@pytest.mark.asyncio
async def test_finish_rejects_same_url_reload_after_last_input_verification():
    progress, _, _, _ = await frame_fixture()
    documents = list(progress.documents.values())
    result = {
        "outputs": [
            {
                "age": "30",
                "provenance": {
                    "age": {
                        "source_url": "https://frame.test/form",
                        "source_frame_id": "child",
                        "source_document_id": "form-doc",
                        "observation_sequence": 1,
                    }
                },
            }
        ]
    }
    progress.verify_finished_result(result, {"sequence": 2, "documents": documents})
    changed = deepcopy(documents)
    changed[0]["document_id"] = "same-url-new-document"
    with pytest.raises(ValueError, match="evidence"):
        progress.verify_finished_result(result, {"sequence": 2, "documents": changed})


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["changed", "unreadable", "navigated"])
async def test_click_mutation_or_unreadable_postclick_state_cannot_restore_old_proof(fault):
    progress, execute, _, state = await frame_fixture()

    async def mutate(action):
        if action["type"] == "click" and fault == "changed":
            state["value"] = "31"
        if action["type"] == "read":
            if fault == "unreadable":
                state["fault"] = "unreadable"
            elif fault == "navigated":
                state.update(document_id="result-doc", source_url="https://frame.test/result")
                state["fault"] = "unreadable"
        return await execute(action)

    with pytest.raises(ValueError, match="after click"):
        await progress.handle(
            {"type": "click", "selector": "#submit", "step": "act"}, "click", mutate
        )
    state.update(document_id="result-doc", source_url="https://frame.test/result", fault="")
    await progress.handle({"type": "wait", "selector": "#result", "step": "act"}, "wait", execute)
    assert not progress.required_inputs_complete
    assert not progress.input_proofs
