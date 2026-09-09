from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "evaluate-quality-kb.py"
RUNNER = ROOT / "scripts" / "run-quality-kb-real.py"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "quality" / "kb"
APPROVED = (
    "approved-provider",
    "approved-model",
    "structured",
    "sha256:" + "6" * 64,
    "approval-record:unit-v2",
)


def _module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _evaluator() -> ModuleType:
    return _module(SCRIPT, "evaluate_quality_kb")


def _runner() -> ModuleType:
    return _module(RUNNER, "run_quality_kb_real")


def _sha(character: str) -> str:
    return "sha256:" + character * 64


def _anchor(*, suffix: str = "a", source: str = "source-a") -> dict[str, object]:
    return {
        "evidenceId": f"evidence-{suffix}",
        "sourceId": source,
        "documentRevisionId": "revision-a",
        "chunkId": f"chunk-{suffix}",
        "locator": {"type": "document", "page": 1, "startOffset": 0, "endOffset": 12},
        "evidenceDigest": _sha(suffix),
    }


def _dataset() -> dict[str, object]:
    answer_anchor = _anchor()
    return {
        "schemaVersion": "quality-kb-dataset-v1",
        "profileId": "QUALITY-KB-01",
        "dataset": {
            "version": "human-unit-v2",
            "labelingMethod": "human",
            "provenance": "review-record:unit-v2",
        },
        "bindings": {
            "policyDigest": _sha("1"),
            "modelAlias": "tapper-chat",
            "promptDigest": _sha("2"),
            "agentRevisionDigest": _sha("3"),
            "skillRevisionDigests": [_sha("4")],
            "governanceDigests": [_sha("3"), _sha("4")],
            "schemaDigest": _sha("5"),
        },
        "cases": [
            {
                "caseId": "answerable-a",
                "caseType": "answerable",
                "question": "Which reviewer approves request alpha?",
                "projectId": "project-a",
                "authorizedSourceIds": ["source-a"],
                "selectedSourceIds": ["source-a"],
                "authorizationExpected": "allow",
                "shouldAbstain": False,
                "conflictLabel": None,
                "expectedEvidence": [answer_anchor],
                "expectedClaims": [
                    {
                        "claimId": "claim-label-a",
                        "textDigest": _sha("b"),
                        "supportedEvidenceIds": ["evidence-a"],
                    }
                ],
                "labelProvenance": "review-record:unit-v2#answerable-a",
            },
            {
                "caseId": "conflict-a",
                "caseType": "conflict",
                "question": "Do the alpha policies conflict?",
                "projectId": "project-a",
                "authorizedSourceIds": ["source-a", "source-b"],
                "selectedSourceIds": ["source-a", "source-b"],
                "authorizationExpected": "allow",
                "shouldAbstain": True,
                "conflictLabel": "material-conflict",
                "expectedEvidence": [answer_anchor, _anchor(suffix="c", source="source-b")],
                "expectedClaims": [],
                "labelProvenance": "review-record:unit-v2#conflict-a",
            },
            {
                "caseId": "unauthorized-a",
                "caseType": "unauthorized",
                "question": "Can alpha read the restricted source?",
                "projectId": "project-a",
                "authorizedSourceIds": ["source-a"],
                "selectedSourceIds": ["source-b"],
                "authorizationExpected": "deny",
                "authorizationFailure": "source",
                "shouldAbstain": True,
                "conflictLabel": None,
                "expectedEvidence": [],
                "expectedClaims": [],
                "labelProvenance": "review-record:unit-v2#unauthorized-a",
            },
            {
                "caseId": "abstain-a",
                "caseType": "abstain",
                "question": "What does alpha omit about retention?",
                "projectId": "project-a",
                "authorizedSourceIds": ["source-a"],
                "selectedSourceIds": ["source-a"],
                "authorizationExpected": "allow",
                "shouldAbstain": True,
                "conflictLabel": None,
                "expectedEvidence": [],
                "expectedClaims": [],
                "labelProvenance": "review-record:unit-v2#abstain-a",
            },
        ],
    }


def _execution(
    module: ModuleType, dataset: dict[str, object], case_id: str, call: int
) -> dict[str, object]:
    return {
        "timeoutMs": 1000,
        "startedAtUtc": "2026-09-09T00:00:00Z",
        "durationMs": 5,
        "cacheKey": module.expected_cache_key(dataset, case_id),
        "cacheHit": False,
        "maxRetriesPerCase": 2,
        "attempts": [
            {
                "attempt": 1,
                "retryReason": None,
                "startedAtUtc": "2026-09-09T00:00:00Z",
                "durationMs": 4,
                "providerCalls": [
                    {
                        "operation": "structured",
                        "alias": "tapper-chat",
                        "actualProvider": "approved-provider",
                        "actualModel": "approved-model",
                        "promptDigest": _sha("2"),
                        "schemaDigest": _sha("5"),
                        "governanceDigests": [_sha("3"), _sha("4")],
                        "providerRequestId": f"provider-{call}",
                        "gatewayCallId": f"gateway-{call}",
                        "durationMs": 2,
                    }
                ],
            }
        ],
    }


def _observations(dataset: dict[str, object]) -> dict[str, object]:
    module = _evaluator()
    answer_anchor = dataset["cases"][0]["expectedEvidence"][0]
    citation = {
        "citationId": "citation-a",
        "projectId": "project-a",
        "sourceId": answer_anchor["sourceId"],
        "documentRevisionId": answer_anchor["documentRevisionId"],
        "chunkId": answer_anchor["chunkId"],
        "locator": answer_anchor["locator"],
        "resolvedEvidenceDigest": answer_anchor["evidenceDigest"],
    }
    cases = []
    for index, case in enumerate(dataset["cases"], 1):
        answerable = case["caseType"] == "answerable"
        conflict = case["caseType"] == "conflict"
        unauthorized = case["caseType"] == "unauthorized"
        retrieved = []
        if answerable:
            retrieved = [
                {
                    "rank": 1,
                    "projectId": "project-a",
                    "sourceId": "source-a",
                    "documentRevisionId": "revision-a",
                    "chunkId": "chunk-a",
                }
            ]
        elif conflict:
            retrieved = [
                {
                    "rank": 1,
                    "projectId": "project-a",
                    "sourceId": "source-a",
                    "documentRevisionId": "revision-a",
                    "chunkId": "chunk-a",
                },
                {
                    "rank": 2,
                    "projectId": "project-a",
                    "sourceId": "source-b",
                    "documentRevisionId": "revision-a",
                    "chunkId": "chunk-c",
                },
            ]
        execution = _execution(module, dataset, case["caseId"], index)
        if unauthorized:
            execution["attempts"][0]["outcome"] = "authorization-denied"
            execution["attempts"][0]["providerCalls"] = []
        observation = {
            "caseId": case["caseId"],
            "authority": {
                "actualProjectId": "project-a",
                "authorizedSourceIds": case["authorizedSourceIds"],
                "decision": "deny" if case["caseType"] == "unauthorized" else "allow",
                "reason": ("source-not-authorized" if case["caseType"] == "unauthorized" else None),
            },
            "retrieved": retrieved,
            "abstained": not answerable,
            "claims": (
                [
                    {
                        "claimId": "claim-observed-a",
                        "textDigest": _sha("b"),
                        "citationIds": ["citation-a"],
                    }
                ]
                if answerable
                else []
            ),
            "citations": ([citation] if answerable else []),
            "execution": execution,
        }
        execution["cacheEvidenceDigest"] = module.runtime_evidence_digest(observation)
        cases.append(observation)
    return {
        "schemaVersion": "quality-kb-observations-v1",
        "datasetDigest": module.dataset_digest(dataset),
        "runId": "run-unit-v2",
        "execution": {
            "providerCallBudget": 1000,
            "providerCalls": 3,
            "cacheHits": 0,
            "retryCount": 0,
            "maxRetriesPerCase": 2,
        },
        "cases": cases,
    }


def test_report_is_deterministic_and_binds_dataset_config_and_governance() -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    first = module.evaluate_run(
        dataset, observations, min_cases=4, require_real=True, approved_mapping=APPROVED
    )
    second = module.evaluate_run(
        dataset, observations, min_cases=4, require_real=True, approved_mapping=APPROVED
    )

    assert first == second
    assert first["status"] == "pass"
    assert first["datasetDigest"].startswith("sha256:")
    assert first["configDigest"].startswith("sha256:")
    assert first["evaluatorDigest"].startswith("sha256:")
    assert first["bindings"] == dataset["bindings"]


def test_cross_source_fixture_fails_with_explicit_leakage_evidence(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "failing-cross-source-v1.json").read_text())
    dataset = fixture["dataset"]
    observations = fixture["observations"]
    dataset_path = tmp_path / "dataset.json"
    observations_path = tmp_path / "observations.json"
    report_path = tmp_path / "report.json"
    dataset_path.write_text(json.dumps(dataset))
    observations_path.write_text(json.dumps(observations))
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(dataset_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
            "--min-cases",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "leakage=2 (required 0)" in completed.stderr
    report = json.loads(report_path.read_text())
    assert report["metrics"]["leakageCount"] == 2
    assert report["cases"][0]["leakage"] == [
        "citation:citation-foreign:unselected-source:source-b",
        "retrieval:chunk-foreign:unselected-source:source-b",
    ]


def test_dataset_digest_covers_every_human_support_anchor_and_closed_label() -> None:
    module = _evaluator()
    original = _dataset()
    mutations = []
    for field, value in (
        ("shouldAbstain", True),
        ("authorizationExpected", "deny"),
        ("conflictLabel", "different"),
    ):
        changed = deepcopy(original)
        changed["cases"][0][field] = value
        mutations.append(changed)
    support = deepcopy(original)
    support["cases"][0]["expectedClaims"][0]["supportedEvidenceIds"] = []
    mutations.append(support)
    locator = deepcopy(original)
    locator["cases"][0]["expectedEvidence"][0]["locator"]["page"] = 2
    mutations.append(locator)
    baseline = module.dataset_digest(original)

    assert all(module.dataset_digest(item) != baseline for item in mutations)


def test_schema_rejects_100_renamed_copies_and_inconsistent_semantics() -> None:
    module = _evaluator()
    copied = _dataset()
    copied["cases"] = [
        {**deepcopy(copied["cases"][0]), "caseId": f"copy-{index}"} for index in range(100)
    ]
    with pytest.raises(ValueError, match="duplicate case content"):
        module.validate_dataset(copied, min_cases=100)

    inconsistent = _dataset()
    inconsistent["cases"][0]["shouldAbstain"] = True
    with pytest.raises(ValueError, match="answerable.*shouldAbstain"):
        module.validate_dataset(inconsistent, min_cases=1)


def test_empty_anchor_precision_and_abstain_denominators_fail_closed() -> None:
    module = _evaluator()
    dataset = _dataset()
    dataset["cases"] = [dataset["cases"][3]]
    observations = {
        "schemaVersion": "quality-kb-observations-v1",
        "datasetDigest": module.dataset_digest(dataset),
        "runId": "empty",
        "execution": {
            "providerCallBudget": 1000,
            "providerCalls": 0,
            "cacheHits": 0,
            "retryCount": 0,
            "maxRetriesPerCase": 2,
        },
        "cases": [],
    }
    report = module.evaluate_run(dataset, observations, min_cases=1)

    assert report["status"] == "fail"
    assert report["thresholds"]["anchorResolution"]["passed"] is False
    assert report["thresholds"]["groundedClaimCitationPrecision"]["passed"] is False
    assert report["thresholds"]["abstainAccuracy"]["passed"] is False


def test_anchor_digest_and_identity_are_resolved_not_boolean_self_reports() -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    observations["cases"][0]["citations"][0]["resolvedEvidenceDigest"] = _sha("f")
    observations["cases"][0]["citations"][0]["anchorResolvable"] = True
    observations["cases"][0]["execution"]["cacheEvidenceDigest"] = module.runtime_evidence_digest(
        observations["cases"][0]
    )
    report = module.evaluate_run(dataset, observations, min_cases=4)

    assert report["metrics"]["anchorResolved"] == 0
    assert report["thresholds"]["anchorResolution"]["passed"] is False
    assert report["status"] == "fail"


def test_execution_aggregate_tampering_and_duplicate_call_ids_fail() -> None:
    module = _evaluator()
    dataset = _dataset()
    tampered = _observations(dataset)
    tampered["execution"]["providerCalls"] = 1
    report = module.evaluate_run(dataset, tampered, min_cases=4)
    assert "runner execution aggregate does not match immutable case evidence" in report["failures"]

    duplicated = _observations(dataset)
    duplicated["cases"][1]["execution"]["attempts"][0]["providerCalls"][0]["providerRequestId"] = (
        "provider-1"
    )
    duplicated["cases"][1]["execution"]["attempts"][0]["providerCalls"][0]["gatewayCallId"] = (
        "gateway-1"
    )
    with pytest.raises(ValueError, match="globally unique"):
        module.evaluate_run(dataset, duplicated, min_cases=4)


@pytest.mark.asyncio
async def test_runner_calls_gateway_on_cache_miss_and_identity_comes_from_audit() -> None:
    from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelRequest,
        ModelResult,
        ModelUsage,
    )

    module = _runner()
    dataset = _dataset()
    dataset["cases"] = [dataset["cases"][0]]
    scope = ProjectScopeContext(
        enterprise_id="tenant",
        project_id="project-a",
        actor_id="actor",
        identity_mode=IdentityMode.VALIDATION,
    )
    calls = []

    class FakeGateway:
        async def generate_structured(self, request):
            calls.append(request)
            audit = ModelCallAudit(
                scope=scope,
                alias=request.alias,
                operation=ModelOperation.STRUCTURED,
                prompt_digest=request.prompt_digest,
                schema_digest=request.schema_digest,
                context_digest=_sha("9"),
                idempotency_key=request.idempotency_key,
                actual_provider="provider-from-audit",
                actual_model="model-from-audit",
                usage=ModelUsage(10, 5),
            )
            return ModelResult(
                {"answer": "Approved.", "claims": []},
                "model-from-audit",
                ModelUsage(10, 5),
                "provider-from-audit",
                audit,
                "provider-call-1",
                "gateway-call-1",
            )

    class FakePath:
        def __init__(self, gateway):
            self.gateway = gateway

        async def resolve_authority(self, case):
            return {
                "actualProjectId": "project-a",
                "authorizedSourceIds": ["source-a"],
            }

        async def run_case(self, case):
            await self.gateway.generate_structured(
                ModelRequest(
                    scope,
                    "tapper-chat",
                    ModelOperation.STRUCTURED,
                    "prompt",
                    _sha("2"),
                    "context",
                    1.0,
                    case["caseId"],
                    {},
                    _sha("5"),
                )
            )
            return {"retrieved": [], "abstained": False, "claims": [], "citations": []}

        async def resolve_citation(self, citation):
            raise AssertionError("no citations")

    cache = {}
    observations = await module.run_dataset(
        dataset,
        gateway=FakeGateway(),
        path_factory=FakePath,
        timeout_ms=1000,
        provider_call_budget=2,
        cache=cache,
    )
    cached_observations = await module.run_dataset(
        dataset,
        gateway=FakeGateway(),
        path_factory=FakePath,
        timeout_ms=1000,
        provider_call_budget=2,
        cache=cache,
    )
    provider_call = observations["cases"][0]["execution"]["attempts"][0]["providerCalls"][0]

    assert len(calls) == 1
    assert (provider_call["actualProvider"], provider_call["actualModel"]) == (
        "provider-from-audit",
        "model-from-audit",
    )
    assert (provider_call["providerRequestId"], provider_call["gatewayCallId"]) == (
        "provider-call-1",
        "gateway-call-1",
    )
    assert len(calls) == 1
    assert cached_observations["execution"]["providerCalls"] == 0
    assert cached_observations["cases"][0]["execution"]["cacheHit"] is True
    assert (
        cached_observations["cases"][0]["execution"]["cacheEvidenceDigest"]
        == observations["cases"][0]["execution"]["cacheEvidenceDigest"]
    )


@pytest.mark.asyncio
async def test_runner_rejects_a_cache_miss_that_bypasses_model_gateway() -> None:
    module = _runner()
    dataset = _dataset()
    dataset["cases"] = [dataset["cases"][0]]

    class BypassPath:
        def __init__(self, gateway):
            self.gateway = gateway

        async def resolve_authority(self, case):
            return {
                "actualProjectId": "project-a",
                "authorizedSourceIds": ["source-a"],
            }

        async def run_case(self, case):
            return {"retrieved": [], "abstained": True, "claims": [], "citations": []}

        async def resolve_citation(self, citation):
            raise AssertionError("no citations")

    with pytest.raises(RuntimeError, match="cache miss made no ModelGateway call"):
        await module.run_dataset(
            dataset,
            gateway=object(),
            path_factory=BypassPath,
            timeout_ms=1000,
            provider_call_budget=2,
            cache={},
        )


def test_real_runner_preflight_requires_opt_in_and_mapping_before_io(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    no_opt_in = subprocess.run(
        [sys.executable, str(RUNNER), str(missing), "--observations", str(tmp_path / "o.json")],
        check=False,
        capture_output=True,
        text=True,
        env={},
    )
    assert no_opt_in.returncode == 2
    assert "TAP_RUN_QUALITY_KB_01=1" in no_opt_in.stderr
    assert "provider I/O was not started" in no_opt_in.stderr

    no_mapping = subprocess.run(
        [sys.executable, str(RUNNER), str(missing), "--observations", str(tmp_path / "o.json")],
        check=False,
        capture_output=True,
        text=True,
        env={"TAP_RUN_QUALITY_KB_01": "1"},
    )
    assert no_mapping.returncode == 2
    assert "approved actual provider/model/operation mapping" in no_mapping.stderr
    assert "provider I/O was not started" in no_mapping.stderr

    malformed_mapping = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            str(missing),
            "--observations",
            str(tmp_path / "o.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "TAP_RUN_QUALITY_KB_01": "1",
            "TAP_QUALITY_KB_APPROVED_PROVIDER": "provider",
            "TAP_QUALITY_KB_APPROVED_MODEL": "model",
            "TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST": "sha256:" + "g" * 64,
        },
    )
    assert malformed_mapping.returncode == 2
    assert "approved actual provider/model/operation mapping" in malformed_mapping.stderr
    assert "dataset invalid" not in malformed_mapping.stderr


def test_100_synthetic_self_described_cases_cannot_zero_io_pass_real_gate(
    tmp_path: Path,
) -> None:
    dataset = _dataset()
    dataset["dataset"]["labelingMethod"] = "model-generated"
    generated = []
    for repetition in range(25):
        for original in dataset["cases"]:
            case = deepcopy(original)
            case["caseId"] = f"{original['caseId']}-{repetition}"
            case["question"] = f"{original['question']} Synthetic variant {repetition}."
            generated.append(case)
    dataset["cases"] = generated
    dataset_path = tmp_path / "synthetic-100.json"
    dataset_path.write_text(json.dumps(dataset))
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            str(dataset_path),
            "--observations",
            str(tmp_path / "observations.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "TAP_RUN_QUALITY_KB_01": "1",
            "TAP_QUALITY_KB_APPROVED_PROVIDER": "approved-provider",
            "TAP_QUALITY_KB_APPROVED_MODEL": "approved-model",
            "TAP_QUALITY_KB_APPROVED_OPERATION": "structured",
            "TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST": _sha("6"),
            "TAP_QUALITY_KB_MODEL_APPROVAL_ARTIFACT": "approval-record:unit-v2",
        },
    )

    assert completed.returncode == 2
    assert "traceable human labels are required" in completed.stderr
    assert "provider I/O was not started" in completed.stderr


def test_every_answer_call_must_match_approval_and_dataset_governance() -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    observations["cases"][1]["execution"]["attempts"][0]["providerCalls"][0]["actualModel"] = (
        "unapproved-model"
    )
    observations["cases"][0]["execution"]["attempts"][0]["providerCalls"][0]["promptDigest"] = _sha(
        "f"
    )
    observations["cases"][1]["execution"]["attempts"][0]["providerCalls"][0]["operation"] = "chat"

    report = module.evaluate_run(
        dataset,
        observations,
        min_cases=4,
        require_real=True,
        approved_mapping=APPROVED,
    )

    assert report["status"] == "fail"
    assert "unapproved answer identity" in " ".join(report["failures"])
    assert "prompt governance mismatch" in " ".join(report["failures"])
    assert "unapproved answer operation" in " ".join(report["failures"])


@pytest.mark.parametrize(
    ("binding", "value"),
    [
        ("policyDigest", None),
        ("promptDigest", None),
        ("agentRevisionDigest", None),
        ("skillRevisionDigests", []),
        ("governanceDigests", []),
        ("schemaDigest", None),
        ("modelAlias", ""),
    ],
)
def test_missing_dataset_governance_binding_fails(binding: str, value: object) -> None:
    module = _evaluator()
    dataset = _dataset()
    dataset["bindings"][binding] = value

    if binding in {"modelAlias", "governanceDigests"}:
        with pytest.raises(ValueError, match=binding):
            module.evaluate_run(dataset, None, min_cases=4)
    else:
        report = module.evaluate_run(dataset, None, min_cases=4)
        assert report["status"] == "fail"
        assert binding in " ".join(report["failures"])


def test_model_alias_and_approval_artifact_formats_are_locked() -> None:
    module = _evaluator()
    dataset = _dataset()
    dataset["bindings"]["modelAlias"] = "alias with spaces"
    with pytest.raises(ValueError, match="modelAlias"):
        module.evaluate_run(dataset, None, min_cases=4)

    dataset = _dataset()
    with pytest.raises(ValueError, match="approval artifact"):
        module.evaluate_run(
            dataset,
            _observations(dataset),
            min_cases=4,
            approved_mapping=(APPROVED[0], APPROVED[1], APPROVED[2], APPROVED[3], "free text"),
        )


def test_full_governance_list_must_bind_agent_and_every_skill_revision() -> None:
    module = _evaluator()
    dataset = _dataset()
    dataset["bindings"]["governanceDigests"] = [_sha("3")]

    with pytest.raises(ValueError, match="governanceDigests.*Skill"):
        module.evaluate_run(dataset, None, min_cases=4)


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid-start",
        "case-duration-over-timeout",
        "attempt-after-case",
        "call-duration-over-attempt",
    ],
)
def test_execution_time_evidence_is_parsed_and_bounded(mutation: str) -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    execution = observations["cases"][0]["execution"]
    if mutation == "invalid-start":
        execution["startedAtUtc"] = "2026-99-99T00:00:00Z"
    elif mutation == "case-duration-over-timeout":
        execution["durationMs"] = 1001
    elif mutation == "attempt-after-case":
        execution["attempts"][0]["startedAtUtc"] = "2026-09-09T00:00:01Z"
    else:
        execution["attempts"][0]["providerCalls"][0]["durationMs"] = 6
        execution["attempts"][0]["durationMs"] = 5

    with pytest.raises(ValueError, match="time|duration"):
        module.evaluate_run(dataset, observations, min_cases=4)


def test_retry_configuration_tampering_fails() -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    observations["execution"]["maxRetriesPerCase"] = 1
    observations["cases"][0]["execution"]["maxRetriesPerCase"] = 1

    report = module.evaluate_run(dataset, observations, min_cases=4, max_retries_per_case=2)

    assert report["status"] == "fail"
    assert "retry configuration" in " ".join(report["failures"])


def test_cached_runtime_evidence_digest_rejects_observation_tampering() -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    observations["cases"][0]["retrieved"][0]["chunkId"] = "tampered-chunk"

    with pytest.raises(ValueError, match="cache evidence"):
        module.evaluate_run(dataset, observations, min_cases=4)


def test_denied_case_with_retrieval_or_model_evidence_fails_closed() -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    denied = observations["cases"][2]
    denied["retrieved"] = [
        {
            "rank": 1,
            "projectId": "project-a",
            "sourceId": "source-b",
            "documentRevisionId": "revision-b",
            "chunkId": "leaked-chunk",
        }
    ]
    denied["execution"]["cacheEvidenceDigest"] = module.runtime_evidence_digest(denied)

    report = module.evaluate_run(dataset, observations, min_cases=4)

    assert report["status"] == "fail"
    assert "denied case produced retrieval/model evidence" in " ".join(report["failures"])


@pytest.mark.asyncio
async def test_provider_budget_stops_n_plus_one_before_delegate_io() -> None:
    from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelRequest,
        ModelResult,
        ModelUsage,
    )

    module = _runner()
    dataset = _dataset()
    dataset["cases"] = [dataset["cases"][0]]
    scope = ProjectScopeContext(
        enterprise_id="tenant",
        project_id="project-a",
        actor_id="actor",
        identity_mode=IdentityMode.VALIDATION,
    )
    delegate_calls = 0

    class Gateway:
        async def generate_structured(self, request):
            nonlocal delegate_calls
            delegate_calls += 1
            audit = ModelCallAudit(
                scope=scope,
                alias=request.alias,
                operation=ModelOperation.STRUCTURED,
                prompt_digest=request.prompt_digest,
                schema_digest=request.schema_digest,
                context_digest=_sha("9"),
                idempotency_key=request.idempotency_key,
                actual_provider="approved-provider",
                actual_model="approved-model",
                usage=ModelUsage(1, 1),
                governance_digests=(_sha("3"), _sha("4")),
            )
            return ModelResult(
                {},
                "approved-model",
                ModelUsage(1, 1),
                "approved-provider",
                audit,
                f"provider-{delegate_calls}",
                f"gateway-{delegate_calls}",
            )

    class TwoCallPath:
        def __init__(self, gateway):
            self.gateway = gateway

        async def resolve_authority(self, case):
            return {"actualProjectId": "project-a", "authorizedSourceIds": ["source-a"]}

        async def run_case(self, case):
            request = ModelRequest(
                scope,
                "tapper-chat",
                ModelOperation.STRUCTURED,
                "prompt",
                _sha("2"),
                "context",
                1.0,
                case["caseId"],
                {},
                _sha("5"),
                governance_digests=(_sha("3"), _sha("4")),
            )
            await self.gateway.generate_structured(request)
            await self.gateway.generate_structured(request)
            return {"retrieved": [], "abstained": False, "claims": [], "citations": []}

        async def resolve_citation(self, citation):
            raise AssertionError("no citations")

    with pytest.raises(RuntimeError, match="provider call budget exhausted"):
        await module.run_dataset(
            dataset,
            gateway=Gateway(),
            path_factory=TwoCallPath,
            timeout_ms=1000,
            provider_call_budget=1,
            cache={},
        )

    assert delegate_calls == 1


@pytest.mark.asyncio
async def test_scope_aware_service_rejects_wrong_project_and_unauthorized_source_without_io() -> (
    None
):
    from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
    from tap.modules.knowledge.application.answers import AnswerService
    from tap.modules.knowledge.domain.models import (
        AnswerMode,
        AnswerRequest,
        ResourceMode,
        ResourceRef,
        SourceFamily,
    )

    module = _runner()
    allowed = "src_" + "a" * 32
    denied = "src_" + "b" * 32
    dataset = _dataset()
    source_case = deepcopy(dataset["cases"][2])
    source_case["authorizedSourceIds"] = [allowed]
    source_case["selectedSourceIds"] = [denied]
    project_case = deepcopy(source_case)
    project_case["caseId"] = "wrong-project-a"
    project_case["question"] = "Can another project read alpha?"
    project_case["projectId"] = "project-b"
    project_case["selectedSourceIds"] = [allowed]
    project_case["authorizationFailure"] = "project"
    dataset["cases"] = [source_case, project_case]
    scope = ProjectScopeContext(
        enterprise_id="tenant",
        project_id="project-a",
        actor_id="actor",
        identity_mode=IdentityMode.VALIDATION,
    )

    class ScopeAwareRepository:
        def __init__(self):
            self.scope = scope
            self.loads = 0

        async def load_source_revisions(self, source_ids):
            self.loads += 1
            return ()

    repository = ScopeAwareRepository()
    service = AnswerService(repository=repository, knowledge=object())

    class ServicePath:
        def __init__(self, gateway):
            self.gateway = gateway

        async def resolve_authority(self, case):
            return {
                "actualProjectId": service.scope.project_id,
                "authorizedSourceIds": [allowed],
            }

        async def run_case(self, case):
            await service.answer(
                AnswerRequest(
                    query=case["question"],
                    answer_mode=AnswerMode.QUICK,
                    source_families=(SourceFamily.DOC,),
                    resource_refs=(
                        ResourceRef(
                            family=SourceFamily.DOC,
                            source_id=case["selectedSourceIds"][0],
                            mode=ResourceMode.SCOPE,
                        ),
                    ),
                )
            )
            raise AssertionError("authorization should reject before Knowledge/model I/O")

        async def resolve_citation(self, citation):
            raise AssertionError("no citations")

    observations = await module.run_dataset(
        dataset,
        gateway=object(),
        path_factory=ServicePath,
        timeout_ms=1000,
        provider_call_budget=1,
        cache={},
    )

    assert repository.loads == 1
    assert observations["execution"]["providerCalls"] == 0
    assert [item["authority"]["reason"] for item in observations["cases"]] == [
        "source-not-authorized",
        "project-mismatch",
    ]
    assert all(
        not item["retrieved"] and not item["citations"] and item["abstained"]
        for item in observations["cases"]
    )


@pytest.mark.asyncio
async def test_runner_fails_stale_authorization_labels_before_model_io() -> None:
    module = _runner()
    dataset = _dataset()
    dataset["cases"] = [dataset["cases"][2]]
    run_calls = 0

    class StaleAuthorityPath:
        def __init__(self, gateway):
            self.gateway = gateway

        async def resolve_authority(self, case):
            return {
                "actualProjectId": "project-a",
                "authorizedSourceIds": ["source-a", "source-b"],
            }

        async def run_case(self, case):
            nonlocal run_calls
            run_calls += 1
            raise AssertionError("stale deny label must stop before model I/O")

        async def resolve_citation(self, citation):
            raise AssertionError("no citations")

    with pytest.raises(ValueError, match="authorization label contradicts"):
        await module.run_dataset(
            dataset,
            gateway=object(),
            path_factory=StaleAuthorityPath,
            timeout_ms=1000,
            provider_call_budget=1,
            cache={},
        )

    assert run_calls == 0


def test_offline_evaluator_never_needs_provider_configuration(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(FIXTURES / "profile-v1.json")],
        check=False,
        capture_output=True,
        text=True,
        env={},
    )

    assert completed.returncode == 1
    assert "cases=0 (required 100)" in completed.stderr
    assert "provider" not in completed.stderr.casefold()
