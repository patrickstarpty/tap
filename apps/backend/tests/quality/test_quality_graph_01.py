import hashlib
import importlib.util
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
    return {
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
