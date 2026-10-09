from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tap.modules.knowledge.domain.models import (
    AnswerResponse,
    Citation,
    Claim,
    ContentRole,
    DocumentAnchor,
    EdgeCitation,
    ModelCallProvenance,
    RetrievalProfileId,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.quality.evidence import canonical_digest
from tap.quality.graph_corpus import manifest_digest
from tap.quality.graph_relations import ExpectedEdge, GoldenQuestion, match_question

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "evaluate-graph-relations.py"
RUNNER_SCRIPT = ROOT / "scripts" / "run-graph-relations-candidate.py"
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "quality" / "graph-relations"
FIXTURE_GOLDEN = FIXTURE_DIR / "golden-fixture-v1.json"


def _evaluator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_graph_relations", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_graph_relations_candidate", RUNNER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _question(
    index: int,
    *,
    subject: str = "A",
    relation: str = "REQUIRES",
    obj: str = "B",
    source: str = "source-a",
) -> dict[str, object]:
    return {
        "id": f"q-{index:02d}",
        "question": f"Question {index}?",
        "locale": "zh",
        "expectedEntities": [{"label": subject}, {"label": obj}],
        "expectedEdges": [{"subject": subject, "relationType": relation, "object": obj}],
        "expectedSources": [source],
    }


def _golden(
    questions: list[dict[str, object]],
    *,
    labeled_by: str = "qa-reviewer",
    manifest: str = "fixture",
    digest: str = "sha256:" + "a" * 64,
) -> dict[str, object]:
    return {
        "schemaVersion": "graph-relation-golden-v1",
        "labeledBy": labeled_by,
        "labeledAt": "2026-10-06",
        "corpus": {"manifest": manifest, "digest": digest},
        "questions": questions,
    }


def _edge_citation(
    *, subject: str, relation: str, obj: str, citation_id: str = "c-1"
) -> dict[str, object]:
    return {
        "citationId": citation_id,
        "kind": "edge",
        "evidenceLabel": "E1",
        "edgeId": "e-1",
        "subject": {"nodeId": "n-subject", "label": subject},
        "object": {"nodeId": "n-object", "label": obj},
        "relationType": relation,
        "relationLabel": relation,
    }


def _chunk_citation(citation_id: str = "c-chunk-1") -> dict[str, object]:
    return {"citationId": citation_id, "kind": "chunk", "evidenceLabel": "C1", "chunkId": "chunk-1"}


def _answer(
    question_id: str,
    *,
    group: str = "golden",
    citations: list[dict[str, object]] | None = None,
    abstained: bool = False,
) -> dict[str, object]:
    return {
        "questionId": question_id,
        "group": group,
        "abstained": abstained,
        "graphContextStatus": "ready",
        "claims": [],
        "citations": citations or [],
    }


def _observations(
    answers: list[dict[str, object]],
    *,
    execution_mode: str = "fake",
    extraction_mode: str = "fake",
    model_actual: str = "fake/deterministic-tapper",
    golden_digest: str = "sha256:" + "a" * 64,
    corpus_digest: str = "sha256:" + "a" * 64,
    graph_version: str = "graph-v1",
    graph_reasoning: bool | None = None,
) -> dict[str, object]:
    document: dict[str, object] = {
        "schemaVersion": "graph-relation-observations-v1",
        "executionMode": execution_mode,
        "goldenDigest": golden_digest,
        "corpusDigest": corpus_digest,
        "graphVersion": graph_version,
        "extractionMode": extraction_mode,
        "model": {"alias": "fake", "actual": model_actual},
        "startedAt": "2026-10-06T00:00:00Z",
        "finishedAt": "2026-10-06T00:05:00Z",
        "answers": answers,
    }
    if graph_reasoning is not None:
        document["graphReasoning"] = graph_reasoning
    return document


def _write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _run_main(monkeypatch: object, args: list[str]) -> int:
    module = _evaluator()
    monkeypatch.setattr(sys, "argv", ["evaluate-graph-relations.py", *args])
    return int(module.main())


def test_report_lists_correct_missed_wrong_per_question(tmp_path, monkeypatch):
    golden = _golden(
        [
            _question(1, subject="A1", obj="B1"),
            _question(2, subject="A2", obj="B2"),
            _question(3, subject="A3", obj="B3"),
        ]
    )
    answers = [
        _answer("q-01", citations=[_edge_citation(subject="A1", relation="REQUIRES", obj="B1")]),
        # q-02 is missing from observations entirely -> counted as "miss".
        _answer(
            "q-03",
            citations=[
                _edge_citation(subject="X", relation="USES", obj="Y", citation_id="c-wrong")
            ],
        ),
    ]
    observations = _observations(answers)

    golden_path = _write(tmp_path / "golden.json", golden)
    observations_path = _write(tmp_path / "observations.json", observations)
    report_path = tmp_path / "report.json"

    exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
        ],
    )

    assert exit_code == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    statuses = {item["id"]: item["status"] for item in report["questions"]}
    assert statuses == {"q-01": "pass", "q-02": "miss", "q-03": "wrong"}
    assert report["passed"] is False
    assert report["summary"]["passedCount"] == 1
    assert report["summary"]["total"] == 3
    assert report["regression"]["groups"] == []
    assert report["schemaVersion"] == "graph-relation-report-v1"
    assert report["evaluatorDigest"].startswith("sha256:")


def _thirty_question_golden() -> dict[str, object]:
    return _golden(
        [_question(index, subject=f"S{index}", obj=f"O{index}") for index in range(1, 31)]
    )


def _thirty_question_answers(passed_count: int) -> list[dict[str, object]]:
    answers = []
    for index in range(1, 31):
        question_id = f"q-{index:02d}"
        if index <= passed_count:
            citations = [_edge_citation(subject=f"S{index}", relation="REQUIRES", obj=f"O{index}")]
        else:
            citations = []
        answers.append(_answer(question_id, citations=citations))
    return answers


def test_passes_at_24_of_30_and_fails_at_23(tmp_path, monkeypatch):
    golden = _thirty_question_golden()
    golden_path = _write(tmp_path / "golden.json", golden)

    passing_observations = _observations(_thirty_question_answers(24))
    passing_observations_path = _write(tmp_path / "observations-24.json", passing_observations)
    passing_report_path = tmp_path / "report-24.json"
    passing_exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(passing_observations_path),
            "--report",
            str(passing_report_path),
        ],
    )
    assert passing_exit_code == 0
    passing_report = json.loads(passing_report_path.read_text(encoding="utf-8"))
    assert passing_report["summary"]["passedCount"] == 24
    assert passing_report["summary"]["passed"] is True
    assert passing_report["passed"] is True

    failing_observations = _observations(_thirty_question_answers(23))
    failing_observations_path = _write(tmp_path / "observations-23.json", failing_observations)
    failing_report_path = tmp_path / "report-23.json"
    failing_exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(failing_observations_path),
            "--report",
            str(failing_report_path),
        ],
    )
    assert failing_exit_code == 1
    failing_report = json.loads(failing_report_path.read_text(encoding="utf-8"))
    assert failing_report["summary"]["passedCount"] == 23
    assert failing_report["summary"]["passed"] is False
    assert failing_report["passed"] is False


def _regression_document(*, question_count: int = 17) -> dict[str, object]:
    questions = [f"r-{index:02d}" for index in range(question_count)]
    return {
        "schemaVersion": "graph-regression-questions-v1",
        "groups": [{"id": "demo-group", "requiredCount": question_count, "questions": questions}],
    }


def _incomplete_regression_document() -> dict[str, object]:
    return {
        "schemaVersion": "graph-regression-questions-v1",
        "groups": [{"id": "demo-group", "requiredCount": 17, "questions": []}],
    }


def _regression_group_answers(*, grounded: int, total: int) -> list[dict[str, object]]:
    answers = []
    for index in range(total):
        if index < grounded:
            answers.append(
                _answer(f"r-{index:02d}", group="demo-group", citations=[_chunk_citation()])
            )
        else:
            answers.append(_answer(f"r-{index:02d}", group="demo-group", abstained=True))
    return answers


def test_regression_group_must_not_drop_below_baseline(tmp_path, monkeypatch):
    golden = _golden([_question(1)])
    golden_path = _write(tmp_path / "golden.json", golden)
    regression_path = _write(tmp_path / "regression.json", _regression_document())

    passing_golden_answer = _answer(
        "q-01", citations=[_edge_citation(subject="A", relation="REQUIRES", obj="B")]
    )
    baseline_observations = _observations(
        [passing_golden_answer, *_regression_group_answers(grounded=16, total=17)]
    )
    baseline_path = _write(tmp_path / "baseline.json", baseline_observations)

    # 16/17 against a 16/17 baseline must pass.
    passing_observations = _observations(
        [passing_golden_answer, *_regression_group_answers(grounded=16, total=17)]
    )
    passing_observations_path = _write(tmp_path / "observations-pass.json", passing_observations)
    passing_report_path = tmp_path / "report-pass.json"
    passing_exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(passing_observations_path),
            "--report",
            str(passing_report_path),
            "--regression",
            str(regression_path),
            "--baseline-observations",
            str(baseline_path),
        ],
    )
    assert passing_exit_code == 0
    passing_report = json.loads(passing_report_path.read_text(encoding="utf-8"))
    assert passing_report["regression"]["groups"] == [
        {
            "id": "demo-group",
            "grounded": 16,
            "total": 17,
            "baselineGrounded": 16,
            "baselineTotal": 17,
            "passed": True,
        }
    ]
    assert passing_report["passed"] is True

    # 15/17 against the same 16/17 baseline must fail, even though the golden question still passes.
    failing_observations = _observations(
        [passing_golden_answer, *_regression_group_answers(grounded=15, total=17)]
    )
    failing_observations_path = _write(tmp_path / "observations-fail.json", failing_observations)
    failing_report_path = tmp_path / "report-fail.json"
    failing_exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(failing_observations_path),
            "--report",
            str(failing_report_path),
            "--regression",
            str(regression_path),
            "--baseline-observations",
            str(baseline_path),
        ],
    )
    assert failing_exit_code == 1
    failing_report = json.loads(failing_report_path.read_text(encoding="utf-8"))
    assert failing_report["regression"]["groups"][0]["passed"] is False
    assert failing_report["summary"]["passed"] is True
    assert failing_report["passed"] is False


def _human_labeled_golden(*, labeled_by: str = "qa-reviewer") -> dict[str, object]:
    questions = [_question(index, subject=f"S{index}", obj=f"O{index}") for index in range(1, 21)]
    return _golden(questions, labeled_by=labeled_by)


def test_real_mode_rejects_fake_observations_and_placeholder_labeler(tmp_path, monkeypatch, capsys):
    golden = _human_labeled_golden()
    golden_path = _write(tmp_path / "golden.json", golden)

    fake_observations = _observations([], execution_mode="fake")
    fake_observations_path = _write(tmp_path / "observations-fake.json", fake_observations)
    report_path = tmp_path / "report.json"

    exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(fake_observations_path),
            "--report",
            str(report_path),
            "--real",
        ],
    )
    assert exit_code == 2
    assert "real" in capsys.readouterr().err
    assert not report_path.exists()

    placeholder_golden = _human_labeled_golden(labeled_by="pending-human-labeling")
    placeholder_golden_path = _write(tmp_path / "golden-placeholder.json", placeholder_golden)
    real_observations = _observations(
        [], execution_mode="real", extraction_mode="model", model_actual="litellm/gpt-4o"
    )
    real_observations_path = _write(tmp_path / "observations-real.json", real_observations)

    placeholder_exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(placeholder_golden_path),
            "--observations",
            str(real_observations_path),
            "--report",
            str(report_path),
            "--real",
        ],
    )
    assert placeholder_exit_code == 2
    assert "real" in capsys.readouterr().err
    assert not report_path.exists()


def test_real_mode_rejects_stale_golden_digest(tmp_path, monkeypatch, capsys):
    golden = _human_labeled_golden()
    golden_path = _write(tmp_path / "golden.json", golden)

    stale_observations = _observations(
        [],
        execution_mode="real",
        extraction_mode="model",
        model_actual="litellm/gpt-4o",
        golden_digest="sha256:" + "0" * 64,
    )
    observations_path = _write(tmp_path / "observations.json", stale_observations)
    report_path = tmp_path / "report.json"

    exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
            "--real",
        ],
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "real" in stderr
    assert "goldenDigest" in stderr or "golden set" in stderr
    assert not report_path.exists()


def test_regression_group_scores_declared_questions_and_missing_answers(tmp_path, monkeypatch):
    # The group declares three questions; only two are actually observed. The missing
    # question must still count toward the denominator (fail closed), not shrink it.
    golden = _golden([_question(1)])
    golden_path = _write(tmp_path / "golden.json", golden)
    regression_path = _write(tmp_path / "regression.json", _regression_document(question_count=3))

    passing_golden_answer = _answer(
        "q-01", citations=[_edge_citation(subject="A", relation="REQUIRES", obj="B")]
    )
    group_answers = [
        _answer("r-00", group="demo-group", citations=[_chunk_citation("c-r-00")]),
        # r-01 is declared by the regression document but never observed.
        _answer("r-02", group="demo-group", citations=[_chunk_citation("c-r-02")]),
    ]
    observations = _observations([passing_golden_answer, *group_answers])
    observations_path = _write(tmp_path / "observations.json", observations)
    baseline = _observations([passing_golden_answer, *group_answers])
    baseline_path = _write(tmp_path / "baseline.json", baseline)
    report_path = tmp_path / "report.json"

    exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
            "--regression",
            str(regression_path),
            "--baseline-observations",
            str(baseline_path),
        ],
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["regression"]["groups"] == [
        {
            "id": "demo-group",
            "grounded": 2,
            "total": 3,
            "baselineGrounded": 2,
            "baselineTotal": 3,
            "passed": True,
        }
    ]


def test_regression_rejects_unknown_question_ids(tmp_path, monkeypatch, capsys):
    golden = _golden([_question(1)])
    golden_path = _write(tmp_path / "golden.json", golden)
    # The regression document only declares "r-00"; the observation names "r-99".
    regression_path = _write(tmp_path / "regression.json", _regression_document(question_count=1))

    unknown_answer = _answer("r-99", group="demo-group", citations=[_chunk_citation()])
    observations = _observations([unknown_answer])
    observations_path = _write(tmp_path / "observations.json", observations)
    baseline_path = _write(tmp_path / "baseline.json", _observations([]))
    report_path = tmp_path / "report.json"

    exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
            "--regression",
            str(regression_path),
            "--baseline-observations",
            str(baseline_path),
        ],
    )

    assert exit_code == 2
    assert "demo-group" in capsys.readouterr().err
    assert not report_path.exists()


def _sha(byte_value: bytes) -> str:
    return "sha256:" + hashlib.sha256(byte_value).hexdigest()


def _valid_manifest() -> dict[str, object]:
    policy_entries = [
        {
            "id": f"policy-{index:02d}",
            "title": f"Policy {index}",
            "kind": "policy",
            "sha256": _sha(f"policy-{index}".encode()),
            "url": f"https://example.invalid/policy-{index}.pdf",
        }
        for index in range(1, 9)
    ]
    process_entries = [
        {
            "id": f"process-{index:02d}",
            "title": f"Process {index}",
            "kind": "process",
            "sha256": _sha(f"process-{index}".encode()),
            "path": f".local/graph-real/process-{index:02d}.pdf",
        }
        for index in range(1, 3)
    ]
    return {
        "schemaVersion": "graph-real-corpus-manifest-v1",
        "entries": [*policy_entries, *process_entries],
    }


def test_real_mode_rejects_incomplete_regression_set(tmp_path, monkeypatch, capsys):
    golden = _human_labeled_golden()
    manifest = _valid_manifest()
    corpus_digest = manifest_digest(manifest)
    _write(tmp_path / "manifest.json", manifest)
    golden["corpus"]["manifest"] = "manifest.json"
    # goldenDigest must match the document as written to disk, including the manifest
    # pointer above -- compute it only after corpus.manifest is set.
    golden_digest = canonical_digest(golden)
    golden_path = _write(tmp_path / "golden.json", golden)

    # The shipped skeleton: declared but never filled in (requiredCount 17, questions []).
    regression_path = _write(tmp_path / "regression.json", _incomplete_regression_document())

    real_observations = _observations(
        [],
        execution_mode="real",
        extraction_mode="model",
        model_actual="litellm/gpt-4o",
        golden_digest=golden_digest,
        corpus_digest=corpus_digest,
        graph_version="graph-v1",
    )
    observations_path = _write(tmp_path / "observations.json", real_observations)
    baseline_observations = _observations(
        [],
        corpus_digest=corpus_digest,
        graph_version="graph-v1",
        graph_reasoning=False,
    )
    baseline_path = _write(tmp_path / "baseline.json", baseline_observations)
    report_path = tmp_path / "report.json"

    exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
            "--regression",
            str(regression_path),
            "--baseline-observations",
            str(baseline_path),
            "--real",
        ],
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "real" in stderr
    assert "regression" in stderr or "complete" in stderr
    assert not report_path.exists()


def test_regression_flags_must_be_paired(tmp_path, monkeypatch, capsys):
    golden = _golden([_question(1)])
    golden_path = _write(tmp_path / "golden.json", golden)
    observations = _observations(
        [_answer("q-01", citations=[_edge_citation(subject="A", relation="REQUIRES", obj="B")])]
    )
    observations_path = _write(tmp_path / "observations.json", observations)
    regression_path = _write(tmp_path / "regression.json", _regression_document())
    baseline_path = _write(tmp_path / "baseline.json", _observations([]))
    report_path = tmp_path / "report.json"

    regression_only_exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
            "--regression",
            str(regression_path),
        ],
    )
    assert regression_only_exit_code == 2
    assert capsys.readouterr().err
    assert not report_path.exists()

    baseline_only_exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
            "--baseline-observations",
            str(baseline_path),
        ],
    )
    assert baseline_only_exit_code == 2
    assert capsys.readouterr().err
    assert not report_path.exists()


def test_missing_input_file_exits_2(tmp_path, monkeypatch, capsys):
    missing_golden_path = tmp_path / "does-not-exist.json"
    observations_path = _write(tmp_path / "observations.json", _observations([]))
    report_path = tmp_path / "report.json"

    exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(missing_golden_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
        ],
    )

    assert exit_code == 2
    assert capsys.readouterr().err
    assert not report_path.exists()


@pytest.mark.asyncio
async def test_fake_pipeline_emits_edge_citations_for_the_fixture_pair():
    observations = await _runner().run_fake(golden=FIXTURE_GOLDEN, corpus_dir=FIXTURE_DIR)

    by_id = {item["questionId"]: item for item in observations["answers"]}
    assert any(c["kind"] == "edge" for c in by_id["q-01"]["citations"])

    report = _evaluator().evaluate(json.loads(FIXTURE_GOLDEN.read_text()), observations)
    assert report["passed"] is True and report["questions"][0]["status"] == "pass"
    assert any(
        c["kind"] == "edge" and c["subject"]["label"] == "保单签发"
        for c in by_id["q-03"]["citations"]
    )


def test_real_mode_refuses_fake_extraction_mode(monkeypatch):
    monkeypatch.setenv("TAPPER_GRAPH_EXTRACTION_MODE", "fake")
    with pytest.raises(ValueError, match="TAPPER_GRAPH_EXTRACTION_MODE=model"):
        asyncio.run(
            _runner().run_real(golden=FIXTURE_GOLDEN, regression=None, corpus=Path("missing.json"))
        )


def _minimal_real_edge_answer() -> AnswerResponse:
    """One non-abstained `AnswerResponse` carrying a single R-labeled edge
    citation -- enough to exercise `_real_answer_dict`'s citation flattening
    without any live service (no retrieval, no DB, no model call)."""
    source = SourceRevisionRef(
        source_id="src_" + "1" * 32,
        source_type="doc",
        revision_kind=RevisionKind.BLOB_VERSION,
        revision="rev-1",
        source_content_hash="sha256:" + "a" * 64,
        anchor=DocumentAnchor(start_offset=0, end_offset=10),
    )
    edge_citation = Citation(
        family=SourceFamily.DOC,
        citation_id="citation-r1",
        evidence_label="R1",
        chunk_id="chunk-1",
        logical_chunk_id="logical-1",
        source=source,
        chunk_content_hash="sha256:" + "c" * 64,
        content_role=ContentRole.SOURCE,
        kind="edge",
        edge=EdgeCitation(
            edge_id="e-1",
            graph_version="7",
            subject_node_id="n-subject",
            subject_label="核保流程",
            object_node_id="n-object",
            object_label="健康告知",
            relation_type="REQUIRES",
            relation_label="REQUIRES",
        ),
    )
    answer_text = "核保流程需要健康告知。"
    return AnswerResponse(
        trace_id="trace-1",
        query_plan_id="plan-1",
        context_snapshot_id="context-1",
        corpus_version="tapper-demo-v1",
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        answer=answer_text,
        abstained=False,
        claims=(
            Claim(
                claim_id="claim-1",
                text=answer_text,
                answer_start=0,
                answer_end=len(answer_text),
                citation_ids=("citation-r1",),
            ),
        ),
        citations=(edge_citation,),
        embedding_provenance=ModelCallProvenance("text-embedding-v4", "embed-request"),
        answer_provenance=ModelCallProvenance(
            "qwen-plus", "answer-request", provider_model_id="qwen-plus-2026"
        ),
    )


def test_real_citation_is_flattened_and_scores_as_pass():
    answer_dict = _runner()._real_answer_dict("q-01", "golden", _minimal_real_edge_answer(), "7")

    citation = answer_dict["citations"][0]
    assert citation["kind"] == "edge"
    assert "edge" not in citation
    assert citation["subject"]["label"] == "核保流程"
    assert citation["object"]["label"] == "健康告知"
    assert citation["relationType"] == "REQUIRES"
    assert answer_dict["abstained"] is False
    assert answer_dict["claims"] == [
        {"text": "核保流程需要健康告知。", "citationIds": ["citation-r1"]}
    ]

    question = GoldenQuestion(
        id="q-01",
        question="核保流程和健康告知是什么关系",
        locale="zh",
        expected_entities=(("核保流程", ()), ("健康告知", ())),
        expected_edges=(
            ExpectedEdge(subject="核保流程", relation_type="REQUIRES", object="健康告知"),
        ),
        expected_sources=("underwriting-process",),
    )
    result = match_question(question, answer_dict["citations"])
    assert result.status == "pass"


def test_real_answer_dict_abstains_on_none_answer():
    answer_dict = _runner()._real_answer_dict("q-01", "golden", None, "7")
    assert answer_dict == {
        "questionId": "q-01",
        "group": "golden",
        "abstained": True,
        "graphContextStatus": "UNAVAILABLE",
        "claims": [],
        "citations": [],
    }


def test_run_real_observations_carry_golden_and_corpus_digests():
    golden_json = {"schemaVersion": "graph-relation-golden-v1", "questions": []}
    corpus_value = {"manifestDigest": "sha256:" + "d" * 64, "sources": []}

    observations = _runner()._assemble_real_observations(
        golden_json=golden_json,
        corpus_value=corpus_value,
        answers=[],
        graph_version="3",
        actual_model="qwen-plus-2026",
        settings_default_chat_model="qwen-plus",
        graph_extraction_mode="model",
        graph_reasoning=True,
        started_at="2026-10-09T00:00:00Z",
        finished_at="2026-10-09T00:00:01Z",
    )

    assert observations["goldenDigest"] == canonical_digest(golden_json)
    assert observations["corpusDigest"] == "sha256:" + "d" * 64
    assert observations["model"] == {"alias": "qwen-plus", "actual": "qwen-plus-2026"}
    assert observations["startedAt"] == "2026-10-09T00:00:00Z"
    assert observations["finishedAt"] == "2026-10-09T00:00:01Z"


@pytest.mark.asyncio
async def test_fake_observations_include_timestamps_and_claim_shape():
    observations = await _runner().run_fake(golden=FIXTURE_GOLDEN, corpus_dir=FIXTURE_DIR)

    assert observations["startedAt"]
    assert observations["finishedAt"]
    assert observations["goldenDigest"] == canonical_digest(json.loads(FIXTURE_GOLDEN.read_text()))
    for answer in observations["answers"]:
        assert answer["graphContextStatus"] in {
            "APPLIED",
            "NOT_READY",
            "STALE",
            "FAILED",
            "EMPTY",
        }
        assert isinstance(answer["claims"], list)
        for claim in answer["claims"]:
            assert set(claim) == {"text", "citationIds"}


def test_real_mode_rejects_blank_model_actual(tmp_path, monkeypatch, capsys):
    golden = _human_labeled_golden()
    golden_path = _write(tmp_path / "golden.json", golden)

    blank_model_observations = _observations(
        [], execution_mode="real", extraction_mode="model", model_actual=""
    )
    observations_path = _write(tmp_path / "observations.json", blank_model_observations)
    report_path = tmp_path / "report.json"

    exit_code = _run_main(
        monkeypatch,
        [
            "--golden",
            str(golden_path),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
            "--real",
        ],
    )

    assert exit_code == 2
    assert "real" in capsys.readouterr().err
    assert not report_path.exists()
