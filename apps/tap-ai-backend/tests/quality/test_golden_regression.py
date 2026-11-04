"""Golden set regression for knowledge answers with the deterministic model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
from tap.modules.ai.adapters.litellm_catalog import ModelRoles
from tap.modules.ai.domain.models import (
    ModelOperation,
    ModelRequest,
    schema_digest,
    text_digest,
)
from tap.modules.knowledge.adapters.litellm import _ANSWER_SCHEMA
from tap.quality.golden import (
    GoldenCase,
    GoldenReport,
    compare,
    deterministic_golden_gateway,
    load_cases,
    run_golden,
)
from tap.testing.deterministic_model_gateway import DeterministicModelGateway

GOLDEN = Path(__file__).resolve().parent / "golden"
CASES = GOLDEN / "kb-golden.v1.jsonl"
CORPUS = GOLDEN / "corpus"
BASELINE = GOLDEN / "baseline.fake.json"


def _write(tmp_path: Path, *rows: object) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    return path


def _row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "kb-fact-900",
        "category": "fact",
        "question": "问题？",
        "sources": ["a.md"],
        "expect": {"abstain": False, "citedDocuments": ["a.md"], "mustContain": ["x"]},
    }
    row.update(changes)
    return row


def test_load_cases_reads_the_committed_golden_set() -> None:
    cases = load_cases(CASES)

    categories = [case.category for case in cases]
    assert len(cases) >= 20
    assert categories.count("fact") >= 10
    assert categories.count("procedure") >= 4
    assert categories.count("abstain") >= 4
    assert sum(case.question.isascii() for case in cases) == 2
    assert len({case.id for case in cases}) == len(cases)
    corpus = {path.name for path in CORPUS.glob("*.md")}
    assert len(corpus) >= 6
    assert all(set(case.sources) <= corpus for case in cases)
    assert all(case.abstain == (case.category == "abstain") for case in cases)


def test_load_cases_maps_fields(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _row(
            category="relation",
            expect={
                "abstain": False,
                "citedDocuments": ["a.md"],
                "mustContain": [],
                "relations": ["DEPENDS_ON"],
            },
        ),
    )

    assert load_cases(path) == (
        GoldenCase(
            id="kb-fact-900",
            category="relation",
            question="问题？",
            sources=("a.md",),
            abstain=False,
            cited_documents=("a.md",),
            must_contain=(),
            relations=("DEPENDS_ON",),
        ),
    )


@pytest.mark.parametrize(
    "row",
    [
        _row(category="opinion"),
        {key: value for key, value in _row().items() if key != "question"},
        {key: value for key, value in _row().items() if key != "expect"},
        _row(sources=[]),
        _row(expect={"citedDocuments": ["a.md"], "mustContain": []}),
        _row(expect={"abstain": False, "citedDocuments": [], "mustContain": []}),
        _row(expect={"abstain": False, "citedDocuments": ["b.md"], "mustContain": []}),
        _row(expect={"abstain": "no", "citedDocuments": ["a.md"], "mustContain": []}),
    ],
    ids=(
        "unknown-category",
        "missing-question",
        "missing-expect",
        "no-sources",
        "missing-abstain",
        "answerable-without-citation",
        "citation-outside-sources",
        "non-boolean-abstain",
    ),
)
def test_load_cases_rejects_invalid_rows(tmp_path: Path, row: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        load_cases(_write(tmp_path, row))


def test_load_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_cases(_write(tmp_path, _row(), _row()))


def test_compare_reports_any_metric_decrease_and_missing_metrics() -> None:
    report = GoldenReport(
        metrics={
            "fact": {"citationRecall": 0.9, "keywordRecall": 1.0},
            "total": {"abstainAccuracy": 1.0},
        },
        cases=(),
    )

    assert compare(report, {"fact": {"citationRecall": 0.9, "keywordRecall": 0.5}}) == []
    regressions = compare(
        report,
        {
            "fact": {"citationRecall": 0.95},
            "total": {"abstainAccuracy": 1.0, "answered": 1.0},
        },
    )
    assert len(regressions) == 2
    assert "fact.citationRecall" in regressions[0] and "0.95" in regressions[0]
    assert "total.answered" in regressions[1]


@pytest.mark.asyncio
async def test_best_overlap_mode_cites_the_most_relevant_sentence_or_abstains() -> None:
    config = LiteLLMModelGatewayConfig(
        base_url="http://127.0.0.1:4000",
        api_key="golden",
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=1536,
    )

    async def redact(text: str) -> str:
        return text

    gateway = DeterministicModelGateway(
        config, scope=VALIDATION_SCOPE, redact=redact, answer_mode="best-overlap"
    )

    async def answer(query: str) -> object:
        context = json.dumps(
            {
                "query": query,
                "evidence": [
                    {"label": "S1", "content": "本文档说明发布。代码冻结在发布前 3 天。"},
                    {"label": "S2", "content": "回滚须在 15 分钟内启动。\n回滚后提交复盘。"},
                ],
            },
            ensure_ascii=False,
        )
        schema = _ANSWER_SCHEMA
        request = ModelRequest(
            VALIDATION_SCOPE,
            "qwen-plus",
            ModelOperation.STRUCTURED,
            "prompt",
            text_digest("prompt"),
            context,
            1.0,
            "request",
            schema,
            schema_digest(schema),
        )
        return (await gateway.generate_structured(request)).output

    assert await answer("回滚需要在多少分钟内启动？") == {
        "answer": "回滚须在 15 分钟内启动。",
        "claims": [{"text": "回滚须在 15 分钟内启动。", "evidenceLabels": ["S2"]}],
    }
    assert await answer("食堂几点开饭？") == {"answer": "", "claims": []}


@pytest.mark.asyncio
async def test_golden_set_does_not_regress_against_the_fake_baseline() -> None:
    report = await run_golden(
        load_cases(CASES), corpus_dir=CORPUS, gateway=deterministic_golden_gateway()
    )

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert compare(report, baseline) == []
    assert report.metrics["fact"]["citationRecall"] >= 0.8
    assert report.metrics["abstain"]["abstainAccuracy"] == 1.0


@pytest.mark.asyncio
async def test_relation_golden_cases_return_graph_and_original_text_citations() -> None:
    cases = [case for case in load_cases(CASES) if case.category == "relation"]
    assert len(cases) >= 8
    report = await run_golden(cases, corpus_dir=CORPUS, gateway=deterministic_golden_gateway())
    assert report.metrics["relation"]["relationRecall"] == 1.0
    for case, result in zip(cases, report.cases, strict=True):
        assert set(case.relations) <= set(result.relation_types)
        assert result.cited_documents
        assert result.error is None


def test_relation_recall_counts_only_relations_actually_cited() -> None:
    from tap.quality.golden import CaseResult, _metrics

    case = GoldenCase(
        "relation",
        "relation",
        "A 与 C 的关系？",
        ("a.md",),
        False,
        ("a.md",),
        (),
        ("DEPENDS_ON", "CALLS"),
    )
    result = CaseResult(
        "relation",
        "relation",
        case.question,
        False,
        "A depends on B.",
        ("a.md",),
        relation_types=("DEPENDS_ON",),
    )
    assert _metrics((case,), (result,))["relation"]["relationRecall"] == 0.5
