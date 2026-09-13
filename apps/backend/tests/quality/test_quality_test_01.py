import asyncio
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.models import text_digest
from tap.modules.test_management.domain.models import (
    TestPlanGenerationRequest as GenerationRequest,
)

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/evaluate-quality-test-design.py"
GENERATOR = ROOT / "scripts/generate-quality-test-design-profile.py"
RUNNER = ROOT / "scripts/run-quality-test-design-candidate.py"


def _evaluator():
    spec = importlib.util.spec_from_file_location("evaluate_quality_test_design", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generator():
    spec = importlib.util.spec_from_file_location("generate_quality_test_design_profile", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner():
    spec = importlib.util.spec_from_file_location("run_quality_test_design_candidate", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile() -> dict:
    cases = []
    for index in range(50):
        suffix = f"{index + 1:03d}"
        intent = f"Verify business workflow {index}"
        source = "A successful operation creates an immutable record."
        output = {
            "title": f"Workflow {index}",
            "objective": intent,
            "scope": ["Approved operation"],
            "prerequisites": ["The request exists"],
            "risks": ["Duplicate records"],
            "cases": [
                {
                    "caseId": f"case_{suffix}",
                    "ordinal": 1,
                    "title": "Create record",
                    "objective": intent,
                    "critical": True,
                    "scenarios": [
                        {
                            "scenarioId": f"scenario_{suffix}",
                            "ordinal": 1,
                            "title": "Approved request",
                            "steps": [
                                {
                                    "stepId": f"given_{suffix}",
                                    "ordinal": 1,
                                    "keyword": "Given",
                                    "text": "a valid request",
                                    "expectedResult": None,
                                    "critical": False,
                                },
                                {
                                    "stepId": f"when_{suffix}",
                                    "ordinal": 2,
                                    "keyword": "When",
                                    "text": "the request is approved",
                                    "expectedResult": None,
                                    "critical": False,
                                },
                                {
                                    "stepId": f"then_{suffix}",
                                    "ordinal": 3,
                                    "keyword": "Then",
                                    "text": "an immutable record is created",
                                    "expectedResult": "one immutable record exists",
                                    "critical": True,
                                },
                            ],
                        }
                    ],
                }
            ],
            "citations": [
                {
                    "citationId": f"citation_{suffix}",
                    "sourceRevisionId": f"source_revision_{suffix}",
                    "documentRevisionId": f"document_revision_{suffix}",
                    "chunkId": f"chunk_{suffix}",
                    "contentDigest": text_digest(source),
                    "claimText": source,
                    "origin": "SOURCE",
                }
            ],
            "assumptions": [],
            "unknowns": [],
            "coverageGaps": [],
        }
        output_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    output, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()
        )
        request = GenerationRequest.create(
            project_id=VALIDATION_SCOPE.project_id,
            conversation_id=f"quality_conversation_{suffix}",
            turn_id=f"quality_turn_{suffix}",
            input_snapshot_digest=text_digest(intent),
            answer_evidence_snapshot_digest=text_digest(source),
            model_alias="tapper-chat",
            agent_revision_id="validation-test-design-agent-v1",
            skill_revision_ids=("validation-test-design-skill-v1",),
            objective=intent,
            idempotency_key=f"quality_test_design_{suffix}",
        )
        candidate_digest = text_digest(
            json.dumps(
                {
                    "caseId": f"intent-{index:03d}",
                    "intent": intent,
                    "source": source,
                    "criticalRequirements": ["immutable record"],
                    "requestDigest": request.request_digest,
                    "outputDigest": output_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        cases.append(
            {
                "caseId": f"intent-{index:03d}",
                "intent": intent,
                "source": source,
                "criticalRequirements": ["immutable record"],
                "observation": {
                    "schemaValid": True,
                    "bddValid": True,
                    "unsupportedFactCount": 0,
                    "criticalCovered": 1,
                    "criticalTotal": 1,
                    "criticalCorrectionRequired": False,
                    "outputDigest": output_digest,
                    "reviewedOutputDigest": output_digest,
                    "generatedOutput": output,
                    "candidateDigest": candidate_digest,
                    "reviewedCaseDigest": candidate_digest,
                    "providerReceipt": {
                        "requestDigest": request.request_digest,
                        "outputDigest": output_digest,
                        "provider": "provider",
                        "model": "model",
                        "providerRequestId": f"provider-request-{suffix}",
                    },
                },
                "reviewerJudgments": [
                    {"reviewer": "patrick", "approved": True},
                ],
            }
        )
    return {
        "schemaVersion": "quality-test-profile-v1",
        "profileId": "QUALITY-TEST-01",
        "dataset": {
            "version": "unit-v1",
            "reviewStatus": "approved",
            "providerInvocationCount": 50,
        },
        "bindings": {
            "modelAlias": "tapper-chat",
            "actualModel": "provider/model",
            "agentRevisionId": "validation-test-design-agent-v1",
            "skillRevisionIds": ["validation-test-design-skill-v1"],
        },
        "cases": cases,
    }


def test_quality_test_thresholds_pass_complete_observations() -> None:
    report = _evaluator().evaluate(_profile())
    assert report["passed"] is True
    assert report["metrics"]["criticalRequirementCoverage"]["actual"] == "50/50"


@pytest.mark.parametrize(
    ("mutation", "metric"),
    [
        (
            lambda profile: profile["cases"][0]["observation"].update(schemaValid=False),
            "schemaAndBdd",
        ),
        (lambda profile: profile["cases"][0]["observation"].update(bddValid=False), "schemaAndBdd"),
        (
            lambda profile: profile["cases"][0]["observation"].update(unsupportedFactCount=1),
            "unsupportedSourceFacts",
        ),
        (
            lambda profile: [
                profile["cases"][index]["observation"].update(criticalCovered=0)
                for index in range(6)
            ],
            "criticalRequirementCoverage",
        ),
        (
            lambda profile: [
                profile["cases"][index]["observation"].update(criticalCorrectionRequired=True)
                for index in range(11)
            ],
            "withoutCriticalCorrection",
        ),
    ],
)
def test_quality_test_reports_specific_failure(mutation, metric) -> None:
    profile = _profile()
    mutation(profile)
    report = _evaluator().evaluate(profile)
    assert report["passed"] is False
    assert report["metrics"][metric]["passed"] is False


def test_real_gate_requires_one_named_approved_reviewer() -> None:
    module = _evaluator()
    profile = _profile()
    profile["bindings"].update(module.current_bindings())
    for case in profile["cases"]:
        case["reviewerJudgments"] = []
    with pytest.raises(ValueError, match="one approved named reviewer"):
        module.evaluate(profile, real=True)


def test_reviewer_name_rejects_role_prefixes() -> None:
    module = _evaluator()
    for reviewer in ("human:patrick", "machine-self-review"):
        profile = _profile()
        profile["cases"][0]["reviewerJudgments"][0]["reviewer"] = reviewer
        with pytest.raises(ValueError, match="without a prefix"):
            module.evaluate(profile)


def test_generated_profile_is_ready_for_one_named_reviewer_gate() -> None:
    profile = _generator().build()

    assert profile["dataset"] == {
        "version": "candidate-v1",
        "reviewStatus": "pending",
        "labelingMethod": "named-review-with-deterministic-adjudication",
    }
    assert all(not item["reviewerJudgments"] for item in profile["cases"])
    assert all(item["observation"]["criticalCorrectionRequired"] for item in profile["cases"])


@pytest.mark.parametrize("missing", ["generatedOutput", "reviewedOutputDigest"])
def test_real_gate_requires_review_bound_generated_output(missing: str) -> None:
    module = _evaluator()
    profile = _profile()
    profile["bindings"].update(module.current_bindings())
    profile["cases"][0]["observation"].pop(missing)

    with pytest.raises(ValueError, match="generated output"):
        module.evaluate(profile, real=True)


def test_real_gate_rejects_incomplete_generated_output() -> None:
    module = _evaluator()
    profile = _profile()
    profile["bindings"].update(module.current_bindings())

    profile["cases"][0]["observation"]["generatedOutput"] = {"title": "incomplete"}

    with pytest.raises(ValueError, match="generated output"):
        module.evaluate(profile, real=True)


def test_real_gate_recomputes_generated_output_digest() -> None:
    module = _evaluator()
    profile = _profile()
    profile["bindings"].update(module.current_bindings())
    profile["cases"][0]["observation"]["generatedOutput"]["title"] = "changed"

    with pytest.raises(ValueError, match="bound to its review"):
        module.evaluate(profile, real=True)


def test_real_gate_requires_per_case_provider_receipts() -> None:
    module = _evaluator()
    profile = _profile()
    profile["bindings"].update(module.current_bindings())

    profile["cases"][0]["observation"].pop("providerReceipt")

    with pytest.raises(ValueError, match="provider receipt"):
        module.evaluate(profile, real=True)


def test_real_gate_rejects_review_for_changed_critical_requirements() -> None:
    module = _evaluator()
    profile = _profile()
    profile["bindings"].update(module.current_bindings())
    profile["cases"][0]["criticalRequirements"] = ["a new unreviewed requirement"]

    with pytest.raises(ValueError, match="current business case"):
        module.evaluate(profile, real=True)


def test_real_make_target_uses_validated_model_timeout(tmp_path: Path) -> None:
    log = tmp_path / "timeouts.log"
    uv = tmp_path / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "${TAPPER_MODEL_TIMEOUT_SECONDS:-unset}" '
        '>> "$TAP_TEST_TIMEOUT_LOG"\n',
        encoding="utf-8",
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{tmp_path}:{environment['PATH']}",
            "TAP_RUN_QUALITY_TEST_01": "1",
            "TAP_TEST_TIMEOUT_LOG": str(log),
        }
    )

    subprocess.run(
        ["make", "--no-print-directory", "quality-test-design-real"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert log.read_text(encoding="utf-8").splitlines()[0] == "60"


def test_real_runner_reinvokes_and_invalidates_review_for_changed_requirements(
    monkeypatch,
) -> None:
    module = _runner()
    calls = []

    class Gateway:
        async def generate_structured(self, request):
            calls.append(request)
            return SimpleNamespace(
                actual_provider="provider",
                actual_model="model",
                provider_request_id="provider-request-001",
            )

    class Models:
        gateway = Gateway()

        async def aclose(self):
            return None

    class Revision:
        content_digest = "sha256:" + "b" * 64

        def canonical_content(self):
            return {"title": "fresh model output"}

    class Generator:
        def __init__(self, gateway, *, timeout_seconds):
            self.gateway = gateway

        async def generate(self, context):
            await self.gateway.generate_structured(context.request)
            return Revision()

    profile = _generator().build()
    profile["cases"] = profile["cases"][:1]
    item = profile["cases"][0]
    request = GenerationRequest.create(
        project_id=VALIDATION_SCOPE.project_id,
        conversation_id="quality_conversation_001",
        turn_id="quality_turn_001",
        input_snapshot_digest=text_digest(item["intent"]),
        answer_evidence_snapshot_digest=text_digest(item["source"]),
        model_alias="tapper-chat",
        agent_revision_id="validation-test-design-agent-v1",
        skill_revision_ids=("validation-test-design-skill-v1",),
        objective=item["intent"],
        idempotency_key="quality_test_design_001",
    )
    reviewed_case_digest = module._candidate_digest(
        item,
        request_digest=request.request_digest,
        output_digest="sha256:" + "b" * 64,
    )
    profile["cases"][0]["observation"].update(
        outputDigest="sha256:" + "b" * 64,
        reviewedOutputDigest="sha256:" + "b" * 64,
        reviewedCaseDigest=reviewed_case_digest,
    )
    profile["cases"][0]["reviewerJudgments"] = [{"reviewer": "patrick", "approved": True}]
    profile["cases"][0]["criticalRequirements"] = ["a new unreviewed requirement"]
    monkeypatch.setattr(
        module.TapperSettings,
        "from_mapping",
        lambda _mapping: SimpleNamespace(model_timeout_seconds=60, chat_alias="tapper-chat"),
    )
    monkeypatch.setattr(module, "_create_embeddings", lambda _settings, max_retries: Models())
    monkeypatch.setattr(module, "ModelGatewayTestDesign", Generator)
    monkeypatch.setattr(module, "validate_draft_structure", lambda _revision: None)

    result = asyncio.run(module.run(profile))

    assert len(calls) == 1
    assert result["dataset"]["providerInvocationCount"] == 1
    assert result["dataset"]["reviewStatus"] == "pending"
    assert result["cases"][0]["reviewerJudgments"] == []
    assert "reviewedCaseDigest" not in result["cases"][0]["observation"]
    assert result["cases"][0]["observation"]["generatedOutput"] == {"title": "fresh model output"}
    assert result["cases"][0]["observation"]["providerReceipt"] == {
        "requestDigest": calls[0].request_digest,
        "outputDigest": "sha256:" + "b" * 64,
        "provider": "provider",
        "model": "model",
        "providerRequestId": "provider-request-001",
    }
