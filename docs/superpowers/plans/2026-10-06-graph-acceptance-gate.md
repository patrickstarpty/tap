# 图谱脉络分析验收门禁实施计划（PR 5/5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为知识图谱脉络分析建立可重复的验收门禁：真实语料清单与校验、人工标注的关系类 golden set、比对 R 引用的评测脚本、边与合并实体的抽样导出、1 万节点/5 万边的性能基准，以及记录门禁结果的评审文档与路线图更新。

**Architecture:** 评测逻辑全部放在可导入的纯模块 `tap.quality.graph_relations / graph_corpus / graph_samples / graph_bench`，`scripts/` 下只放薄 CLI，与 `scripts/evaluate-quality-graph.py` + `tap.quality.evidence` 的分工一致。回答的产生与评测分离：`run-graph-relations-candidate.py` 产生观测（fake 模式在进程内用规则式假抽取 + 确定性模型跑完整链路，real 模式复用推荐问题依据校验的进程内"干跑"路径 `freeze → plan → answer_conversation`，不落对话），`evaluate-graph-relations.py` 只读 golden set 与观测并输出报告。真实模型运行一律 opt-in，报告区分 `executionMode: fake | real`。

**Tech Stack:** Python 3.13、SQLAlchemy async（MySQL）、pytest-asyncio、argparse、csv、statistics；无新增依赖。

**Spec:** [知识图谱脉络分析设计](../specs/2026-10-06-knowledge-graph-reasoning-design.md) 第 4 节（4.1–4.5）、5.3 节第 5 项。

## Global Constraints

- 假设 PR 1–4 已合并：`vocabulary.normalize_key`（PR 1 Task 1）、项目图表 `graph_project_version/node/edge/node_source/edge_evidence/alias/community`（spec 1.2）、`ProjectGraphStore`（spec 1.3）、`RelationAnalysisInput / RelationContext` 与 `AgentContext`（spec 2.2–2.3）、`RetrievalCitation.kind: "chunk" | "edge"` 及边字段 `edge_id、graph_version、subject{node_id,label}、object{node_id,label}、relation_type、relation_label`（spec 2.5）、确定性模型在有关系证据时产出 R 标签（PR 3 为 spec 3.4 的 fake E2E 所需）。任务开始前用 `grep -rn "graph_project_edge\|class ProjectGraphStore\|class RelationContext\|relation-analysis" apps/tap-ai-backend/src/tap` 定位实际模块路径；本计划只引用 spec 固定的类型名。
- 真实语料（spec 4.1）：`apps/tap-ai-backend/tests/fixtures/quality/graph-real/manifest.json` 记录 8 份公开友邦保险条款的下载地址与 sha256，脚本下载到 `.local/graph-real/`，**不提交 PDF**；另由人工提供 2–3 份流程类文档（核保、理赔）放入 `.local/graph-real/` 并在 manifest 中登记 sha256。本计划不填写任何 URL；manifest 条目由人工填写，校验脚本检查每条都有 `url + sha256 + title`。
- golden set（spec 4.2）：20–30 条；字段为问题、期望实体、期望关系边（主语、关系类型、宾语、等价替代列表）、期望来源；人工标注并记录标注人与日期。
- 评测（spec 4.2）：`scripts/evaluate-graph-relations.py` 输出每题的正确边、遗漏边、错误边；**通过标准：≥80% 的题目至少命中一条期望边且无错误边；出现错误边的题目不通过**。同时重跑现有 17 条推荐问题与一组普通问题，有依据率不低于加入图谱前（基线 = 同一语料下 `TAPPER_GRAPH_REASONING=0` 的观测）。
- 抽样（spec 4.3）：随机导出 50 条边及证据片段为 CSV，人工核对准确率 ≥85%；导出 30 个跨来源合并实体及其别名与来源，误合并 ≤5%。
- 性能（spec 4.4）：合成生成器写入 1 万节点、5 万边；邻居、路径、总览查询 p95 < 300ms；make 目标 `graph-bench`，不进默认 CI；每类查询 N=200、缓存预热后计时。
- CI 结构性检查：golden set 评测链路在 CI 用规则式假抽取 + 确定性模型跑通（fixture 文档对产生 R 引用）；真实模型评测仅在 opt-in 环境变量下运行，沿用 `quality-graph-real` 的授权变量风格（`Makefile:176-182`）。
- 门禁记录写入 `docs/reviews/2026-10-DD-graph-reasoning-gate.md`（DD 为实际运行日），并更新 V1 总纲与产品路线图对应行（参照 `docs/superpowers/plans/2026-09-29-v1-roadmap.md:18` 的 2026-10-05 记录格式）。
- 任何文本（代码、夹具、文档、CSV 表头）不得出现客户企业名称；不新增 Python 或 Node 依赖；不提交凭据；`.gitignore` 增加 `.local/graph-real/`、`.local/graph-relations/`、`.local/graph-bench/`。
- 提交信息用小写祈使句 Conventional Commit；每个任务结束前 `git diff --check`；测试在 `apps/tap-ai-backend` 下用 `uv run pytest` 运行。

## Review Focus

- 回答没有任何 R 引用但有正确的切片引用：该题判 `miss`，`wrongEdges` 为空，不得判为 `wrong`（Task 2 Step 1 的 `test_zero_edge_citations_is_a_miss_not_wrong`）。
- 对称关系 `CONFLICTS_WITH`/`RELATED_TO` 的 R 引用方向与期望相反：判命中；`REQUIRES` 等有向关系反向则判错误边（Task 2 Step 1 的 `test_symmetric_relation_matches_either_direction`）。
- 两道题期望同一条边：各自独立计分，一题命中不影响另一题（Task 2 Step 1 的 `test_shared_expected_edge_scores_each_question`）。
- `--mode real` 在 `TAPPER_GRAPH_EXTRACTION_MODE != model` 时必须在连接任何服务之前拒绝（Task 4 Step 1 的 `test_real_mode_refuses_fake_extraction_mode`，镜像 `scripts/run-quality-graph-candidate.py:158`）。
- p95 必须是 ≥200 个样本的次序统计量，不是均值；样本不足直接报错（Task 6 Step 1 的 `test_p95_uses_order_statistic_and_requires_200_samples`）。

---

### Task 1: 语料清单、golden set 结构与校验

**Files:**
- Create: `apps/tap-ai-backend/src/tap/quality/graph_corpus.py`
- Create: `apps/tap-ai-backend/src/tap/quality/graph_relations.py`（本任务只放 `validate_golden` 与 dataclass；匹配逻辑在 Task 2）
- Create: `apps/tap-ai-backend/tests/fixtures/quality/graph-real/manifest.json`（条目待人工填写）、`regression-questions.json`（组骨架）、`golden-v1.json`（骨架：`labeledBy: "pending-human-labeling"`、`questions: []`）
- Create: `scripts/load-graph-real-corpus.py`
- Modify: `.gitignore:10` 之后追加三行
- Test: `apps/tap-ai-backend/tests/unit/quality/test_graph_corpus.py`、`tests/unit/quality/test_graph_golden_schema.py`

**Interfaces:**
- Produces（`graph_corpus.py`）：
  - `LOCAL_CORPUS_DIR = Path(".local/graph-real")`
  - `@dataclass(frozen=True) class CorpusEntry: id: str; title: str; kind: Literal["policy", "process"]; sha256: str; url: str | None; path: str | None`
  - `validate_manifest(value: object) -> tuple[CorpusEntry, ...]`：`schemaVersion == "graph-real-corpus-manifest-v1"`；`policy` 条目恰好 8 条，每条必须有非空 `title`、`https://` 开头的 `url`、`sha256:[0-9a-f]{64}`；`process` 条目 2–3 条，每条必须有 `title`、`sha256`、`path`，且 `path` 解析后位于 `LOCAL_CORPUS_DIR` 内；`id` 唯一且匹配 `[a-z0-9-]+`；任一 `url` 或 `path` 指向仓库内 `tests/fixtures/` 均报 `ValueError("corpus files must not live in the repository")`。
  - `download_target(entry: CorpusEntry) -> Path`：`LOCAL_CORPUS_DIR / f"{entry.id}.pdf"`（process 条目返回 `path`）。
  - `verify_local_file(entry: CorpusEntry) -> None`：文件存在且 sha256 一致，否则 `ValueError`。
  - `manifest_digest(value: object) -> str`：`tap.quality.evidence.canonical_digest`。
- Produces（`graph_relations.py`）：
  - `@dataclass(frozen=True) class ExpectedEdge: subject: str; relation_type: str; object: str; alternatives: tuple["ExpectedEdge", ...] = ()`
  - `@dataclass(frozen=True) class GoldenQuestion: id: str; question: str; locale: str; expected_entities: tuple[tuple[str, tuple[str, ...]], ...]`（(label, aliases)）`; expected_edges: tuple[ExpectedEdge, ...]; expected_sources: tuple[str, ...]`
  - `@dataclass(frozen=True) class GoldenSet: labeled_by: str; labeled_at: str; corpus_manifest: str; corpus_digest: str; questions: tuple[GoldenQuestion, ...]`
  - `validate_golden(value: object, *, require_human: bool = False) -> GoldenSet`：按下方 JSON Schema 校验；`require_human=True` 时 `labeledBy` 不得以 `pending-`、`generated-`、`machine-`、`fixture` 开头，`questions` 数量必须在 20–30；`relationType` 必须属于 `RELATION_TYPES`；`expectedSources[]` 必须是 manifest 条目 id（由调用方传入的 manifest 校验，函数签名加 `corpus_ids: frozenset[str] | None = None`）。
- golden set JSON Schema（写入 `graph_relations.py` 的模块常量 `GOLDEN_SCHEMA`，校验用 `jsonschema` 不可用则手写等价检查）：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["schemaVersion", "labeledBy", "labeledAt", "corpus", "questions"],
  "properties": {
    "schemaVersion": {"const": "graph-relation-golden-v1"},
    "labeledBy": {"type": "string", "minLength": 1},
    "labeledAt": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
    "corpus": {"type": "object", "required": ["manifest", "digest"],
      "properties": {"manifest": {"type": "string"}, "digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}}},
    "questions": {"type": "array", "minItems": 1, "maxItems": 30, "items": {"type": "object",
      "required": ["id", "question", "locale", "expectedEntities", "expectedEdges", "expectedSources"],
      "properties": {
        "id": {"type": "string", "pattern": "^q-[0-9]{2,3}$"}, "question": {"type": "string", "minLength": 1}, "locale": {"enum": ["zh", "en"]},
        "expectedEntities": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["label"],
          "properties": {"label": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}, "maxItems": 10}}}},
        "expectedEdges": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/edge"}},
        "expectedSources": {"type": "array", "minItems": 1, "items": {"type": "string"}}}}}
  },
  "$defs": {"edgeCore": {"type": "object", "required": ["subject", "relationType", "object"],
      "properties": {"subject": {"type": "string"}, "relationType": {"type": "string"}, "object": {"type": "string"}}},
    "edge": {"allOf": [{"$ref": "#/$defs/edgeCore"}], "properties": {"alternatives": {"type": "array", "items": {"$ref": "#/$defs/edgeCore"}}}}}
}
```

- `regression-questions.json`：`{"schemaVersion": "graph-regression-questions-v1", "groups": [{"id": "prompt-suggestions-2026-10-05", "requiredCount": 17, "questions": []}, {"id": "general", "requiredCount": 10, "questions": []}]}`；`validate_regression(value) -> tuple[tuple[str, tuple[str, ...]], ...]` 要求每组 `questions` 数量 ≥ `requiredCount`（骨架状态下允许为空，但返回值标记 `complete=False`，real 模式拒绝）。
- Produces（`scripts/load-graph-real-corpus.py`）：子命令 `validate --manifest <path>`（只校验）、`download --manifest <path>`（`urllib.request` 下载缺失文件到 `download_target`，逐条 `verify_local_file`，单文件上限 50 MiB，失败条目列出后退出 1）、`upload --manifest <path> --api http://127.0.0.1:18000 --output .local/graph-real/corpus.json`（对每条以 multipart 字段 `upload` `POST {api}/api/v1/projects/{project_id}/knowledge/sources`，头 `Idempotency-Key: graph-real-{id}-{sha256[:12]}`，参照 `apps/tap-ai-frontend/tests/e2e/knowledge-graph.spec.ts:14-30`；轮询 `GET .../knowledge/documents` 至全部 READY，再轮询 `GET .../knowledge/graph/project` 至 `status == "READY"`，超时 `--timeout-seconds` 默认 1800；写出 `{manifestDigest, graphVersion, sources: [{id, sourceId, revisionId}]}`）。发布仍由人工在 Library 审核发布流程完成（与 2026-10-05 记录一致），脚本不触碰审核接口。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/quality/test_graph_corpus.py
def test_manifest_requires_eight_policy_entries_with_url_sha_and_title(): ...   # 缺 url / 7 条 / sha 非法 → ValueError 含 "policy"
def test_manifest_rejects_repository_paths_and_outside_local_dir(): ...         # path "tests/fixtures/x.pdf" 与 "/tmp/x.pdf" 均 ValueError
def test_download_target_and_verify_local_file(tmp_path, monkeypatch): ...     # 写入字节后 sha 匹配通过，篡改后 ValueError
def test_committed_manifest_skeleton_has_no_pdf_beside_it(): ...               # glob tests/fixtures/quality/graph-real/*.pdf == []

# tests/unit/quality/test_graph_golden_schema.py
def test_golden_accepts_minimal_valid_document(): ...
def test_golden_rejects_unknown_relation_type_and_missing_alternatives_shape(): ...
def test_require_human_rejects_placeholder_labeler_and_out_of_range_counts(): ...  # "pending-human-labeling"、19 题、31 题 → ValueError
def test_expected_sources_must_be_manifest_ids(): ...
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/quality -v`
Expected: FAIL（`ModuleNotFoundError: tap.quality.graph_corpus`）

- [ ] **Step 3: 实现 `graph_corpus.py`、`graph_relations.py` 的校验部分、三个夹具骨架、`load-graph-real-corpus.py`、`.gitignore`**

- [ ] **Step 4: 运行测试与脚本校验确认通过**

Run: `uv run pytest tests/unit/quality -v`；仓库根目录 `uv run --project apps/tap-ai-backend python scripts/load-graph-real-corpus.py validate --manifest apps/tap-ai-backend/tests/fixtures/quality/graph-real/manifest.json; echo exit=$?`
Expected: 测试 PASS；骨架 manifest 因条目为空而 `exit=1` 并打印 `policy entries: 0/8`

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/quality apps/tap-ai-backend/tests/unit/quality apps/tap-ai-backend/tests/fixtures/quality/graph-real scripts/load-graph-real-corpus.py .gitignore
git commit -m "feat: add graph relation golden schema and real corpus manifest validation"
```

---

### Task 2: R 引用匹配器与报告聚合

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/quality/graph_relations.py`
- Test: `apps/tap-ai-backend/tests/unit/quality/test_graph_relation_matcher.py`

**Interfaces:**
- Produces：
  - `SYMMETRIC_RELATIONS = frozenset({"CONFLICTS_WITH", "RELATED_TO"})`
  - `@dataclass(frozen=True) class EdgeCitation: citation_id: str; evidence_label: str; edge_id: str; subject_label: str; object_label: str; relation_type: str; relation_label: str`
  - `edge_citations(citations: Sequence[Mapping[str, object]]) -> tuple[EdgeCitation, ...]`：只取 `kind == "edge"` 的引用。
  - `@dataclass(frozen=True) class QuestionResult: question_id: str; status: Literal["pass", "miss", "wrong"]; correct_edges: tuple[str, ...]; missed_edges: tuple[str, ...]; wrong_edges: tuple[str, ...]; chunk_citations: int`，边以 `"{subject} -{relationType}-> {object}"` 文本表示。
  - `match_question(question: GoldenQuestion, citations: Sequence[Mapping[str, object]]) -> QuestionResult`。匹配规则：对每条 R 引用，若存在一条期望边或其任一替代满足 `relationType` 相等，且 `normalize_key(subject_label)` 属于期望主语的键集合、`normalize_key(object_label)` 属于期望宾语的键集合，则命中该期望边；键集合 = `normalize_key(期望 label)` ∪ `expectedEntities` 中同 label 条目的全部 `normalize_key(alias)`；`relationType ∈ SYMMETRIC_RELATIONS` 时主宾可互换；替代命中记为主期望边命中。未命中任何期望边或替代的 R 引用计入 `wrong_edges`。`status`：有 wrong → `wrong`；否则 correct 非空 → `pass`；否则 `miss`。
  - `aggregate(results: Sequence[QuestionResult], *, required_percent: int = 80) -> dict[str, object]`：`{"total", "passed", "actual": "24/30", "required": ">=80%", "passed": passed * 100 >= total * required_percent and total > 0}`，与 `scripts/evaluate-quality-graph.py:39-45` 的 `_ratio` 同算法。
  - `grounded_rate(answers: Sequence[Mapping[str, object]]) -> tuple[int, int]`：（非弃答且 `citations` 非空的数量，总数）。

- [ ] **Step 1: 写失败的测试**

```python
def test_matches_after_normalization_and_alias(): ...        # 期望 "健康告知" 别名 "健康问卷"；引用 subject_label "健康問卷 " → pass
def test_alternative_edge_counts_as_hit_for_primary(): ...   # 替代 (A, APPLIES_TO, B) 命中 → correct_edges 含主边文本
def test_unmatched_edge_citation_is_wrong_even_with_hits(): ...  # 一条命中 + 一条无关 → status "wrong"
def test_zero_edge_citations_is_a_miss_not_wrong(): ...      # 只有 chunk 引用 → status "miss"、wrong_edges == ()、chunk_citations == 2
def test_symmetric_relation_matches_either_direction(): ...  # CONFLICTS_WITH 反向 → pass；REQUIRES 反向 → wrong
def test_shared_expected_edge_scores_each_question(): ...    # 两题同一期望边，仅第一题的回答含该 R → 结果 ["pass", "miss"]
def test_aggregate_threshold_boundary():
    assert aggregate(_results(passed=24, total=30))["passed"] is True
    assert aggregate(_results(passed=23, total=30))["passed"] is False
    assert aggregate(())["passed"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/quality/test_graph_relation_matcher.py -v`
Expected: FAIL（`ImportError: match_question`）

- [ ] **Step 3: 实现匹配器与聚合**

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/quality -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/quality/graph_relations.py apps/tap-ai-backend/tests/unit/quality/test_graph_relation_matcher.py
git commit -m "feat: match relation edge citations against the golden set"
```

---

### Task 3: 评测脚本 `evaluate-graph-relations.py`

**Files:**
- Create: `scripts/evaluate-graph-relations.py`
- Test: `apps/tap-ai-backend/tests/quality/test_quality_graph_relations.py`（用 `importlib.util.spec_from_file_location` 载入脚本，照 `tests/quality/test_quality_graph_01.py:19-24`）

**Interfaces:**
- Consumes：Task 1 的 `validate_golden / validate_regression / validate_manifest`，Task 2 的 `match_question / aggregate / grounded_rate`。
- CLI：`evaluate-graph-relations.py --golden <path> --observations <path> --report <path> [--regression <questions.json> --baseline-observations <path>] [--real]`。退出码：0 通过、1 不通过、2 输入非法。
- 观测文件形状（Task 4 产生）：`{"schemaVersion": "graph-relation-observations-v1", "executionMode": "fake"|"real", "goldenDigest", "corpusDigest", "graphVersion", "extractionMode", "model": {"alias", "actual"}, "startedAt", "finishedAt", "answers": [{"questionId", "group": "golden"|<regression group id>, "abstained": bool, "graphContextStatus", "claims": [{"text", "citationIds": []}], "citations": [{"citationId", "kind", "evidenceLabel", "chunkId"?, "edgeId"?, "graphVersion"?, "subject"?: {"nodeId","label"}, "object"?, "relationType"?, "relationLabel"?}]}]}`。
- 报告形状：

```json
{"schemaVersion": "graph-relation-report-v1", "executionMode": "fake", "goldenDigest": "sha256:…", "observationsDigest": "sha256:…",
 "evaluatorDigest": "sha256:…", "graphVersion": "…",
 "questions": [{"id": "q-01", "status": "pass", "correctEdges": ["核保流程 -REQUIRES-> 健康告知"], "missedEdges": [], "wrongEdges": [], "chunkCitations": 3}],
 "summary": {"total": 30, "passed": 24, "actual": "24/30", "required": ">=80%", "passed": true},
 "regression": {"groups": [{"id": "prompt-suggestions-2026-10-05", "grounded": 16, "total": 17, "baselineGrounded": 16, "baselineTotal": 17, "passed": true}]},
 "passed": true}
```

- 规则：`passed = summary.passed and all(group.passed)`；组通过 = `grounded * baselineTotal >= baselineGrounded * total`；未提供 `--regression` 时 `regression.groups == []`，但 `--real` 必须提供。`--real` 额外要求：`validate_golden(require_human=True)`；观测 `executionMode == "real"`、`extractionMode == "model"`、`model.actual` 不以 `fake/`、`pending/`、`simulated/` 开头；观测 `goldenDigest` 等于当前 golden 的 `canonical_digest`，`corpusDigest` 等于 manifest 摘要；基线观测的 `graphVersion`、`corpusDigest` 与主观测一致且其 `graphReasoning == False`（观测字段，Task 4 从 `TapperSettings.graph_reasoning` 写入）。缺失观测中的题目计为 `miss`。`evaluatorDigest` 为脚本自身 sha256（`scripts/evaluate-quality-graph.py:34-35`）。

- [ ] **Step 1: 写失败的测试**

```python
def test_report_lists_correct_missed_wrong_per_question(tmp_path): ...     # 3 题观测：pass / miss / wrong → questions[*].status 与 exit 1
def test_passes_at_24_of_30_and_fails_at_23(tmp_path): ...                  # 合成 30 题观测
def test_regression_group_must_not_drop_below_baseline(tmp_path): ...      # 16/17 vs 基线 16/17 通过；15/17 vs 16/17 失败
def test_real_mode_rejects_fake_observations_and_placeholder_labeler(tmp_path): ...  # executionMode fake → exit 2 且 stderr 含 "real"
def test_real_mode_rejects_stale_golden_digest(tmp_path): ...
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/quality/test_quality_graph_relations.py -v`
Expected: FAIL（脚本不存在）

- [ ] **Step 3: 实现脚本（`main() -> int`，`evaluate(golden, observations, *, regression=None, baseline=None) -> dict`，`validate_real(...)` 三个顶层函数）**

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/quality/test_quality_graph_relations.py tests/unit/quality -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/evaluate-graph-relations.py apps/tap-ai-backend/tests/quality/test_quality_graph_relations.py
git commit -m "feat: evaluate relation answers against the graph golden set"
```

---

### Task 4: 观测生成脚本（fake 全链路与 real 干跑）与 CI 结构性检查

**Files:**
- Create: `scripts/run-graph-relations-candidate.py`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/prompt_suggestion_knowledge.py:241-300`（把 `is_grounded` 拆成 `dry_run_answer` + 判定）
- Create: `apps/tap-ai-backend/tests/fixtures/quality/graph-relations/underwriting-process.md`、`claims-process.md`、`golden-fixture-v1.json`
- Test: `apps/tap-ai-backend/tests/quality/test_quality_graph_relations.py`（追加）、`tests/unit/entrypoints/test_prompt_suggestion_grounding.py`（追加）

**Interfaces:**
- Produces（`ConversationGroundingCheck`）：`async def dry_run_answer(self, actor_id: str, question: str, source_ids: tuple[str, ...]) -> object | None`：现有 `is_grounded` 第 242–299 行逻辑，来源不在当前就绪集合时返回 `None`，`AnswerUnavailable` 非 `model-unavailable` 时返回 `None`；`is_grounded` 改为 `answer = await self.dry_run_answer(...)`；`return answer is not None and not answer.abstained and bool(answer.citations)`。
- CLI：`run-graph-relations-candidate.py --mode fake|real --golden <path> --observations <path> [--regression <path>] [--corpus-dir <dir>]（fake 必填）[--corpus .local/graph-real/corpus.json]（real 必填）[--actor-id tapper-gate]`。
- fake 模式（无外部服务）：读 `--corpus-dir` 下全部 `*.md`，按空行切段为切片（`chunkId = f"{stem}-{ordinal}"`，anchor 形如 `tests/unit/graph/test_fake_graph_publisher.py:27-32`），每份文档 `DeterministicGraphExtraction().extract(GraphExtractionRequest(...))` 得片段，经 PR 2 的合并纯函数（`project_merge` job 处理器调用的那一个）得到项目图版本并装入内存 `ProjectGraphStore`；每题构造 `RelationAnalysisInput(query=问题, 授权修订=全部, 检索证据=(), 项目图版本)`，调用注册名 `relation-analysis` 的子图 `run(AgentContext, input)` 得 `RelationContext`；把关系证据记录与每条边第一条证据切片喂给 `DeterministicTapperModel.answer(query, evidence, profile_id)`，用 `ports/answers.py` 的 R 标签校验把 claims 转成 `RetrievalAnswerResponse` 形状的字典（`kind: "edge"` 引用来自 `RelationContext` 中被引用的边）。观测 `executionMode="fake"`、`extractionMode="fake"`、`model={"alias": "fake", "actual": "fake/deterministic-tapper"}`。
- real 模式：先 `settings = TapperSettings.from_mapping(dict(os.environ))`，`settings.graph_extraction_mode != "model"` 立即 `ValueError("candidate run requires TAPPER_GRAPH_EXTRACTION_MODE=model")`（在 `create_api_runtime` 之前）；随后 `runtime = await create_api_runtime(settings)`（`tests/smoke/test_prompt_suggestions_real_model.py:22-30` 的用法），用 `runtime.http_services.knowledge` 与就绪来源构造 `ConversationGroundingCheck(..., model_alias=settings.default_chat_model)`，对 golden 与 regression 每题调用 `dry_run_answer(actor_id, question, source_ids)`，`source_ids` = `corpus.json` 中该题 `expectedSources` 对应的 sourceId（regression 题用全部来源）；用 `answer_response_to_http`（`interfaces/http/knowledge_service.py:202-206` 所用）序列化；`None` 记为 `abstained=True, citations=[]`。观测 `graphVersion` 取 `GET /knowledge/graph/project` 等价的服务调用，`graphReasoning = settings.graph_reasoning`，`model.actual` 取回答的实际模型（`model_call` 记录的 `actual_model`，不可得时 `settings.default_chat_model` 前加 `litellm/`）。
- fixture 文档对（两份都含实体"健康告知"）：`underwriting-process.md` 含句子 `核保流程需要健康告知。健康告知由核保员负责。核保流程之后是保单签发。`；`claims-process.md` 含 `理赔申请需要健康告知。理赔审核由理赔员负责。保单签发触发理赔权益。`。`golden-fixture-v1.json`：`labeledBy: "generated-fixture"`、3 题：q-01 "核保流程和健康告知是什么关系" 期望 `核保流程 -REQUIRES-> 健康告知`；q-02 "理赔申请需要什么" 期望 `理赔申请 -REQUIRES-> 健康告知`；q-03 "保单签发之后会发生什么" 期望 `保单签发 -TRIGGERS-> 理赔权益`，替代 `核保流程 -PRECEDES-> 保单签发`；`corpus.manifest: "fixture"`，`expectedSources` 为文件 stem。

- [ ] **Step 1: 写失败的测试**

```python
# 追加到 tests/unit/entrypoints/test_prompt_suggestion_grounding.py
@pytest.mark.asyncio
async def test_dry_run_answer_returns_answer_and_none_for_unknown_source(): ...  # 复用文件内 _Knowledge/_ReadySources 夹具

# 追加到 tests/quality/test_quality_graph_relations.py
@pytest.mark.asyncio
async def test_fake_pipeline_emits_edge_citations_for_the_fixture_pair(tmp_path):
    observations = await _runner().run_fake(golden=FIXTURE_GOLDEN, corpus_dir=FIXTURE_DIR)
    by_id = {item["questionId"]: item for item in observations["answers"]}
    assert any(c["kind"] == "edge" for c in by_id["q-01"]["citations"])
    report = _evaluator().evaluate(json.loads(FIXTURE_GOLDEN.read_text()), observations)
    assert report["passed"] is True and report["questions"][0]["status"] == "pass"
    assert any(c["kind"] == "edge" and c["subject"]["label"] == "保单签发" for c in by_id["q-03"]["citations"])

def test_real_mode_refuses_fake_extraction_mode(monkeypatch):
    monkeypatch.setenv("TAPPER_GRAPH_EXTRACTION_MODE", "fake")
    with pytest.raises(ValueError, match="TAPPER_GRAPH_EXTRACTION_MODE=model"):
        asyncio.run(_runner().run_real(golden=FIXTURE_GOLDEN, regression=None, corpus=Path("missing.json")))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/quality/test_quality_graph_relations.py tests/unit/entrypoints/test_prompt_suggestion_grounding.py -v`
Expected: FAIL（`AttributeError: run_fake`、`dry_run_answer`）

- [ ] **Step 3: 实现 `dry_run_answer` 拆分、fixture 文档对与 golden、脚本 `run_fake / run_real / main`**

- [ ] **Step 4: 运行测试确认通过，并手动跑一次 fake 全链路**

Run: `uv run pytest tests/quality tests/unit/entrypoints/test_prompt_suggestion_grounding.py -v`，然后在仓库根目录依次运行 Task 7 `quality-graph-relations` 目标中的两条命令
Expected: 测试 PASS；评测输出 `"passed": true`、`"actual": "3/3"`

- [ ] **Step 5: 提交**

```bash
git add scripts/run-graph-relations-candidate.py apps/tap-ai-backend/src/tap/entrypoints/prompt_suggestion_knowledge.py apps/tap-ai-backend/tests
git commit -m "feat: produce relation answer observations in fake and real modes"
```

---

### Task 5: 抽样导出与人工核对统计

**Files:**
- Create: `apps/tap-ai-backend/src/tap/quality/graph_samples.py`
- Create: `scripts/export-graph-samples.py`、`scripts/evaluate-graph-samples.py`
- Test: `apps/tap-ai-backend/tests/unit/quality/test_graph_samples.py`、`apps/tap-ai-backend/tests/integration/test_graph_sample_export.py`（有 MySQL 时运行，沿用 `owned_project_mysql` 的 `TAP_RUN_MYSQL_INTEGRATION=1` 约定）

**Interfaces:**
- Produces（`graph_samples.py`，纯函数）：
  - `EDGE_COLUMNS = ("edgeId", "graphVersion", "subjectLabel", "subjectType", "relationType", "relationLabel", "objectLabel", "objectType", "confidence", "origin", "sourceTitle", "chunkId", "evidenceSnippet", "verdict", "reviewer", "reviewedAt", "note")`
  - `MERGE_COLUMNS = ("nodeId", "graphVersion", "label", "nodeType", "aliases", "sourceCount", "sourceTitles", "mergeRules", "mergedFromCount", "verdict", "reviewer", "reviewedAt", "note")`
  - `sample_rows(rows: Sequence[Mapping[str, object]], *, count: int, seed: int) -> tuple[Mapping[str, object], ...]`：`random.Random(seed).sample(sorted(rows, key=主键), min(count, len(rows)))`。
  - `edge_csv_row(edge, subject, object_, evidence, *, snippet_limit: int = 300) -> dict[str, str]`；`merge_csv_row(node, aliases, sources, merge_log) -> dict[str, str]`（`aliases`、`sourceTitles`、`mergeRules` 用 `|` 连接；`verdict/reviewer/reviewedAt/note` 留空）。
  - `is_cross_source(sources: Sequence[Mapping[str, object]]) -> bool`：`len({s["source_revision_id"]}) >= 2`。
  - `tally(rows: Sequence[Mapping[str, str]], *, kind: Literal["edges", "merges"]) -> dict[str, object]`：`verdict` 必须全部为 `correct` 或 `wrong`，`reviewer`、`reviewedAt` 非空且不以 `pending-`/`machine-` 开头，否则 `ValueError("sample review incomplete")`；edges 返回 `_ratio(correct, total, 85)`，merges 返回 `_maximum(wrong, total, 5)`（算法同 `scripts/evaluate-quality-graph.py:39-52`）。
- CLI：`export-graph-samples.py edges --count 50 --seed <int> --output .local/graph-real/edge-sample.csv [--graph-version <v>]`、`export-graph-samples.py merges --count 30 --seed <int> --output .local/graph-real/merge-sample.csv`。用 `TapperSettings.from_mapping(dict(os.environ))` + `_open_database(settings)`（`entrypoints/tapper_runtime.py:909`）读当前 READY 版本的 `graph_project_*` 表；边候选为全部 `origin == EXTRACTED` 的边，证据取 `graph_project_edge_evidence` 第一条并从 `knowledge_chunk_manifest` 读切片正文截断 300 字；合并候选为 `graph_project_node_source` 中跨 ≥2 个 `source_revision_id` 的节点，`mergeRules` 取 `graph_merge_log.rule`。`evaluate-graph-samples.py --edges <csv> --merges <csv> --report .local/graph-real/sample-report.json`：输出 `{"edgeAccuracy": {...}, "incorrectMergeRate": {...}, "passed": bool}`，退出 0/1。

- [ ] **Step 1: 写失败的测试**

```python
def test_sample_rows_is_deterministic_for_a_seed_and_bounded(): ...     # 同 seed 两次相同；count > len 时返回全部
def test_edge_and_merge_csv_rows_use_exact_columns(): ...               # set(row) == set(EDGE_COLUMNS)，snippet 截断 300
def test_cross_source_requires_two_distinct_source_revisions(): ...
def test_tally_thresholds_and_incomplete_review():
    assert tally(_verdicts(correct=43, wrong=7), kind="edges")["passed"] is True     # 86%
    assert tally(_verdicts(correct=42, wrong=8), kind="edges")["passed"] is False    # 84%
    assert tally(_verdicts(correct=29, wrong=1), kind="merges")["passed"] is True    # 3.3%
    assert tally(_verdicts(correct=28, wrong=2), kind="merges")["passed"] is False   # 6.7%
    with pytest.raises(ValueError, match="incomplete"): tally(_verdicts(correct=49, wrong=0, blank=1), kind="edges")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/quality/test_graph_samples.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现纯模块与两个脚本；集成测试写入 5 条边、2 个跨来源节点后导出并断言 CSV 行数与列**

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/quality tests/integration/test_graph_sample_export.py -v`
Expected: 单元 PASS；集成在无 MySQL 时 1 skip

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/quality/graph_samples.py scripts/export-graph-samples.py scripts/evaluate-graph-samples.py apps/tap-ai-backend/tests
git commit -m "feat: export graph edge and merge samples for human review"
```

---

### Task 6: 合成基准生成器与 `graph-bench`

**Files:**
- Create: `apps/tap-ai-backend/src/tap/quality/graph_bench.py`
- Create: `scripts/graph-bench.py`
- Test: `apps/tap-ai-backend/tests/unit/quality/test_graph_bench.py`

**Interfaces:**
- Produces（`graph_bench.py`）：
  - `@dataclass(frozen=True) class BenchGraph: version: str; nodes: tuple[dict, ...]; edges: tuple[dict, ...]; node_sources: tuple[dict, ...]; edge_evidence: tuple[dict, ...]; aliases: tuple[dict, ...]; communities: tuple[dict, ...]`，字典键与 spec 1.2 列名一致。
  - `synthesize_graph(*, seed: int, node_count: int = 10_000, edge_count: int = 50_000, community_count: int = 50, source_count: int = 40) -> BenchGraph`：`random.Random(seed)`；`node_id = f"bn_{index:06d}"`，label `f"实体{index}"`，类型循环取自 `NODE_TYPES`，社区 `index % community_count`；边 80% 在社区内、20% 跨社区，无自环，`(source, target, relation_type)` 去重后恰好 `edge_count` 条，`relation_type` 循环取自 `RELATION_TYPES`；每节点 1 条 `node_source`、每边 1 条 `edge_evidence`，`chunk_id = f"bench-chunk-{index}"`；每 10 个节点 1 条别名；`version = f"bench-{seed}"`。
  - `digest(graph: BenchGraph) -> str`（`canonical_digest`）。
  - `p95_ms(samples: Sequence[float]) -> float`：`len(samples) < 200` 抛 `ValueError("p95 requires at least 200 samples")`；返回 `sorted(samples)[math.ceil(0.95 * len(samples)) - 1]`。
  - `BENCH_KINDS = ("neighbors", "path", "overview")`、`P95_LIMIT_MS = 300.0`。
- CLI：`graph-bench.py generate --seed 20261006 [--nodes 10000 --edges 50000]`：写 `graph_project_*` 各表并插入 `graph_project_version(status="READY")`，同版本已存在则跳过；`graph-bench.py run --seed 20261006 --samples 200 --warmup 20 --report .local/graph-bench/report.json`：装载 `ProjectGraphStore` 一次并记录装载耗时，每类查询先 20 次预热不计时，再 200 次计时（neighbors：随机节点 1 跳；path：随机两节点 ≤3 跳；overview：随机 1–3 个社区、`nodeLimit=150`），用 `time.perf_counter()`；报告 `{"seed", "nodeCount", "edgeCount", "loadMs", "queries": {kind: {"samples": 200, "p95Ms", "meanMs", "passed": p95 < 300}}, "passed"}`，退出 0/1。两个子命令都要求 `TAP_DATABASE_URL`。

- [ ] **Step 1: 写失败的测试**

```python
def test_synthesize_is_deterministic_and_sized():
    a, b = synthesize_graph(seed=7, node_count=500, edge_count=2000), synthesize_graph(seed=7, node_count=500, edge_count=2000)
    assert digest(a) == digest(b) and digest(a) != digest(synthesize_graph(seed=8, node_count=500, edge_count=2000))
    assert len(a.nodes) == 500 and len(a.edges) == 2000 and len(a.communities) <= 50
    ids = {n["node_id"] for n in a.nodes}
    assert all(e["source_node_id"] in ids and e["target_node_id"] in ids and e["source_node_id"] != e["target_node_id"] for e in a.edges)
    assert {e["relation_type"] for e in a.edges} <= RELATION_TYPES

def test_p95_uses_order_statistic_and_requires_200_samples():
    samples = [1.0] * 199 + [1000.0]
    assert p95_ms(samples) == 1.0          # 均值约 6.0，均值实现会失败
    with pytest.raises(ValueError, match="200"): p95_ms(samples[:199])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/quality/test_graph_bench.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `graph_bench.py` 与 `scripts/graph-bench.py`**

- [ ] **Step 4: 运行测试确认通过；有本地 MySQL 时在 Task 7 接线后跑一次 `TAP_RUN_GRAPH_BENCH=1 make graph-bench`**

Run: `uv run pytest tests/unit/quality/test_graph_bench.py -v`
Expected: PASS；基准报告三类查询 `p95Ms < 300`（无 MySQL 时在 PR 描述注明未运行）

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/quality/graph_bench.py scripts/graph-bench.py apps/tap-ai-backend/tests/unit/quality/test_graph_bench.py
git commit -m "feat: add synthetic project graph benchmark"
```

---

### Task 7: Makefile 目标、`make check` 接线与文档

**Files:**
- Modify: `Makefile:21-23`（新脚本加入 ruff/mypy 列表）、`Makefile:35` 之后（`check` 追加一行）、`Makefile:183` 之后（新目标块）
- Modify: `docs/architecture.md:61-64`（图谱段落增加"验收门禁：golden set 评测、抽样导出、`graph-bench`"一句）、`docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 2、3 工程完成行增加门禁命令；子项目行标"PR 5 门禁工具已合并，真实门禁待运行"）、`docs/superpowers/plans/2026-10-01-product-roadmap.md:53-54`（同步）

**Interfaces:**
- `check` 追加：`$(MAKE) quality-graph-relations`。
- 新目标（变量 `TAP_GRAPH_RELATIONS_GOLDEN ?= apps/tap-ai-backend/tests/fixtures/quality/graph-real/golden-v1.json`、`TAP_GRAPH_RELATIONS_REGRESSION ?= .../regression-questions.json`、`TAP_GRAPH_REAL_MANIFEST ?= .../manifest.json`、`TAP_GRAPH_RELATIONS_OBSERVATIONS ?= .local/graph-relations/observations.json`、`TAP_GRAPH_RELATIONS_BASELINE ?= .local/graph-relations/baseline-observations.json`、`TAP_GRAPH_RELATIONS_REPORT ?= .local/graph-relations/report.json`）：

```makefile
quality-graph-relations: ## run the relation golden set end to end with the rule-based fake extractor and fake model
	uv run --project apps/tap-ai-backend python scripts/run-graph-relations-candidate.py --mode fake --golden apps/tap-ai-backend/tests/fixtures/quality/graph-relations/golden-fixture-v1.json --corpus-dir apps/tap-ai-backend/tests/fixtures/quality/graph-relations --observations .local/graph-relations/fixture-observations.json
	uv run --project apps/tap-ai-backend python scripts/evaluate-graph-relations.py --golden apps/tap-ai-backend/tests/fixtures/quality/graph-relations/golden-fixture-v1.json --observations .local/graph-relations/fixture-observations.json --report .local/graph-relations/fixture-report.json

quality-graph-relations-real: ## evaluate the human-labeled relation golden set against the real model (opt-in)
	@if [ "$${TAP_RUN_QUALITY_GRAPH_RELATIONS:-}" != "1" ]; then echo "quality-graph-relations-real requires TAP_RUN_QUALITY_GRAPH_RELATIONS=1" >&2; exit 2; fi
	@case "$${TAP_QUALITY_GRAPH_RELATIONS_DATASET_AUTHORIZATION:-}" in approved:?*) ;; *) echo "quality-graph-relations-real requires explicit dataset authorization" >&2; exit 2;; esac
	@case "$${TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION:-}" in approved:?*) ;; *) echo "quality-graph-relations-real requires explicit model execution authorization" >&2; exit 2;; esac
	@if [ "$${TAPPER_GRAPH_EXTRACTION_MODE:-}" != "model" ]; then echo "quality-graph-relations-real requires TAPPER_GRAPH_EXTRACTION_MODE=model" >&2; exit 2; fi
	@if [ -z "$${TAP_DATABASE_URL:-}" ] || [ -z "$${MILVUS_URI:-}" ]; then echo "quality-graph-relations-real requires explicit MySQL and Milvus endpoints" >&2; exit 2; fi
	uv run --project apps/tap-ai-backend python scripts/load-graph-real-corpus.py validate --manifest "$(TAP_GRAPH_REAL_MANIFEST)"
	uv run --project apps/tap-ai-backend python scripts/run-graph-relations-candidate.py --mode real --golden "$(TAP_GRAPH_RELATIONS_GOLDEN)" --regression "$(TAP_GRAPH_RELATIONS_REGRESSION)" --corpus .local/graph-real/corpus.json --observations "$(TAP_GRAPH_RELATIONS_OBSERVATIONS)"
	uv run --project apps/tap-ai-backend python scripts/evaluate-graph-relations.py --golden "$(TAP_GRAPH_RELATIONS_GOLDEN)" --observations "$(TAP_GRAPH_RELATIONS_OBSERVATIONS)" --regression "$(TAP_GRAPH_RELATIONS_REGRESSION)" --baseline-observations "$(TAP_GRAPH_RELATIONS_BASELINE)" --report "$(TAP_GRAPH_RELATIONS_REPORT)" --real

graph-sample-export: ## export 50 edges and 30 cross-source merged entities for human review
	@[ -n "$${TAP_DATABASE_URL:-}" ] || { echo "graph-sample-export requires TAP_DATABASE_URL" >&2; exit 2; }
	uv run --project apps/tap-ai-backend python scripts/export-graph-samples.py edges --count 50 --seed "$${TAP_GRAPH_SAMPLE_SEED:-20261006}" --output .local/graph-real/edge-sample.csv
	uv run --project apps/tap-ai-backend python scripts/export-graph-samples.py merges --count 30 --seed "$${TAP_GRAPH_SAMPLE_SEED:-20261006}" --output .local/graph-real/merge-sample.csv

graph-bench: ## synthesize 10k nodes / 50k edges and measure neighbors, path and overview p95 (not in CI)
	@if [ "$${TAP_RUN_GRAPH_BENCH:-}" != "1" ]; then echo "graph-bench requires TAP_RUN_GRAPH_BENCH=1" >&2; exit 2; fi
	@[ -n "$${TAP_DATABASE_URL:-}" ] || { echo "graph-bench requires TAP_DATABASE_URL" >&2; exit 2; }
	uv run --project apps/tap-ai-backend python scripts/graph-bench.py generate --seed "$${TAP_GRAPH_BENCH_SEED:-20261006}"
	uv run --project apps/tap-ai-backend python scripts/graph-bench.py run --seed "$${TAP_GRAPH_BENCH_SEED:-20261006}" --samples 200 --warmup 20 --report .local/graph-bench/report.json
```

- [ ] **Step 1: 修改 Makefile、三份文档；对客户企业名称做大小写不敏感全文检索，必须无输出**

- [ ] **Step 2: 验证 make 目标的拒绝路径与 CI 路径**

Run（仓库根目录）: `make quality-graph-relations && for t in quality-graph-relations-real graph-bench graph-sample-export; do make $t; test $? -eq 2 || exit 1; done`
Expected: 第一个目标输出 `"passed": true`；后三个未设 opt-in 变量时 exit 2 且 stderr 为各自的 `requires ...` 提示

- [ ] **Step 3: 全量检查**

Run: `make check && make test && git diff --check`
Expected: 全部通过（`check` 内含 `quality-graph-relations`；新脚本通过 ruff/mypy）

- [ ] **Step 4: 提交**

```bash
git add Makefile docs/architecture.md docs/superpowers/plans
git commit -m "build: wire graph relation quality, sample export and bench targets"
```

---

### Task 8: 真实门禁执行与记录（人工参与，可在 PR 合并后进行）

**Files:**
- Modify: `apps/tap-ai-backend/tests/fixtures/quality/graph-real/manifest.json`、`golden-v1.json`、`regression-questions.json`（人工填写）
- Create: `docs/reviews/2026-10-DD-graph-reasoning-gate.md`
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 2、3 业务验证行增加记录，格式同 `:18`）、`docs/superpowers/plans/2026-10-01-product-roadmap.md`

**Interfaces:**
- 门禁记录模板（章节固定）：
  1. `## 结论`：PASS / FAIL，一句话；执行日期、执行人、模型别名与实际模型、`graphVersion`、`GRAPH_EXTRACTION_PROFILE_DIGEST`。
  2. `## 语料`：manifest 摘要、8 份条款标题、2–3 份流程文档标题、上传与发布方式、项目图节点/边/社区数。
  3. `## 关系 golden set`：标注人、日期、题数；`summary.actual`；逐题表（id、状态、正确边、遗漏边、错误边）；失败题的原因归类（抽取缺边 / 对齐失败 / 模型未引用 / 校验丢弃）。
  4. `## 有依据率回归`：两组各自的 `grounded/total` 与基线值、是否通过。
  5. `## 抽样核对`：边 50 条准确率、合并 30 个误合并率、核对人与日期、CSV 路径（`.local/`，不提交）。
  6. `## 性能基准`：`graph-bench` 的 `loadMs` 与三类 `p95Ms`、机器描述。
  7. `## 偏差与后续`：与 spec 4 节阈值的差距、已知问题、真实项目资料到位后的复验计划。
  8. `## 命令与产物`：按顺序列出实际执行的 make 命令与报告路径及其 `sha256`。

- [ ] **Step 1: 人工填写 manifest（8 条公开友邦条款 URL + sha256 + 标题；2–3 份流程文档登记）与 regression 两组问题（17 条来自 2026-10-05 会话历史 `GET /conversations`，普通问题 ≥10 条）**

Run: `uv run --project apps/tap-ai-backend python scripts/load-graph-real-corpus.py validate --manifest $M && uv run --project apps/tap-ai-backend python scripts/load-graph-real-corpus.py download --manifest $M`（`M=apps/tap-ai-backend/tests/fixtures/quality/graph-real/manifest.json`）
Expected: exit 0；`.local/graph-real/` 下 10–11 个文件，`git status` 不出现任何 PDF

- [ ] **Step 2: 启动本地栈（`make demo-up && make tap-ai-dev`，`TAPPER_GRAPH_EXTRACTION_MODE=model`），`upload` 语料并在 Library 完成审核发布；确认 `GET /knowledge/graph/project` 为 READY。人工标注 golden set（20–30 题，`labeledBy` 实名、`labeledAt` 当日，`corpus.digest` 填 manifest 摘要）**

Run: `uv run --project apps/tap-ai-backend python scripts/evaluate-graph-relations.py --golden $G --observations /dev/null --report /dev/null --real; test $? -eq 2`
Expected: 因观测缺失退出 2，stderr 不含 golden 校验错误

- [ ] **Step 3: 生成基线观测（`TAPPER_GRAPH_REASONING=0` 运行 `run-graph-relations-candidate.py --mode real ... --observations .local/graph-relations/baseline-observations.json`），再运行 `TAP_RUN_QUALITY_GRAPH_RELATIONS=1 TAP_QUALITY_GRAPH_RELATIONS_DATASET_AUTHORIZATION=approved:<name> TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION=approved:<name> make quality-graph-relations-real`、`make graph-sample-export`、`TAP_RUN_GRAPH_BENCH=1 make graph-bench`；人工填写两份 CSV 的 `verdict/reviewer/reviewedAt` 后运行 `evaluate-graph-samples.py`**

Expected: `.local/graph-relations/report.json`、`.local/graph-real/sample-report.json`、`.local/graph-bench/report.json` 各带 `passed` 字段

- [ ] **Step 4: 按模板写门禁记录，更新两份路线图行；无论 PASS 或 FAIL 都如实记录，不得把 fake 模式结果写成真实门禁**

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/tests/fixtures/quality/graph-real docs/reviews docs/superpowers/plans
git commit -m "docs: record graph reasoning acceptance gate run"
```

PR 描述列出：意图、受影响文档、`make check/test` 结果、`quality-graph-relations` 的 fake 结果与真实门禁状态（已运行并附记录路径，或 PENDING 并说明缺少的人工输入）。
