import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from tap.quality import evidence as quality_evidence

ROOT = Path(__file__).resolve().parents[4]


def _batch() -> tuple[dict, dict[str, object], dict[str, object], dict[str, object]]:
    module = quality_evidence
    dataset = {"version": "dataset-v1", "caseIds": ["case-1", "case-2"]}
    config = {"profile": "quality-v1", "threshold": 90}
    model = {
        "alias": "qwen-plus",
        "provider": "provider",
        "model": "model",
        "promptDigest": "sha256:" + "a" * 64,
        "schemaDigest": "sha256:" + "b" * 64,
    }
    dataset_digest = module.canonical_digest(dataset)
    config_digest = module.canonical_digest(config)
    model_digest = module.canonical_digest(model)
    cases = []
    for index, case_id in enumerate(dataset["caseIds"], start=1):
        output = {"answer": f"output-{index}", "citations": [f"citation-{index}"]}
        output_digest = module.canonical_digest(output)
        request_digest = module.canonical_digest(
            {
                "caseId": case_id,
                "datasetDigest": dataset_digest,
                "configDigest": config_digest,
                "modelDigest": model_digest,
            }
        )
        request_id = f"provider-request-{index}"
        receipt = {
            "provider": "provider",
            "model": "model",
            "providerRequestId": request_id,
            "requestDigest": request_digest,
            "outputDigest": output_digest,
        }
        receipt_digest = module.canonical_digest(receipt)
        candidate_digest = module.candidate_digest(
            case_id=case_id,
            request_id=request_id,
            request_digest=request_digest,
            receipt_digest=receipt_digest,
            output_digest=output_digest,
            dataset_digest=dataset_digest,
            config_digest=config_digest,
            model_digest=model_digest,
        )
        cases.append(
            {
                "caseId": case_id,
                "status": "completed",
                "executionMode": "real",
                "requestId": request_id,
                "requestDigest": request_digest,
                "providerReceipt": receipt,
                "receiptDigest": receipt_digest,
                "output": output,
                "outputDigest": output_digest,
                "datasetDigest": dataset_digest,
                "configDigest": config_digest,
                "modelDigest": model_digest,
                "candidateDigest": candidate_digest,
                "reviewedOutputDigest": output_digest,
                "reviewResult": {"decision": "approved", "grounded": True},
                "reviewJudgments": [
                    {
                        "reviewer": "named-reviewer",
                        "approved": True,
                        "reviewRunId": "pending",
                    }
                ],
            }
        )
    run_id = module.candidate_run_id(cases)
    for case in cases:
        case["reviewJudgments"][0]["reviewRunId"] = run_id
        case["reviewedCandidateDigest"] = module.reviewed_candidate_digest(
            candidate_digest_value=case["candidateDigest"],
            review_result=case["reviewResult"],
            review_judgments=case["reviewJudgments"],
        )
    return (
        {
            "schemaVersion": "quality-candidate-evidence-v1",
            "runId": run_id,
            "runStatus": "completed",
            "executionMode": "real",
            "datasetDigest": dataset_digest,
            "configDigest": config_digest,
            "modelDigest": model_digest,
            "cases": cases,
        },
        dataset,
        config,
        model,
    )


def _validate(batch, dataset, config, model):
    return quality_evidence.validate_candidate_batch(
        batch,
        expected_case_ids=("case-1", "case-2"),
        dataset_material=dataset,
        config_material=config,
        model_material=model,
    )


def test_complete_current_candidate_evidence_is_accepted() -> None:
    batch, dataset, config, model = _batch()

    summary = _validate(batch, dataset, config, model)

    assert summary == {
        "caseCount": 2,
        "requestCount": 2,
        "reviewerCount": 1,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda batch: batch["cases"][0]["reviewJudgments"][0].update(reviewRunId="old-run"),
            "prefilled reviewer",
        ),
        (
            lambda batch: batch["cases"][0].update(
                output={"answer": "new output", "citations": []}
            ),
            "output digest",
        ),
        (lambda batch: batch["cases"].pop(), "every expected case"),
        (lambda batch: batch["cases"][0].pop("output"), "complete output"),
        (
            lambda batch: batch["cases"][1].update(requestId=batch["cases"][0]["requestId"]),
            "request IDs must be unique",
        ),
        (
            lambda batch: batch["cases"][0].update(reviewedOutputDigest="sha256:" + "0" * 64),
            "reviewedOutputDigest",
        ),
        (
            lambda batch: batch["cases"][0].update(reviewedCandidateDigest="sha256:" + "0" * 64),
            "current candidate",
        ),
    ],
)
def test_anti_forgery_rejects_stale_incomplete_or_reused_evidence(mutation, message: str) -> None:
    batch, dataset, config, model = _batch()
    mutation(batch)

    with pytest.raises(ValueError, match=message):
        _validate(batch, dataset, config, model)


@pytest.mark.parametrize("digest_name", ["datasetDigest", "configDigest", "modelDigest"])
def test_anti_forgery_requires_complete_dataset_config_and_model_digests(
    digest_name: str,
) -> None:
    batch, dataset, config, model = _batch()
    batch.pop(digest_name)

    with pytest.raises(ValueError, match=digest_name):
        _validate(batch, dataset, config, model)


@pytest.mark.parametrize("digest_name", ["datasetDigest", "configDigest", "modelDigest"])
def test_each_candidate_requires_dataset_config_and_model_digests(
    digest_name: str,
) -> None:
    batch, dataset, config, model = _batch()
    batch["cases"][0].pop(digest_name)

    with pytest.raises(ValueError, match=digest_name):
        _validate(batch, dataset, config, model)


def test_post_review_result_mutation_invalidates_approval() -> None:
    batch, dataset, config, model = _batch()
    batch["cases"][0]["reviewResult"]["grounded"] = False

    with pytest.raises(ValueError, match="current candidate"):
        _validate(batch, dataset, config, model)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runStatus", "skipped"),
        ("executionMode", "simulated"),
        ("executionMode", "prebuilt"),
    ],
)
def test_skipped_simulated_or_prebuilt_batches_cannot_pass(field: str, value: str) -> None:
    batch, dataset, config, model = _batch()
    batch[field] = value

    with pytest.raises(ValueError, match="completed real execution"):
        _validate(batch, dataset, config, model)


def test_dataset_change_cannot_reuse_old_batch_digests() -> None:
    batch, dataset, config, model = _batch()
    changed_dataset = deepcopy(dataset)
    changed_dataset["version"] = "dataset-v2"

    with pytest.raises(ValueError, match="datasetDigest"):
        _validate(batch, changed_dataset, config, model)


def test_swapped_unique_request_ids_invalidate_approved_candidates() -> None:
    batch, dataset, config, model = _batch()
    first = batch["cases"][0]["requestId"]
    batch["cases"][0]["requestId"] = batch["cases"][1]["requestId"]
    batch["cases"][1]["requestId"] = first

    with pytest.raises(ValueError, match="provider receipt|candidate digest"):
        _validate(batch, dataset, config, model)


def test_changed_run_identity_cannot_reuse_old_candidate_approvals() -> None:
    batch, dataset, config, model = _batch()
    batch["runId"] = "run-forged"
    for case in batch["cases"]:
        case["reviewJudgments"][0]["reviewRunId"] = "run-forged"
        case["reviewedCandidateDigest"] = quality_evidence.reviewed_candidate_digest(
            candidate_digest_value=case["candidateDigest"],
            review_result=case["reviewResult"],
            review_judgments=case["reviewJudgments"],
        )

    with pytest.raises(ValueError, match="runId"):
        _validate(batch, dataset, config, model)


@pytest.mark.parametrize(
    ("target", "environment", "message"),
    [
        (
            "quality-graph-real",
            {
                "TAP_RUN_QUALITY_GRAPH_01": "1",
                "TAP_QUALITY_GRAPH_DATASET_AUTHORIZATION": "approved:graph-dataset-v1",
            },
            "reviewer authorization",
        ),
        (
            "quality-test-design-real",
            {
                "TAP_RUN_QUALITY_TEST_01": "1",
                "TAP_QUALITY_TEST_DATASET_AUTHORIZATION": "approved:test-dataset-v1",
            },
            "reviewer authorization",
        ),
        (
            "quality-graph-real",
            {
                "TAP_RUN_QUALITY_GRAPH_01": "1",
                "TAP_QUALITY_GRAPH_DATASET_AUTHORIZATION": "approved:graph-dataset-v1",
                "TAP_QUALITY_GRAPH_REVIEWER_AUTHORIZATION": "approved:reviewer-v1",
            },
            "journey producer authorization",
        ),
        (
            "quality-kb-trusted-real",
            {"TAP_RUN_QUALITY_KB_TRUSTED_01": "1"},
            "dataset authorization",
        ),
        (
            "quality-test-design-candidate-real",
            {
                "TAP_RUN_QUALITY_TEST_01": "1",
                "TAP_QUALITY_TEST_DATASET_AUTHORIZATION": "approved:test-dataset-v1",
            },
            "model execution authorization",
        ),
    ],
)
def test_make_real_gates_fail_closed_before_any_runner(
    target: str, environment: dict[str, str], message: str
) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", target],
        cwd=ROOT,
        env={**os.environ, **environment},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert message in result.stderr


@pytest.mark.parametrize(
    ("target", "environment", "message"),
    [
        (
            "quality-graph-candidate-real",
            {
                "TAP_RUN_QUALITY_GRAPH_01": "1",
                "TAP_QUALITY_GRAPH_DATASET_AUTHORIZATION": "approved:",
            },
            "dataset authorization",
        ),
        (
            "quality-kb-trusted-real",
            {
                "TAP_RUN_QUALITY_KB_TRUSTED_01": "1",
                "TAP_QUALITY_KB_DATASET_AUTHORIZATION": "approved:",
            },
            "dataset authorization",
        ),
    ],
)
def test_make_rejects_empty_approval_identifiers(
    target: str, environment: dict[str, str], message: str
) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", target],
        cwd=ROOT,
        env={**os.environ, **environment},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert message in result.stderr


def test_trusted_kb_official_gate_cannot_pass_without_real_candidate_producer(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "reviewed-profile.json"
    profile.write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        ["make", "--no-print-directory", "quality-kb-trusted-real"],
        cwd=ROOT,
        env={
            **os.environ,
            "TAP_RUN_QUALITY_KB_TRUSTED_01": "1",
            "TAP_QUALITY_KB_DATASET_AUTHORIZATION": "approved:dataset-v1",
            "TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION": "approved:model-v1",
            "TAP_QUALITY_KB_REVIEWER_AUTHORIZATION": "approved:reviewer-v1",
            "TAP_QUALITY_KB_TRUSTED_PROFILE": str(profile),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "no authorized real candidate producer" in result.stderr
    assert "PENDING" in result.stderr
