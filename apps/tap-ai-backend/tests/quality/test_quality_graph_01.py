import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "evaluate-quality-graph.py"


def _evaluator():
    spec = importlib.util.spec_from_file_location("evaluate_quality_graph", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner():
    path = ROOT / "scripts" / "run-quality-graph-candidate.py"
    spec = importlib.util.spec_from_file_location("run_quality_graph", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile():
    documents = []
    for index in range(20):
        content = f"# Document {index:02d}\n\nA structured requirement.\n"
        documents.append(
            {
                "documentId": f"document-{index:02d}",
                "content": content,
                "contentDigest": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    labels = []
    for index in range(200):
        labels.append(
            {
                "labelId": f"label-{index:03d}",
                "documentId": f"document-{index % 20:02d}",
                "kind": "edge" if index % 3 else "merge",
                "origin": "INFERRED" if index % 5 == 0 else "EXTRACTED",
                "predicted": True,
                "correct": True,
                "evidenceResolvable": True,
                "provenanceComplete": True,
                "reviewer": "reviewer-1",
            }
        )
    profile = {
        "schemaVersion": "quality-graph-profile-v1",
        "profileId": "QUALITY-GRAPH-01",
        "dataset": {"version": "unit-v1", "reviewStatus": "approved"},
        "bindings": {
            "modelAlias": "tapper-graph",
            "actualModel": "provider/model",
            "promptDigest": "sha256:" + "a" * 64,
            "schemaDigest": "sha256:" + "b" * 64,
            "evaluatorDigest": "sha256:" + "c" * 64,
        },
        "documents": documents,
        "labels": labels,
    }
    profile["bindings"].update(_evaluator().current_bindings())

    def digest(value):
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()
        )

    dataset_material = {
        "profileId": profile["profileId"],
        "version": profile["dataset"]["version"],
        "documents": documents,
        "labels": [
            {
                key: value
                for key, value in label.items()
                if key
                not in {
                    "predicted",
                    "correct",
                    "evidenceResolvable",
                    "provenanceComplete",
                    "reviewer",
                }
            }
            for label in labels
        ],
    }
    config_material = {
        "minimumDocuments": 20,
        "minimumLabels": 200,
        "evidenceProvenanceResolution": "100%",
        "extractedEdgeEvidencePrecision": "100%",
        "relationPrecision": ">=90%",
        "incorrectEntityMergeRate": "<=1%",
        "inferredProvenanceCompleteness": "100%",
        "minimumRevisionsPerCandidate": 2,
    }
    model_material = profile["bindings"]
    dataset_digest = digest(dataset_material)
    config_digest = digest(config_material)
    model_digest = digest(model_material)
    evidence_cases = []
    for index in range(10):
        case_id = f"graph-set-{index + 1:03d}"
        output = {
            "sourceRevisionIds": [
                documents[index * 2]["documentId"]
                + "-revision-"
                + documents[index * 2]["contentDigest"].removeprefix("sha256:")[:12],
                documents[index * 2 + 1]["documentId"]
                + "-revision-"
                + documents[index * 2 + 1]["contentDigest"].removeprefix("sha256:")[:12],
            ],
            "nodes": [{"nodeId": f"node-{index}"}],
            "edges": [],
            "evidence": [],
            "provenance": [],
        }
        output_digest = digest(output)
        request_digest = digest(
            {
                "caseId": case_id,
                "datasetDigest": dataset_digest,
                "configDigest": config_digest,
                "modelDigest": model_digest,
            }
        )
        candidate_digest = digest(
            {
                "caseId": case_id,
                "requestDigest": request_digest,
                "outputDigest": output_digest,
                "datasetDigest": dataset_digest,
                "configDigest": config_digest,
                "modelDigest": model_digest,
            }
        )
        evidence_cases.append(
            {
                "caseId": case_id,
                "status": "completed",
                "executionMode": "real",
                "requestId": f"graph-request-{index + 1:03d}",
                "requestDigest": request_digest,
                "output": output,
                "outputDigest": output_digest,
                "candidateDigest": candidate_digest,
                "reviewedOutputDigest": output_digest,
                "reviewedCandidateDigest": candidate_digest,
                "reviewJudgments": [
                    {
                        "reviewer": "reviewer-1",
                        "approved": True,
                        "reviewRunId": "graph-run-current",
                    }
                ],
            }
        )
    profile["runEvidence"] = {
        "schemaVersion": "quality-candidate-evidence-v1",
        "runId": "graph-run-current",
        "runStatus": "completed",
        "executionMode": "real",
        "datasetDigest": dataset_digest,
        "configDigest": config_digest,
        "modelDigest": model_digest,
        "cases": evidence_cases,
    }
    profile["journeyEvidence"] = [
        {
            "kind": kind,
            "status": "passed",
            "executionMode": "real",
            "executionId": f"graph-{kind}-current",
            "artifactDigest": digest({"kind": kind, "run": "current"}),
            "datasetDigest": dataset_digest,
            "configDigest": config_digest,
            "modelDigest": model_digest,
        }
        for kind in ("contract", "api", "answer", "browser", "restart")
    ]
    return profile


def test_quality_graph_thresholds_pass_only_with_complete_independent_labels():
    report = _evaluator().evaluate(_profile())
    assert report["passed"] is True
    assert report["metrics"]["evidenceProvenanceResolution"]["actual"] == "200/200"


@pytest.mark.parametrize(
    ("mutation", "metric"),
    [
        (
            lambda profile: profile["labels"][2].update(evidenceResolvable=False),
            "evidenceProvenanceResolution",
        ),
        (
            lambda profile: profile["labels"][1].update(correct=False),
            "extractedEdgeEvidencePrecision",
        ),
        (
            lambda profile: [profile["labels"][index].update(correct=False) for index in range(25)],
            "relationPrecision",
        ),
        (
            lambda profile: [profile["labels"][index].update(correct=False) for index in (0, 3, 6)],
            "incorrectEntityMergeRate",
        ),
        (
            lambda profile: profile["labels"][5].update(provenanceComplete=False),
            "inferredProvenanceCompleteness",
        ),
    ],
)
def test_quality_graph_fails_with_the_specific_metric(mutation, metric):
    profile = _profile()
    mutation(profile)
    report = _evaluator().evaluate(profile)
    assert report["passed"] is False
    assert report["metrics"][metric]["passed"] is False


def test_real_gate_rejects_fake_or_unreviewed_profiles():
    module = _evaluator()
    profile = _profile()
    profile["bindings"]["actualModel"] = "fake/deterministic-graph-v1"
    with pytest.raises(ValueError, match="real model"):
        module.validate_real_profile(profile)

    profile = _profile()
    profile["labels"][0]["reviewer"] = "pending-independent-human-review"
    with pytest.raises(ValueError, match="independent human reviewer"):
        module.validate_real_profile(profile)


def test_real_runner_rejects_unreviewed_labels_before_provider_setup():
    profile = _profile()
    profile["dataset"]["reviewStatus"] = "pending"
    with pytest.raises(ValueError, match="approved human review"):
        _runner().validate_approved_review(profile)


def test_real_gate_rejects_stale_prompt_schema_and_evaluator_bindings():
    module = _evaluator()
    for name in ("promptDigest", "schemaDigest", "evaluatorDigest"):
        profile = _profile()
        profile["bindings"].update(module.current_bindings())
        profile["bindings"][name] = "sha256:" + "0" * 64
        with pytest.raises(ValueError, match=name):
            module.validate_real_profile(profile)

    profile = _profile()
    profile["labels"][0]["reviewer"] = "machine-observation-requires-independent-human-review"
    with pytest.raises(ValueError, match="independent human reviewer"):
        _runner().validate_approved_review(profile)


def test_profile_rejects_content_digest_tampering_and_orphan_labels():
    module = _evaluator()
    profile = _profile()
    profile["documents"][0]["content"] += "tampered"
    with pytest.raises(ValueError, match="content digest"):
        module.evaluate(profile)
    profile = _profile()
    profile["labels"][0]["documentId"] = "missing-document"
    with pytest.raises(ValueError, match="document"):
        module.evaluate(profile)
    profile = _profile()
    profile["dataset"]["reviewStatus"] = "pending"
    with pytest.raises(ValueError, match="review"):
        module.validate_real_profile(profile)


def test_real_gate_requires_current_multi_revision_candidate_outputs():
    module = _evaluator()
    profile = _profile()
    profile["runEvidence"]["cases"][0]["output"]["sourceRevisionIds"] = ["only-one-revision"]

    with pytest.raises(ValueError, match="multi-revision"):
        module.validate_real_profile(profile)


def test_real_gate_binds_contract_api_answer_browser_and_restart_evidence():
    module = _evaluator()
    profile = _profile()
    profile["journeyEvidence"] = [
        item for item in profile["journeyEvidence"] if item["kind"] != "browser"
    ]

    with pytest.raises(ValueError, match="every required kind"):
        module.validate_real_profile(profile)
