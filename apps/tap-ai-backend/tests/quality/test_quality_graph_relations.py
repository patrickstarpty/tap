from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "evaluate-graph-relations.py"


def _evaluator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_graph_relations", SCRIPT)
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


def _regression_document() -> dict[str, object]:
    return {
        "schemaVersion": "graph-regression-questions-v1",
        "groups": [{"id": "demo-group", "requiredCount": 0, "questions": []}],
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
