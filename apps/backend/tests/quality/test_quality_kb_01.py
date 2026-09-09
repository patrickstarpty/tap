from __future__ import annotations

import hashlib
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
APPROVAL_CONTENT = {
    "schemaVersion": "quality-kb-model-approval-v2",
    "approvalId": "unit-v3",
    "routes": [
        {
            "logicalAlias": "tapper-embedding",
            "actualProvider": "approved-provider",
            "actualModel": "approved-provider/approved-embedding",
            "operation": "embed",
            "scope": {"enterpriseId": "tenant", "projectId": "project-a"},
        },
        {
            "logicalAlias": "tapper-chat",
            "actualProvider": "approved-provider",
            "actualModel": "approved-provider/approved-model",
            "operation": "chat",
            "scope": {"enterpriseId": "tenant", "projectId": "project-a"},
        },
        {
            "logicalAlias": "tapper-chat",
            "actualProvider": "approved-provider",
            "actualModel": "approved-provider/approved-model",
            "operation": "structured",
            "scope": {"enterpriseId": "tenant", "projectId": "project-a"},
        },
    ],
    "expiresAtUtc": "2026-09-30T00:00:00Z",
}
APPROVAL_BYTES = json.dumps(APPROVAL_CONTENT, sort_keys=True, separators=(",", ":")).encode()
APPROVAL_DIGEST = "sha256:" + hashlib.sha256(APPROVAL_BYTES).hexdigest()
APPROVED = {
    **APPROVAL_CONTENT,
    "digest": APPROVAL_DIGEST,
    "artifact": "approval-record:unit-v3",
}
PRODUCTION_ROUTES = {item["operation"]: item for item in APPROVAL_CONTENT["routes"]}


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


def _approval_artifact(tmp_path: Path, content: dict[str, object] | None = None) -> Path:
    path = tmp_path / "approval.json"
    path.write_bytes(
        json.dumps(content or APPROVAL_CONTENT, sort_keys=True, separators=(",", ":")).encode()
    )
    return path


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
            "approvalDigest": APPROVAL_DIGEST,
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
        "finishedAtUtc": "2026-09-09T00:00:00.005Z",
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
                        "runnerCallId": f"runner-{call}",
                        "requestedAlias": "tapper-chat",
                        "requestedOperation": "structured",
                        "requestedProvider": "approved-provider",
                        "requestedModel": "approved-provider/approved-model",
                        "startedAtUtc": "2026-09-09T00:00:00Z",
                        "endedAtUtc": "2026-09-09T00:00:00.002Z",
                        "status": "success",
                        "retryable": False,
                        "operation": "structured",
                        "alias": "tapper-chat",
                        "actualProvider": "approved-provider",
                        "actualModel": "approved-provider/approved-model",
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
                "actualEnterpriseId": "tenant",
                "actualProjectId": "project-a",
                "actualPolicyDigest": (None if case["caseType"] == "unauthorized" else _sha("1")),
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
            "startedAtUtc": "2026-09-09T00:00:00Z",
            "finishedAtUtc": "2026-09-09T00:00:00.005Z",
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
    assert first["approvedMappingDigest"] == APPROVAL_DIGEST
    assert first["approvedMappingExpiresAtUtc"] == "2026-09-30T00:00:00Z"
    assert first["approvedRoutes"] == APPROVAL_CONTENT["routes"]


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
            "startedAtUtc": "2026-09-09T00:00:00Z",
            "finishedAtUtc": "2026-09-09T00:00:00Z",
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
                "actualEnterpriseId": "tenant",
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
            return {
                "actualPolicyDigest": _sha("1"),
                "retrieved": [],
                "abstained": False,
                "claims": [],
                "citations": [],
            }

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
        production_routes=PRODUCTION_ROUTES,
    )
    with pytest.raises(ValueError, match="pre-populated cache"):
        await module.run_dataset(
            dataset,
            gateway=FakeGateway(),
            path_factory=FakePath,
            timeout_ms=1000,
            provider_call_budget=2,
            cache=cache,
            production_routes=PRODUCTION_ROUTES,
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
    assert observations["execution"]["cacheHits"] == 0


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
                "actualEnterpriseId": "tenant",
                "actualProjectId": "project-a",
                "authorizedSourceIds": ["source-a"],
            }

        async def run_case(self, case):
            return {
                "actualPolicyDigest": _sha("1"),
                "retrieved": [],
                "abstained": True,
                "claims": [],
                "citations": [],
            }

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
            production_routes=PRODUCTION_ROUTES,
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
        env={
            "TAP_RUN_QUALITY_KB_01": "1",
            "TAP_QUALITY_KB_APPROVED_PROVIDER": "self-reported-provider",
            "TAP_QUALITY_KB_APPROVED_MODEL": "self-reported-model",
            "TAP_QUALITY_KB_APPROVED_OPERATION": "structured",
        },
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
            "TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST": "sha256:" + "g" * 64,
            "TAP_QUALITY_KB_MODEL_APPROVAL_ARTIFACT": str(tmp_path / "missing.json"),
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
    approval_path = _approval_artifact(tmp_path)
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
            "TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST": APPROVAL_DIGEST,
            "TAP_QUALITY_KB_MODEL_APPROVAL_ARTIFACT": str(approval_path),
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
    observations["cases"][1]["execution"]["attempts"][0]["providerCalls"][0][
        "requestedProvider"
    ] = "unapproved-provider"

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
    assert "unapproved requested provider route" in " ".join(report["failures"])


@pytest.mark.parametrize(
    ("binding", "value"),
    [
        ("policyDigest", None),
        ("approvalDigest", None),
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


def test_model_alias_format_is_locked() -> None:
    module = _evaluator()
    dataset = _dataset()
    dataset["bindings"]["modelAlias"] = "alias with spaces"
    with pytest.raises(ValueError, match="modelAlias"):
        module.evaluate_run(dataset, None, min_cases=4)


def test_approval_artifact_requires_canonical_bytes_digest_and_unexpired_scope(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    module = _runner()
    valid = _approval_artifact(tmp_path)
    approved = module._load_approval_artifact(
        valid, APPROVAL_DIGEST, now=datetime(2026, 9, 9, tzinfo=UTC)
    )
    module._require_approval_scope(approved, "tenant", "project-a")
    assert approved["routes"][2]["actualProvider"] == "approved-provider"

    with pytest.raises(ValueError, match="digest"):
        module._load_approval_artifact(valid, _sha("f"), now=datetime(2026, 9, 9, tzinfo=UTC))
    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(json.dumps(APPROVAL_CONTENT, indent=2))
    noncanonical_digest = "sha256:" + hashlib.sha256(noncanonical.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="canonical"):
        module._load_approval_artifact(
            noncanonical, noncanonical_digest, now=datetime(2026, 9, 9, tzinfo=UTC)
        )
    expired_content = {**APPROVAL_CONTENT, "expiresAtUtc": "2026-09-08T00:00:00Z"}
    expired = _approval_artifact(tmp_path, expired_content)
    expired_digest = "sha256:" + hashlib.sha256(expired.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="expired"):
        module._load_approval_artifact(
            expired, expired_digest, now=datetime(2026, 9, 9, tzinfo=UTC)
        )
    missing_expiry_content = dict(APPROVAL_CONTENT)
    missing_expiry_content.pop("expiresAtUtc")
    missing_expiry = _approval_artifact(tmp_path, missing_expiry_content)
    missing_expiry_digest = "sha256:" + hashlib.sha256(missing_expiry.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="expiry"):
        module._load_approval_artifact(
            missing_expiry, missing_expiry_digest, now=datetime(2026, 9, 9, tzinfo=UTC)
        )
    null_expiry = _approval_artifact(tmp_path, {**APPROVAL_CONTENT, "expiresAtUtc": None})
    null_expiry_digest = "sha256:" + hashlib.sha256(null_expiry.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="expiry"):
        module._load_approval_artifact(
            null_expiry, null_expiry_digest, now=datetime(2026, 9, 9, tzinfo=UTC)
        )
    far_expiry = _approval_artifact(
        tmp_path, {**APPROVAL_CONTENT, "expiresAtUtc": "2026-10-10T00:00:01Z"}
    )
    far_expiry_digest = "sha256:" + hashlib.sha256(far_expiry.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="validity window"):
        module._load_approval_artifact(
            far_expiry, far_expiry_digest, now=datetime(2026, 9, 9, tzinfo=UTC)
        )
    with pytest.raises(ValueError, match="scope"):
        module._require_approval_scope(approved, "tenant", "project-b")


def test_dataset_and_observed_scope_must_match_approval_artifact() -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    wrong_scope = deepcopy(APPROVED)
    for route in wrong_scope["routes"]:
        route["scope"] = {"enterpriseId": "tenant", "projectId": "project-b"}

    report = module.evaluate_run(
        dataset,
        observations,
        min_cases=4,
        require_real=True,
        approved_mapping=wrong_scope,
    )

    assert report["status"] == "fail"
    assert "approval scope" in " ".join(report["failures"])


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


def test_case_execution_must_be_nested_in_declared_run_window() -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    observations["execution"]["startedAtUtc"] = "2026-09-29T00:00:00Z"
    observations["execution"]["finishedAtUtc"] = "2026-09-29T00:00:01Z"

    with pytest.raises(ValueError, match="outside run window"):
        module.evaluate_run(
            dataset,
            observations,
            min_cases=4,
            require_real=True,
            approved_mapping=APPROVED,
        )


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


def test_evaluator_requires_zero_cache_hits_and_actual_policy_binding() -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    observations["cases"][0]["authority"]["actualPolicyDigest"] = _sha("f")
    observations["cases"][0]["execution"]["cacheEvidenceDigest"] = module.runtime_evidence_digest(
        observations["cases"][0]
    )
    observations["cases"][1]["execution"]["cacheHit"] = True
    observations["cases"][1]["execution"]["attempts"] = []
    observations["execution"]["providerCalls"] = 2
    observations["execution"]["cacheHits"] = 1

    report = module.evaluate_run(dataset, observations, min_cases=4)

    assert report["status"] == "fail"
    failures = " ".join(report["failures"])
    assert "policy digest" in failures
    assert "cache hits" in failures


@pytest.mark.asyncio
async def test_runner_rejects_rebuilt_policy_mismatch_before_accepting_answer() -> None:
    module = _runner()
    dataset = _dataset()
    dataset["cases"] = [dataset["cases"][0]]

    class PolicyMismatchPath:
        def __init__(self, gateway):
            self.gateway = gateway

        async def resolve_authority(self, case):
            return {
                "actualEnterpriseId": "tenant",
                "actualProjectId": "project-a",
                "authorizedSourceIds": ["source-a"],
            }

        async def run_case(self, case):
            return {
                "actualPolicyDigest": _sha("f"),
                "retrieved": [],
                "abstained": False,
                "claims": [],
                "citations": [],
            }

        async def resolve_citation(self, citation):
            raise AssertionError("no citations")

    with pytest.raises(ValueError, match="policy digest mismatch"):
        await module.run_dataset(
            dataset,
            gateway=object(),
            path_factory=PolicyMismatchPath,
            timeout_ms=1000,
            provider_call_budget=1,
            cache={},
            production_routes=PRODUCTION_ROUTES,
        )


@pytest.mark.asyncio
async def test_runtime_policy_mismatch_precedes_every_model_operation() -> None:
    from types import SimpleNamespace

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelRequest,
        ModelResult,
        ModelUsage,
        text_digest,
    )

    module = _runner()
    dataset = _dataset()
    operations: list[str] = []

    class Delegate:
        async def embed(self, request):
            operations.append("embed")
            audit = ModelCallAudit(
                scope=VALIDATION_SCOPE,
                alias=request.alias,
                operation=ModelOperation.EMBED,
                prompt_digest=request.prompt_digest,
                schema_digest=None,
                context_digest=_sha("9"),
                idempotency_key=request.idempotency_key,
                actual_provider="approved-provider",
                actual_model="approved-provider/approved-embedding",
                usage=ModelUsage(2, 0),
            )
            return ModelResult(
                (0.6, 0.8),
                "approved-provider/approved-embedding",
                ModelUsage(2, 0),
                "approved-provider",
                audit,
                "provider-embed",
                "gateway-embed",
            )

    captured = module.CapturingModelGateway(
        Delegate(), module.ProviderCallBudget(1), PRODUCTION_ROUTES
    )

    class Knowledge:
        scope = VALIDATION_SCOPE

        async def search(self, request):
            prompt = "Embed the supplied text."
            await captured.embed(
                ModelRequest(
                    VALIDATION_SCOPE,
                    "tapper-embedding",
                    ModelOperation.EMBED,
                    prompt,
                    text_digest(prompt),
                    request.query,
                    1.0,
                    "policy-order",
                )
            )
            return SimpleNamespace(hits=())

        async def get_source(self, source_id, cursor, limit):
            return SimpleNamespace(
                documents=SimpleNamespace(
                    items=(
                        SimpleNamespace(
                            revision_id="revision-a",
                            status=SimpleNamespace(value="ready"),
                        ),
                    ),
                    next_cursor=None,
                )
            )

        async def resolve_conversation_selection(self, revision_ids):
            return (), SimpleNamespace(
                decision_id="different-decision",
                policy_version="different-policy",
                active_corpus_version="different-corpus",
                acl_digest=_sha("e"),
            )

    runtime = SimpleNamespace(
        http_services=SimpleNamespace(knowledge=Knowledge(), asset_catalog=SimpleNamespace())
    )
    path = module.RuntimeKnowledgePath(runtime, dataset)
    captured.begin_attempt()
    with pytest.raises(Exception) as captured_error:
        await path.run_case(dataset["cases"][0])

    assert isinstance(captured_error.value, module.PolicyDigestMismatch)
    assert captured.end_attempt() == []
    assert operations == []


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
async def test_failed_provider_io_receipt_drives_bounded_retry_then_success() -> None:
    from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelGatewayUnavailable,
        ModelOperation,
        ModelRequest,
        ModelResult,
        ModelUsage,
    )

    module = _runner()
    evaluator = _evaluator()
    dataset = _dataset()
    dataset["cases"] = [dataset["cases"][0]]
    scope = ProjectScopeContext(
        enterprise_id="tenant",
        project_id="project-a",
        actor_id="actor",
        identity_mode=IdentityMode.VALIDATION,
    )
    delegate_calls = 0

    class FlakyGateway:
        async def generate_structured(self, request):
            nonlocal delegate_calls
            delegate_calls += 1
            if delegate_calls == 1:
                raise ModelGatewayUnavailable()
            audit = ModelCallAudit(
                scope=scope,
                alias=request.alias,
                operation=ModelOperation.STRUCTURED,
                prompt_digest=request.prompt_digest,
                schema_digest=request.schema_digest,
                context_digest=_sha("9"),
                idempotency_key=request.idempotency_key,
                actual_provider="approved-provider",
                actual_model="approved-provider/approved-model",
                usage=ModelUsage(2, 1),
                governance_digests=(_sha("3"), _sha("4")),
            )
            return ModelResult(
                {},
                "approved-provider/approved-model",
                ModelUsage(2, 1),
                "approved-provider",
                audit,
                "provider-success",
                "gateway-success",
            )

    class FlakyPath:
        def __init__(self, gateway):
            self.gateway = gateway

        async def resolve_authority(self, case):
            return {
                "actualEnterpriseId": "tenant",
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
                    governance_digests=(_sha("3"), _sha("4")),
                )
            )
            return {
                "actualPolicyDigest": _sha("1"),
                "retrieved": [],
                "abstained": False,
                "claims": [],
                "citations": [],
            }

        async def resolve_citation(self, citation):
            raise AssertionError("no citations")

    observations = await module.run_dataset(
        dataset,
        gateway=FlakyGateway(),
        path_factory=FlakyPath,
        timeout_ms=1000,
        provider_call_budget=2,
        cache={},
        max_retries_per_case=1,
        approved_mapping=APPROVED,
        production_routes=PRODUCTION_ROUTES,
    )

    attempts = observations["cases"][0]["execution"]["attempts"]
    assert delegate_calls == 2
    assert observations["execution"]["providerCalls"] == 2
    assert observations["execution"]["retryCount"] == 1
    assert attempts[0]["providerCalls"][0]["status"] == "error"
    assert attempts[0]["providerCalls"][0]["errorCode"] == "ModelGatewayUnavailable"
    assert attempts[0]["providerCalls"][0]["retryable"] is True
    assert attempts[1]["retryReason"] == "ModelGatewayUnavailable"
    assert attempts[1]["providerCalls"][0]["status"] == "success"

    evaluator.evaluate_run(
        dataset,
        observations,
        min_cases=1,
        require_real=True,
        approved_mapping=APPROVED,
        provider_call_budget=2,
        max_retries_per_case=1,
    )
    attempts[1]["retryReason"] = "ForgedRetryReason"
    with pytest.raises(ValueError, match="retry reason"):
        evaluator.evaluate_run(
            dataset,
            observations,
            min_cases=1,
            approved_mapping=APPROVED,
            provider_call_budget=2,
            max_retries_per_case=1,
        )


def _production_litellm_gateway(handler, *, max_retries: int):
    import httpx

    from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
    from tap.modules.ai.adapters.litellm import (
        LiteLLMModelGateway,
        LiteLLMModelGatewayConfig,
        ProviderModelMapping,
    )

    async def redact(text: str) -> str:
        return text

    scope = ProjectScopeContext(
        enterprise_id="tenant",
        project_id="project-a",
        actor_id="actor",
        identity_mode=IdentityMode.VALIDATION,
    )

    return LiteLLMModelGateway(
        LiteLLMModelGatewayConfig(
            base_url="https://litellm.example",
            api_key="not-a-real-key",
            chat_alias="tapper-chat",
            embedding_alias="tapper-embedding",
            chat_model=ProviderModelMapping("approved-provider", "approved-model"),
            embedding_model=ProviderModelMapping("approved-provider", "approved-embedding"),
            embedding_dimension=2,
            max_retries=max_retries,
        ),
        scope=scope,
        redact=redact,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_quality_runner_rejects_litellm_internal_retries() -> None:
    module = _runner()

    async def unused(_request):
        raise AssertionError("startup validation must not perform HTTP")

    retrying = _production_litellm_gateway(unused, max_retries=1)
    single_attempt = _production_litellm_gateway(unused, max_retries=0)

    with pytest.raises(ValueError, match="internal retries"):
        module._require_single_http_attempt_gateway(retrying)
    assert module._require_single_http_attempt_gateway(single_attempt) is single_attempt


def test_approval_routes_must_exactly_match_production_litellm_before_http() -> None:
    module = _runner()
    posts = 0

    async def handler(_request):
        nonlocal posts
        posts += 1
        raise AssertionError("route mismatch must stop before HTTP")

    gateway = _production_litellm_gateway(handler, max_retries=0)
    production_routes = module._production_routes_from_gateway(gateway)
    mismatched = deepcopy(APPROVED)
    mismatched["routes"] = deepcopy(APPROVED["routes"])
    mismatched["routes"][2]["actualModel"] = "configured-model-b"
    with pytest.raises(ValueError, match="routes do not match"):
        module._require_approved_production_routes(mismatched, production_routes)

    missing_embedding = deepcopy(APPROVED)
    missing_embedding["routes"] = [
        item for item in missing_embedding["routes"] if item["operation"] != "embed"
    ]
    with pytest.raises(ValueError, match="routes do not match"):
        module._require_approved_production_routes(missing_embedding, production_routes)

    module._require_approved_production_routes(APPROVED, production_routes)
    assert posts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["embed", "chat", "structured"])
async def test_runner_outer_retry_receipts_match_real_litellm_http_attempts(
    operation: str,
) -> None:
    import httpx

    from tap.modules.ai.domain.models import (
        ModelOperation,
        ModelRequest,
        schema_digest,
        text_digest,
    )

    module = _runner()
    dataset = _dataset()
    dataset["cases"] = [dataset["cases"][0]]
    posts: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        if len(posts) == 1:
            return httpx.Response(503)
        if operation == "embed":
            return httpx.Response(
                200,
                json={
                    "model": "approved-provider/approved-embedding",
                    "data": [{"index": 0, "embedding": [0.6, 0.8]}],
                    "usage": {"prompt_tokens": 2, "total_tokens": 2},
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "approved-provider/approved-model",
                "choices": [
                    {
                        "message": {
                            "content": '{"answer":"ok"}' if operation == "structured" else "ok"
                        }
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    gateway = _production_litellm_gateway(handler, max_retries=0)
    output_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }

    class TransportPath:
        def __init__(self, captured):
            self.gateway = captured

        async def resolve_authority(self, case):
            return {
                "actualEnterpriseId": "tenant",
                "actualProjectId": "project-a",
                "authorizedSourceIds": ["source-a"],
            }

        async def run_case(self, case):
            model_operation = ModelOperation(operation)
            prompt = "quality transport probe"
            request = ModelRequest(
                gateway.scope,
                "tapper-embedding" if operation == "embed" else "tapper-chat",
                model_operation,
                prompt,
                text_digest(prompt),
                "safe context",
                1.0,
                f"quality-{operation}",
                output_schema if operation == "structured" else None,
                schema_digest(output_schema) if operation == "structured" else None,
            )
            method = {
                "embed": "embed",
                "chat": "chat",
                "structured": "generate_structured",
            }[operation]
            await getattr(self.gateway, method)(request)
            return {
                "actualPolicyDigest": _sha("1"),
                "retrieved": [],
                "abstained": True,
                "claims": [],
                "citations": [],
            }

        async def resolve_citation(self, citation):
            raise AssertionError("no citations")

    observations = await module.run_dataset(
        dataset,
        gateway=gateway,
        path_factory=TransportPath,
        timeout_ms=1000,
        provider_call_budget=2,
        cache={},
        max_retries_per_case=1,
        approved_mapping=APPROVED,
        production_routes=PRODUCTION_ROUTES,
    )
    attempts = observations["cases"][0]["execution"]["attempts"]
    receipts = [attempt["providerCalls"] for attempt in attempts]
    assert len(posts) == 2
    assert [len(items) for items in receipts] == [1, 1]
    assert receipts[0][0]["status"] == "error"
    assert attempts[1]["retryReason"] == "ModelGatewayUnavailable"
    assert receipts[1][0]["status"] == "success"
    assert receipts[1][0]["requestedAlias"] == PRODUCTION_ROUTES[operation]["logicalAlias"]
    assert receipts[1][0]["requestedProvider"] == PRODUCTION_ROUTES[operation]["actualProvider"]
    assert receipts[1][0]["requestedModel"] == PRODUCTION_ROUTES[operation]["actualModel"]

    budget_posts: list[httpx.Request] = []

    async def budget_handler(request: httpx.Request) -> httpx.Response:
        budget_posts.append(request)
        return httpx.Response(503)

    budget_gateway = _production_litellm_gateway(budget_handler, max_retries=0)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        await module.run_dataset(
            dataset,
            gateway=budget_gateway,
            path_factory=TransportPath,
            timeout_ms=1000,
            provider_call_budget=1,
            cache={},
            max_retries_per_case=1,
            approved_mapping=APPROVED,
            production_routes=PRODUCTION_ROUTES,
        )
    assert len(budget_posts) == 1


@pytest.mark.asyncio
async def test_approval_expiry_stops_next_case_before_reserve_and_http() -> None:
    from datetime import UTC, datetime

    import httpx

    from tap.modules.ai.domain.models import (
        ModelOperation,
        ModelRequest,
        schema_digest,
        text_digest,
    )

    module = _runner()
    posts = 0

    async def handler(_request):
        nonlocal posts
        posts += 1
        return httpx.Response(
            200,
            json={
                "model": "approved-provider/approved-model",
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    class Clock:
        current = datetime(2026, 9, 9, tzinfo=UTC)

        def __call__(self):
            return self.current

    clock = Clock()
    expiry = datetime(2026, 9, 10, tzinfo=UTC)
    gateway = _production_litellm_gateway(handler, max_retries=0)
    budget = module.ProviderCallBudget(2, expires_at=expiry, clock=clock)
    captured = module.CapturingModelGateway(
        gateway, budget, PRODUCTION_ROUTES, clock=clock, expires_at=expiry
    )
    output_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    prompt = "quality expiry probe"
    request = ModelRequest(
        gateway.scope,
        "tapper-chat",
        ModelOperation.STRUCTURED,
        prompt,
        text_digest(prompt),
        "safe context",
        1.0,
        "expiry-case",
        output_schema,
        schema_digest(output_schema),
    )

    captured.begin_attempt()
    await captured.generate_structured(request)
    assert len(captured.end_attempt()) == 1
    clock.current = expiry
    captured.begin_attempt()
    with pytest.raises(module.ApprovalExpired):
        await captured.generate_structured(request)

    assert captured.end_attempt() == []
    assert posts == 1
    assert budget.count == 1

    dataset = _dataset()
    first_case = deepcopy(dataset["cases"][0])
    second_case = deepcopy(first_case)
    second_case["caseId"] = "answerable-after-expiry"
    second_case["question"] = "What is alpha after the approval expires?"
    dataset["cases"] = [first_case, second_case]
    posts = 0
    run_gateway = _production_litellm_gateway(handler, max_retries=0)

    class RunClock:
        calls = 0

        def __call__(self):
            self.calls += 1
            # Run start, case 1 start, attempt start, reserve, call end, case 1 end.
            # The next read is case 2 start and must fail before authority/model work.
            return datetime(2026, 9, 9, tzinfo=UTC) if self.calls <= 6 else expiry

    run_clock = RunClock()

    class TwoCasePath:
        def __init__(self, gateway):
            self.gateway = gateway

        async def resolve_authority(self, case):
            return {
                "actualEnterpriseId": "tenant",
                "actualProjectId": "project-a",
                "authorizedSourceIds": ["source-a"],
            }

        async def run_case(self, case):
            await self.gateway.generate_structured(request)
            return {
                "actualPolicyDigest": _sha("1"),
                "retrieved": [],
                "abstained": True,
                "claims": [],
                "citations": [],
            }

        async def resolve_citation(self, citation):
            raise AssertionError("no citations")

    run_approval = {**APPROVED, "expiresAtUtc": "2026-09-10T00:00:00Z"}
    with pytest.raises(module.ApprovalExpired):
        await module.run_dataset(
            dataset,
            gateway=run_gateway,
            path_factory=TwoCasePath,
            timeout_ms=1000,
            provider_call_budget=2,
            cache={},
            max_retries_per_case=0,
            approved_mapping=run_approval,
            production_routes=PRODUCTION_ROUTES,
            clock=run_clock,
        )

    assert posts == 1
    assert run_clock.calls == 7


@pytest.mark.parametrize("mutation", ["call", "case", "run"])
def test_evaluator_rejects_execution_finishing_after_approval_expiry(
    mutation: str,
) -> None:
    module = _evaluator()
    dataset = _dataset()
    observations = _observations(dataset)
    if mutation == "call":
        observations["cases"][0]["execution"]["attempts"][0]["providerCalls"][0]["endedAtUtc"] = (
            "2026-09-30T00:00:00.001Z"
        )
    elif mutation == "case":
        observations["cases"][0]["execution"]["finishedAtUtc"] = "2026-09-30T00:00:00.001Z"
    else:
        observations["execution"]["finishedAtUtc"] = "2026-09-30T00:00:00.001Z"

    with pytest.raises(ValueError, match="approval expiry"):
        module.evaluate_run(
            dataset,
            observations,
            min_cases=4,
            require_real=True,
            approved_mapping=APPROVED,
        )


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
                actual_model="approved-provider/approved-model",
                usage=ModelUsage(1, 1),
                governance_digests=(_sha("3"), _sha("4")),
            )
            return ModelResult(
                {},
                "approved-provider/approved-model",
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
            return {
                "actualEnterpriseId": "tenant",
                "actualProjectId": "project-a",
                "authorizedSourceIds": ["source-a"],
            }

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
            return {
                "actualPolicyDigest": _sha("1"),
                "retrieved": [],
                "abstained": False,
                "claims": [],
                "citations": [],
            }

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
            production_routes=PRODUCTION_ROUTES,
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
                "actualEnterpriseId": "tenant",
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
        production_routes=PRODUCTION_ROUTES,
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
                "actualEnterpriseId": "tenant",
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
            production_routes=PRODUCTION_ROUTES,
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
