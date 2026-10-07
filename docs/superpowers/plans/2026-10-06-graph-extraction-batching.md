# 图谱抽取分批与受控词表实施计划（PR 1/5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让单个文档的图谱抽取按批次（默认 10 个切片一批）可重试、可恢复、可部分成功，边只使用受控关系词表并带原文关系标签，节点带别名与 PROCESS 类型，假抽取改为规则式以便 CI 与 E2E 跑完整流水线。

**Architecture:** 不改 job 的创建方式（每个文档修订一个 graph job、一个片段快照）。worker 把切片切批，每批一次抽取调用并把结果持久化到新表 `graph_fragment_batch`，批次间续约租约；全部批次结束后把 READY 批次合成一个 `GraphSnapshotDraft` 原子发布，有失败批次时快照状态为 PARTIAL。受控词表在抽取边界执行（模型抽取丢弃词表外的边并计数，规则式假抽取只产出词表内的边），domain 层的 `GraphEdge` 仍只校验格式，以兼容已入库的历史边。

**Tech Stack:** Python 3.13、SQLAlchemy async + Alembic（MySQL）、pytest-asyncio、OpenTelemetry span 工具（`tap.platform.telemetry.tracing.span`）、`make contracts` 生成 OpenAPI 与 TypeScript 类型。

**Spec:** [知识图谱脉络分析设计](../specs/2026-10-06-knowledge-graph-reasoning-design.md) 第 1.1、1.4、5.2、5.3 节（PR 1 范围）。

## Global Constraints

- 不新增 Python 或 Node 依赖。
- 关系词表（spec 1.1）：`REQUIRES`、`APPLIES_TO`、`PART_OF`、`EXCEPTION_OF`、`SUPERSEDES`、`TRIGGERS`、`PRECEDES`、`VALIDATED_BY`、`RESPONSIBLE_FOR`、`USES`、`DEFINES`、`CONFLICTS_WITH`、兜底 `RELATED_TO`。
- 节点类型：`ENTITY`、`CONCEPT`、`REQUIREMENT`、`SYSTEM`、`ACTOR`、`PROCESS`。
- `relationLabel` ≤ 64 字符；节点 `aliases` ≤ 5 个，每个 ≤ 128 字符。
- 批大小 `TAPPER_GRAPH_BATCH_SIZE` 默认 10（1–50）；重试 `TAPPER_GRAPH_BATCH_RETRIES` 默认 3（1–10）；重试退避 1s、2s（`backoff_seconds × 2^(attempt-1)`；默认 3 次重试只产生 2 次退避，第 3 次失败即终止，不再退避），且每次退避不超过租约时长。
- 每个文档最多处理前 500 个切片（现状保留）。
- 批次幂等键：`f"{request_digest}:batch:{batch_index}"`。
- 迁移编号 `0028_graph_fragment_batch`（spec 5.1 的 `0028_project_graph` 顺延为 `0029`，本 PR 同步修正 spec）。
- `.env.example` 的 `TAPPER_GRAPH_EXTRACTION_MODE` 改为 `model`；`scripts/run-tapper-e2e.sh` 显式导出 `TAPPER_GRAPH_EXTRACTION_MODE=fake`。
- 文档、代码与夹具中不得出现客户企业名称（英文缩写或中文全称均不得出现），示例语料只用公开的友邦条款。
- 提交信息用小写祈使句 Conventional Commit；每个任务结束前 `git diff --check`。
- 每个任务的测试命令在 `apps/tap-ai-backend` 目录下用 `uv run pytest` 运行。

## Review Focus

- 中文句子没有空格与词边界：规则式抽取必须按"。；！？"切句并用非贪婪匹配，"核保流程需要健康告知。" 必须得到 `核保流程 —REQUIRES→ 健康告知`（Task 5 Step 1 的 `test_rule_based_extracts_chinese_relations`）。
- 整份文档没有任何关系句：job 必须 READY 而不是 FAILED，快照只含一个 `document:` 前缀的文档节点、零条边（Task 5 Step 1 的 `test_rule_based_falls_back_to_a_document_node`）。
- 模型返回词表外的关系或引用不存在节点的边：只丢弃该边并计数，其余照常；证据摘要不匹配仍然整批失败（Task 4 Step 1 的 `test_edges_outside_vocabulary_are_dropped_not_fatal` 与现有 `test_invalid_model_facts_fail_closed`）。
- worker 在批次之间崩溃后被重新认领：已 READY 的批次不再调用模型（Task 6 Step 1 的 `test_resume_skips_ready_batches`）。
- 批次之间续约失败（租约已被别的 worker 拿走）：立即停止，不发布、不写批次（Task 6 Step 1 的 `test_lease_lost_between_batches_stops_without_publishing`）。

---

### Task 1: 词表、PROCESS 类型、别名、关系标签与 PARTIAL 状态（domain 与契约）

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/graph/domain/vocabulary.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/domain/models.py:36-110`（`GraphSnapshot`、`GraphNode`）、`:186-215`（`GraphEdge`）
- Modify: `apps/tap-ai-backend/src/tap/contracts/http.py:1196-1201`（`GraphSnapshotView.status`）
- Test: `apps/tap-ai-backend/tests/unit/graph/test_graph_vocabulary.py`（新建）、`apps/tap-ai-backend/tests/unit/graph/test_graph_models.py`

**Interfaces:**
- Produces（`vocabulary.py`）：
  - `NODE_TYPES: frozenset[str]`、`RELATION_TYPES: frozenset[str]`、`RELATION_LABEL_MAX = 64`、`NODE_ALIAS_MAX = 5`、`NODE_ALIAS_LENGTH_MAX = 128`
  - `normalize_key(text: str) -> str`：`unicodedata.normalize("NFKC")` → `casefold()` → 去掉所有空白与 Unicode 标点（`unicodedata.category(ch).startswith("P")` 或 `ch.isspace()`）。
- Produces（`models.py`）：
  - `GraphNode(..., evidence_ids=(), aliases: tuple[str, ...] = ())`；`node_type` 必须在 `NODE_TYPES`；`aliases` 去重后 ≤ `NODE_ALIAS_MAX`，每个非空且 ≤ `NODE_ALIAS_LENGTH_MAX`。
  - `GraphEdge(..., evidence_ids=(), relation_label: str = "")`；`relation_label` 长度 ≤ `RELATION_LABEL_MAX`（允许空串）；`relation_type` 仍用现有正则校验，不强制词表。
  - `GraphSnapshot.status: Literal["CANDIDATE", "READY", "PARTIAL", "FAILED"]`，`create()` 的 `status` 参数同样放宽。
- Produces（契约）：`GraphSnapshotView.status: Literal["CANDIDATE", "READY", "PARTIAL", "FAILED"]`。

- [x] **Step 1: 写失败的测试**

```python
# tests/unit/graph/test_graph_vocabulary.py
from tap.modules.graph.domain.vocabulary import NODE_TYPES, RELATION_TYPES, normalize_key

def test_vocabulary_matches_spec():
    assert RELATION_TYPES == frozenset({
        "REQUIRES", "APPLIES_TO", "PART_OF", "EXCEPTION_OF", "SUPERSEDES", "TRIGGERS",
        "PRECEDES", "VALIDATED_BY", "RESPONSIBLE_FOR", "USES", "DEFINES", "CONFLICTS_WITH",
        "RELATED_TO",
    })
    assert "PROCESS" in NODE_TYPES and len(NODE_TYPES) == 6

def test_normalize_key_folds_width_case_space_and_punctuation():
    assert normalize_key("核保 流程。") == "核保流程"
    assert normalize_key("Ｈealth-Disclosure Policy") == "healthdisclosurepolicy"
```

```python
# 追加到 tests/unit/graph/test_graph_models.py
def test_node_accepts_process_type_and_bounded_aliases():
    node = GraphNode("node-1", "snapshot-1", "核保流程", "PROCESS", "核保流程", (), ("核保", "underwriting"))
    assert node.aliases == ("核保", "underwriting")
    with pytest.raises(ValueError):
        GraphNode("node-1", "snapshot-1", "x", "PROCESS", "x", (), tuple(f"a{i}" for i in range(6)))

def test_edge_carries_bounded_relation_label():
    edge = GraphEdge("edge-1", "snapshot-1", "n1", "n2", "REQUIRES", RelationOrigin.EXTRACTED, 1.0, ("e1",), "需要")
    assert edge.relation_label == "需要"
    with pytest.raises(ValueError):
        GraphEdge("edge-1", "snapshot-1", "n1", "n2", "REQUIRES", RelationOrigin.EXTRACTED, 1.0, ("e1",), "x" * 65)

def test_snapshot_accepts_partial_status():
    assert GraphSnapshot.create(snapshot_id="s", project_id="p", source_revision_ids=("r",),
                                document_revision_ids=("r",), status="PARTIAL").status == "PARTIAL"
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/graph/test_graph_vocabulary.py tests/unit/graph/test_graph_models.py -v`
Expected: FAIL（`ModuleNotFoundError: vocabulary`、`TypeError: unexpected argument`）

- [x] **Step 3: 实现 `vocabulary.py`，修改 `GraphNode`、`GraphEdge`、`GraphSnapshot` 与 `GraphSnapshotView`**

`models.py` 的 `GraphNode.__post_init__` 用 `NODE_TYPES` 替换硬编码集合；新增字段放在各自 dataclass 最后以保持位置参数兼容。

- [x] **Step 4: 运行测试确认通过，再跑图谱全部单元与契约测试**

Run: `uv run pytest tests/unit/graph tests/contract/test_graph_http.py tests/contract/test_graph_store_contract.py -v`
Expected: 全部 PASS

- [x] **Step 5: 重新生成契约并确认前端类型编译**

Run（仓库根目录）: `make contracts && corepack pnpm --dir apps/tap-ai-frontend exec tsc -b`
Expected: `contracts/` 与 `apps/tap-ai-frontend/src/shared/api/generated/schema.ts` 中 `GraphSnapshotView.status` 含 `PARTIAL`；tsc 无错误。若 `grep -rn '"READY"' apps/tap-ai-frontend/src/features/graph apps/tap-ai-frontend/src/widgets/tap/workspace/LibraryWorkspace.tsx` 有按状态判断"就绪"的分支，把 `"PARTIAL"` 与 `"READY"` 同等对待。

- [x] **Step 6: 提交**

```bash
git add apps/tap-ai-backend/src/tap/modules/graph/domain apps/tap-ai-backend/src/tap/contracts/http.py apps/tap-ai-backend/tests/unit/graph contracts apps/tap-ai-frontend/src/shared/api/generated
git commit -m "feat: add graph relation vocabulary, aliases and partial snapshots"
```

---

### Task 2: 迁移 `0028_graph_fragment_batch` 与片段表的别名、关系标签持久化

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0028_graph_fragment_batch.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql.py`（表定义 `graph_node`、`graph_edge` 加列；新增 `graph_fragment_batch`；`GRAPH_TABLES`；`publish_graph_snapshot` 与 `_memory` 读写新列并支持 `status` 参数）
- Modify: `apps/tap-ai-backend/tests/architecture/test_migration_metadata.py:11-60`（`EXPECTED_TABLES` 加 `graph_fragment_batch`）
- Test: `apps/tap-ai-backend/tests/integration/test_mysql_graph_store.py`、`tests/integration/test_schema_drift.py`（现有）

**Interfaces:**
- Produces（表 `graph_fragment_batch`，经 `_scoped` 创建，主键 `project_id`、`snapshot_id`、`batch_index`）：`job_id String(64)`、`chunk_ids JSON`、`status String(16)`（`PENDING`/`READY`/`FAILED`）、`attempt Integer default 0`、`failure_code String(64) null`、`draft_json JSON null`、`updated_at DATETIME(6)`。
- Produces（列）：`graph_node.aliases JSON null`；`graph_edge.relation_label String(64) not null server_default ""`。
- Produces：`publish_graph_snapshot(session, scope, draft, *, now, status: Literal["READY", "PARTIAL"] = "READY") -> GraphSnapshot`；已存在 READY 或 PARTIAL 的同一快照直接返回已加载值。`_memory` 读回 `aliases` 与 `relation_label`。

- [x] **Step 1: 写失败的集成测试（需要 `TAP_DATABASE_URL`，缺失时沿用现有 skip 约定）**

```python
# 追加到 tests/integration/test_mysql_graph_store.py
@pytest.mark.asyncio
async def test_publish_round_trips_aliases_relation_label_and_partial_status(sessions):
    draft = _draft_with(aliases=("核保", "underwriting"), relation_label="需要")  # 复用文件内已有的 draft 构造方式
    async with sessions() as session, session.begin():
        snapshot = await publish_graph_snapshot(session, VALIDATION_SCOPE, draft, now=_now(), status="PARTIAL")
    assert snapshot.status == "PARTIAL"
    store = MysqlGraphStore(sessions)
    graph = await store.search(VALIDATION_SCOPE, GraphSearchQuery(snapshot.snapshot_id, "*", 50))
    assert graph.nodes[0].aliases == ("核保", "underwriting")
    assert graph.edges[0].relation_label == "需要"
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/integration/test_mysql_graph_store.py -k partial -v`
Expected: FAIL（`TypeError: unexpected keyword 'status'`）

- [x] **Step 3: 写迁移 `0028_graph_fragment_batch.py`**

`down_revision = "0027_prompt_suggestions"`。`upgrade()`：`op.create_table("graph_fragment_batch", ...)` 列与 scope 列、`_scope_constraints` 写法照抄 `0027_prompt_suggestions.py`；`op.add_column("graph_node", sa.Column("aliases", JSON, nullable=True))`；`op.add_column("graph_edge", sa.Column("relation_label", sa.String(64), nullable=False, server_default=""))`。`downgrade()` 反向。

- [x] **Step 4: 修改 `mysql.py` 表定义、`GRAPH_TABLES`、`publish_graph_snapshot`、`_memory`，并把 `graph_fragment_batch` 加入 `EXPECTED_TABLES`**

- [x] **Step 5: 运行集成、架构与 schema drift 测试**

Run: `uv run pytest tests/integration/test_mysql_graph_store.py tests/architecture/test_migration_metadata.py -v && (cd ../.. && make schema-drift)`
Expected: 全部 PASS；schema drift 报告无差异（`schema-drift` 需要隔离 MySQL，缺失时跳过并在 PR 描述注明）

- [x] **Step 6: 提交**

```bash
git add apps/tap-ai-backend/migrations/versions/0028_graph_fragment_batch.py apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql.py apps/tap-ai-backend/tests
git commit -m "feat: persist graph fragment batches, aliases and relation labels"
```

---

### Task 3: job 存储的批次记录、租约续约与 PARTIAL 完成

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/domain/jobs.py`（新增 `GraphBatchStatus`、`GraphFragmentBatch`）
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/application/jobs.py`（`GraphJobStore` 协议与 `InMemoryGraphJobStore`）
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/application/queries.py`（`InMemoryGraphStore.publish` 增加 `status` 参数）
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql_jobs.py`（`MysqlGraphJobStore` 实现）
- Test: `apps/tap-ai-backend/tests/unit/graph/test_graph_jobs.py`、`apps/tap-ai-backend/tests/integration/test_graph_snapshot_publication.py`

**Interfaces:**
- Produces（`domain/jobs.py`）：
  - `class GraphBatchStatus(StrEnum): PENDING, READY, FAILED`
  - `@dataclass(frozen=True, slots=True) class GraphFragmentBatch: snapshot_id: str; batch_index: int; chunk_ids: tuple[str, ...]; status: GraphBatchStatus; attempt: int = 0; failure_code: str | None = None; draft: GraphSnapshotDraft | None = None`；校验：`batch_index >= 0`、`chunk_ids` 非空、READY 必须带 `draft`，其余不得带。
- Produces（`GraphJobStore` 协议新增，内存与 MySQL 都实现）：
  - `async def renew(self, scope, claim: ClaimedGraphJob, *, now: datetime, lease_duration: timedelta) -> ClaimedGraphJob`：租约仍归本 claim 时延长到 `now + lease_duration` 并返回新的 claim；否则抛 `GraphJobLeaseLost`。
  - `async def record_batch(self, scope, claim: ClaimedGraphJob, batch: GraphFragmentBatch, *, now: datetime) -> None`：租约归本 claim 时按（snapshot_id、batch_index）upsert；否则抛 `GraphJobLeaseLost`。
  - `async def load_batches(self, scope, claim: ClaimedGraphJob) -> tuple[GraphFragmentBatch, ...]`：按 `batch_index` 升序。
  - `complete(self, scope, claim, draft, *, now, status: Literal["READY", "PARTIAL"] = "READY") -> GraphJob`：job 状态仍为 READY，快照状态按参数。
- Produces（`mysql_jobs.py` 模块函数）：`serialize_draft(draft: GraphSnapshotDraft) -> dict[str, object]`、`deserialize_draft(snapshot: GraphSnapshot, payload: Mapping[str, object]) -> GraphSnapshotDraft`（节点含 `aliases`，边含 `relation_label`）。
- Produces：`InMemoryGraphStore.publish(scope, draft, *, status: Literal["READY", "PARTIAL"] = "READY")`。

- [x] **Step 1: 写失败的单元测试（内存实现）**

```python
# 追加到 tests/unit/graph/test_graph_jobs.py
@pytest.mark.asyncio
async def test_batches_round_trip_and_partial_completion_keeps_job_ready():
    jobs, claim = await _claimed_job()   # 复用文件内现有的 request+claim 辅助写法
    draft = _one_batch_draft(claim.snapshot)
    await jobs.record_batch(VALIDATION_SCOPE, claim, GraphFragmentBatch(claim.snapshot.snapshot_id, 0, ("chunk-1",), GraphBatchStatus.READY, draft=draft), now=NOW)
    await jobs.record_batch(VALIDATION_SCOPE, claim, GraphFragmentBatch(claim.snapshot.snapshot_id, 1, ("chunk-2",), GraphBatchStatus.FAILED, attempt=3, failure_code="ModelGatewayUnavailable"), now=NOW)
    batches = await jobs.load_batches(VALIDATION_SCOPE, claim)
    assert [b.batch_index for b in batches] == [0, 1] and batches[0].draft == draft
    job = await jobs.complete(VALIDATION_SCOPE, claim, draft, now=NOW, status="PARTIAL")
    assert job.status is GraphJobStatus.READY and job.snapshot.status == "PARTIAL"

@pytest.mark.asyncio
async def test_renew_extends_lease_and_fences_lost_claims():
    jobs, claim = await _claimed_job()
    renewed = await jobs.renew(VALIDATION_SCOPE, claim, now=NOW, lease_duration=timedelta(seconds=60))
    assert renewed.lease_expires_at == NOW + timedelta(seconds=60)
    stale = replace(claim, lease_token="other")
    with pytest.raises(GraphJobLeaseLost):
        await jobs.renew(VALIDATION_SCOPE, stale, now=NOW, lease_duration=timedelta(seconds=60))
    with pytest.raises(GraphJobLeaseLost):
        await jobs.record_batch(VALIDATION_SCOPE, stale, GraphFragmentBatch(claim.snapshot.snapshot_id, 0, ("c",), GraphBatchStatus.PENDING, attempt=1, failure_code="x"), now=NOW)
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/graph/test_graph_jobs.py -v`
Expected: FAIL（`AttributeError: record_batch`）

- [x] **Step 3: 实现 domain 类型、协议、内存实现与 `InMemoryGraphStore.publish(status=)`**

- [x] **Step 4: 运行单元测试确认通过**

Run: `uv run pytest tests/unit/graph -v`
Expected: PASS

- [x] **Step 5: 写失败的 MySQL 集成测试**

```python
# 追加到 tests/integration/test_graph_snapshot_publication.py
@pytest.mark.asyncio
async def test_mysql_batches_survive_reclaim_and_partial_publish(sessions):
    store = MysqlGraphJobStore(sessions)
    claim = await _claim(store)   # 复用文件内 request→claim 的现有写法
    draft = _one_batch_draft(claim.snapshot)
    await store.record_batch(VALIDATION_SCOPE, claim, GraphFragmentBatch(claim.snapshot.snapshot_id, 0, ("chunk-1",), GraphBatchStatus.READY, draft=draft), now=_now())
    reclaimed = (await store.claim(VALIDATION_SCOPE, worker_id="w2", now=_now() + timedelta(minutes=2), lease_duration=timedelta(seconds=60), limit=1))[0]
    assert (await store.load_batches(VALIDATION_SCOPE, reclaimed))[0].draft == draft
    job = await store.complete(VALIDATION_SCOPE, reclaimed, draft, now=_now() + timedelta(minutes=2), status="PARTIAL")
    assert job.snapshot.status == "PARTIAL"
    assert (await MysqlGraphStore(sessions).get_snapshot(VALIDATION_SCOPE, job.snapshot.snapshot_id)).status == "PARTIAL"
```

- [x] **Step 6: 实现 `MysqlGraphJobStore.renew / record_batch / load_batches`、`serialize_draft / deserialize_draft`，`complete` 透传 `status`**

`renew` 与 `record_batch` 都先 `_owned_row`（已有的 `FOR UPDATE` 校验）；`record_batch` 用 `insert(...).on_duplicate_key_update(...)`。

- [x] **Step 7: 运行集成测试确认通过**

Run: `uv run pytest tests/integration/test_graph_snapshot_publication.py tests/unit/graph -v`
Expected: PASS

- [x] **Step 8: 提交**

```bash
git add apps/tap-ai-backend/src/tap/modules/graph apps/tap-ai-backend/tests
git commit -m "feat: record graph fragment batches and renew extraction leases"
```

---

### Task 4: 抽取请求的批次上下文与模型抽取的词表执行

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/domain/extraction.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/adapters/model_gateway_extraction.py`
- Test: `apps/tap-ai-backend/tests/contract/test_graph_extraction_contract.py`

**Interfaces:**
- Produces（`GraphExtractionRequest` 新增字段，位于现有字段之后）：`batch_index: int = 0`、`document_title: str = ""`、`known_entities: tuple[Mapping[str, object], ...] = ()`（每项键：`id`、`label`、`type`、`canonicalKey`）。校验 `batch_index >= 0`。
- Produces（schema）：节点 `type.enum` 为 `NODE_TYPES` 排序列表；节点新增可选 `aliases: array[string] maxItems 5`；边 `relationType.enum` 为 `RELATION_TYPES` 排序列表；边新增必填 `relationLabel: string`。
- Produces（提示词 `GRAPH_EXTRACTION_PROMPT`）在现有文本后追加：
  `"Input also carries documentTitle and knownEntities from earlier batches of the same document. Reuse a known entity's id, label, type, and canonicalKey whenever the same real-world thing appears; never emit a new node for it. Classify processes, steps, or workflow stages as PROCESS. Use relationType only from the provided enum; put the document's own wording for the relation in relationLabel (at most 64 characters). List up to five aliases for a node when the text names it differently."`
- Produces（上下文）：`context = json.dumps({"snapshotId": ..., "batchIndex": ..., "documentTitle": ..., "knownEntities": [...], "chunks": [...]}, sort_keys=True, separators=(",", ":"))`。
- Produces（后处理）：`relationType ∉ RELATION_TYPES` 或两端节点 id 不在本批节点集合内的边被丢弃；丢弃数写到 span `graph.extract_batch` 的属性 `tap.graph.dropped_edges`；`relationLabel` 超过 64 字符截断；`aliases` 超过 5 个取前 5 个。证据摘要校验（`_validate_evidence_against_chunks`）保持 fail-closed。
- `GRAPH_EXTRACTION_PROFILE_DIGEST` 随提示词与 schema 自动变化，无需手改。

- [x] **Step 1: 写失败的契约测试**

```python
# 追加到 tests/contract/test_graph_extraction_contract.py（把 _output() 里的 "GOVERNS" 改为 "REQUIRES" 并加 "relationLabel": "governs"）
@pytest.mark.asyncio
async def test_batch_context_carries_title_and_known_entities():
    gateway = Gateway(_output())
    request = replace(_request(), batch_index=2, document_title="Claims policy",
                      known_entities=({"id": "node-1", "label": "Policy", "type": "ENTITY", "canonicalKey": "policy"},))
    await ModelGatewayGraphExtraction(gateway).extract(request)
    context = json.loads(gateway.requests[0].context)
    assert context["batchIndex"] == 2 and context["documentTitle"] == "Claims policy"
    assert context["knownEntities"][0]["id"] == "node-1"
    assert "knownEntities" in gateway.requests[0].prompt

@pytest.mark.asyncio
async def test_edges_outside_vocabulary_are_dropped_not_fatal():
    output = _output()
    output["edges"].append({**output["edges"][0], "id": "edge-2", "relationType": "GOVERNS"})
    output["edges"].append({**output["edges"][0], "id": "edge-3", "targetNodeId": "missing-node"})
    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())
    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    assert draft.edges[0].relation_label == "governs"

def test_schema_locks_vocabulary_and_process_type():
    from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_SCHEMA
    nodes = GRAPH_EXTRACTION_SCHEMA["properties"]["nodes"]["items"]["properties"]
    edges = GRAPH_EXTRACTION_SCHEMA["properties"]["edges"]["items"]["properties"]
    assert set(nodes["type"]["enum"]) == NODE_TYPES and nodes["aliases"]["maxItems"] == 5
    assert set(edges["relationType"]["enum"]) == RELATION_TYPES
    assert "relationLabel" in GRAPH_EXTRACTION_SCHEMA["properties"]["edges"]["items"]["required"]
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/contract/test_graph_extraction_contract.py -v`
Expected: 新测试 FAIL；现有 `test_invalid_model_facts_fail_closed` 因 `relationLabel` 缺失而 FAIL（下一步一并修）

- [x] **Step 3: 修改 `GraphExtractionRequest`、schema、提示词、上下文组装与后处理**

后处理在构造 `GraphEdge` 之前过滤原始边字典；`span("graph.extract_batch", {"tap.graph.batch_index": request.batch_index}) as current` 包住网关调用与后处理，结束前 `current.set_attribute("tap.graph.dropped_edges", dropped)`。

- [x] **Step 4: 运行契约测试与 schema 检查确认通过**

Run: `uv run pytest tests/contract/test_graph_extraction_contract.py tests/unit/graph -v`
Expected: PASS（含 `check_schema(GRAPH_EXTRACTION_SCHEMA)`）

- [x] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/modules/graph apps/tap-ai-backend/tests/contract/test_graph_extraction_contract.py
git commit -m "feat: batch graph extraction context and enforce the relation vocabulary"
```

---

### Task 5: 规则式假抽取

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/adapters/fake_extraction.py`（整体重写，保留类名 `DeterministicGraphExtraction`）
- Test: `apps/tap-ai-backend/tests/unit/graph/test_fake_graph_publisher.py`、`apps/tap-ai-backend/tests/unit/graph/test_graph_worker.py:36-48`（改用新函数构造 draft）

**Interfaces:**
- Produces：`rule_based_draft(snapshot: GraphSnapshot, chunks: tuple[Mapping[str, object], ...], *, filename: str) -> GraphSnapshotDraft`，`chunks` 元素与 `GraphExtractionRequest.chunks` 同形（`chunkId`、`content`、`anchor`、`contentDigest`、`sourceRevisionId`、`documentRevisionId`）。`DeterministicGraphExtraction.extract(request)` 调用它，`filename=request.document_title or request.snapshot.document_revision_ids[0]`。移除 `deterministic_draft`。
- 规则（模块常量 `_PATTERNS: tuple[tuple[re.Pattern[str], str, bool], ...]`，第三项为是否交换主宾）。切句：按 `[。；！？.;!?\n]` 切分，每句 `strip()`，长度 < 4 跳过。实体片段用 `[^，,、：:（）()"“”\s]{2,40}`（中文）与 `[A-Za-z][A-Za-z0-9 \-]{1,60}?`（英文，非贪婪）：

| 关系 | 中文触发 | 英文触发 | 交换主宾 |
| --- | --- | --- | --- |
| REQUIRES | 需要、须提供、必须提供 | requires、require、must provide | 否 |
| APPLIES_TO | 适用于 | applies to、apply to | 否 |
| PART_OF | 属于、包含于 | is part of、belongs to | 否 |
| EXCEPTION_OF | 是…的例外、除外于 | is an exception of、is an exception to | 否 |
| SUPERSEDES | 取代、替代 | supersedes、replaces | 否 |
| TRIGGERS | 触发 | triggers | 否 |
| PRECEDES | 之后是、然后进入、下一步是 | precedes、is followed by | 否 |
| VALIDATED_BY | 由…验证、由…核对 | is validated by、is verified by | 否 |
| RESPONSIBLE_FOR | 由…负责 | is handled by、is owned by | 是（负责方为主语） |
| USES | 使用 | uses | 否 |
| DEFINES | 定义 | defines | 否 |
| CONFLICTS_WITH | 与…冲突 | conflicts with | 否 |

- 节点类型启发式 `_node_type(label: str) -> str`：含 `审核|核保员|经办|客户|用户|代理人|reviewer|approver|underwriter|agent|customer|user` → ACTOR；含 `系统|平台|门户|接口|system|portal|service|api` → SYSTEM；含 `规则|要求|条款|政策|控制|rule|requirement|policy|control` → REQUIREMENT；含 `流程|步骤|环节|阶段|process|flow|step|stage` → PROCESS；否则 CONCEPT。
- 标识：节点 `node_id = "grn_" + sha256(normalize_key(label))[:32]`，`canonical_key = normalize_key(label)`；证据 `evidence_id = "gre_" + sha256(chunkId)[:32]`（每个切片一条，`anchor` 来自切片）；边 `edge_id = "ged_" + sha256(f"{source}|{relation}|{target}")[:32]`，`confidence=1.0`，`origin=EXTRACTED`，`relation_label` 为上表中命中的触发词条目原样（例如 `需要`、`由…负责`、`requires`），不是句中实际匹配到的子串。同一节点在多个切片出现时合并 `evidence_ids`。主宾规范化后相同则不产生边。
- 回退：整份文档没有任何边时，产出一个节点 `GraphNode("grn_" + sha256(snapshot_id)[:32], snapshot_id, filename, "ENTITY", f"document:{document_revision_id}", 全部证据 id)`，零条边。
- 上限：最多处理前 500 个切片；节点超过 300 个时按首次出现顺序截断并丢弃涉及被截断节点的边。

- [x] **Step 1: 写失败的测试（替换 `test_fake_graph_publisher.py` 内容）**

```python
def _request(*contents: str) -> GraphExtractionRequest: ...  # 每段 content 一个切片，chunkId 为 chunk-{i}

@pytest.mark.asyncio
async def test_rule_based_extracts_chinese_relations():
    draft = await DeterministicGraphExtraction().extract(_request("核保流程需要健康告知。健康告知由核保员负责。"))
    by_key = {node.canonical_key: node for node in draft.nodes}
    assert by_key["核保流程"].node_type == "PROCESS" and by_key["核保员"].node_type == "ACTOR"
    relations = {(e.source_node_id, e.relation_type, e.target_node_id, e.relation_label) for e in draft.edges}
    assert (by_key["核保流程"].node_id, "REQUIRES", by_key["健康告知"].node_id, "需要") in relations
    assert (by_key["核保员"].node_id, "RESPONSIBLE_FOR", by_key["健康告知"].node_id, "由…负责") in relations

@pytest.mark.asyncio
async def test_rule_based_extracts_english_relations_with_stable_ids():
    first = await DeterministicGraphExtraction().extract(_request("Refund requests require finance review."))
    second = await DeterministicGraphExtraction().extract(_request("Finance review precedes payout."))
    finance = [n for n in first.nodes if n.canonical_key == "financereview"][0]
    assert finance.node_id in {n.node_id for n in second.nodes}
    assert first.edges[0].relation_type == "REQUIRES" and first.edges[0].evidence_ids == (first.evidence[0].evidence_id,)

@pytest.mark.asyncio
async def test_rule_based_falls_back_to_a_document_node():
    draft = await DeterministicGraphExtraction().extract(_request("今天天气很好。"))
    assert len(draft.nodes) == 1 and draft.nodes[0].canonical_key.startswith("document:") and draft.edges == ()
    assert set(draft.nodes[0].evidence_ids) == {e.evidence_id for e in draft.evidence}

@pytest.mark.asyncio
async def test_rule_based_only_emits_vocabulary_relations_and_bounded_nodes():
    sentences = [f"实体{i}需要实体{i + 1}。" for i in range(400)]
    draft = await DeterministicGraphExtraction().extract(_request(*sentences))
    assert {e.relation_type for e in draft.edges} <= RELATION_TYPES
    assert len(draft.nodes) <= 300
    assert all(e.source_node_id in {n.node_id for n in draft.nodes} and e.target_node_id in {n.node_id for n in draft.nodes} for e in draft.edges)
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/graph/test_fake_graph_publisher.py -v`
Expected: FAIL（仍产出 "Section" 节点）

- [x] **Step 3: 重写 `fake_extraction.py`，更新 `test_graph_worker.py` 改用 `rule_based_draft(job.snapshot, request_chunks, filename="revision-1")`**

- [x] **Step 4: 运行相关测试确认通过**

Run: `uv run pytest tests/unit/graph tests/integration/test_prompt_suggestion_inputs_mysql.py -v`
Expected: PASS（后者仅在有 MySQL 时运行，确认 `document:` 前缀节点仍被推荐问题实体过滤排除）

- [x] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/modules/graph/adapters/fake_extraction.py apps/tap-ai-backend/tests/unit/graph
git commit -m "feat: replace placeholder graph extraction with rule-based relations"
```

---

### Task 6: worker 分批执行、恢复、重试与配置接线

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/application/worker.py`
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/fragments.py`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py:95-130`（`TapperSettings` 字段）、`:355-400`（解析）、`:960-990`（`create_graph_worker_runtime` 接线）
- Modify: `.env.example:104`、`scripts/run-tapper-e2e.sh:71`
- Test: `apps/tap-ai-backend/tests/unit/graph/test_graph_worker.py`、`apps/tap-ai-backend/tests/unit/graph/test_graph_fragments.py`（新建）、`apps/tap-ai-backend/tests/unit/test_tapper_settings.py`（若已存在设置测试文件则追加，否则在 `tests/unit/` 下新建同名文件）

**Interfaces:**
- Produces（`fragments.py`）：
  - `split_batches(chunks: Sequence[ChunkDraft], *, batch_size: int) -> tuple[tuple[ChunkDraft, ...], ...]`
  - `assemble_fragment(snapshot: GraphSnapshot, drafts: Sequence[GraphSnapshotDraft]) -> GraphSnapshotDraft`：节点按 `node_id` 合并（`evidence_ids` 并集保持首次顺序，`aliases` 并集截到 5 个，`label/type/canonical_key` 取首次出现），边按 `edge_id` 合并（`evidence_ids` 并集，`confidence` 取最大），证据与推理来源按 id 去重。
  - `known_entities_from(drafts: Sequence[GraphSnapshotDraft], *, limit: int = 200) -> tuple[dict[str, object], ...]`：按首次出现顺序，超过 `limit` 时保留最新的 `limit` 个。
- Produces（`GraphWorker.__init__` 新增关键字参数）：`batch_size: int = 10`、`batch_retries: int = 3`、`backoff_seconds: float = 1.0`、`lease_duration: timedelta = timedelta(seconds=60)`、`sleep: Callable[[float], Awaitable[None]] = asyncio.sleep`。
- Consumes：Task 3 的 `renew / record_batch / load_batches / complete(status=)`，Task 4 的 `GraphExtractionRequest(batch_index, document_title, known_entities)`。
- 每个 claim 的流程：
  1. `chunks = (await artifacts.read_chunks(...))[:500]`；`batches = split_batches(chunks, batch_size=...)`；`existing = {b.batch_index: b for b in await jobs.load_batches(...)}`。
  2. 逐批：READY → 直接取 `draft`；FAILED → 跳过；否则从 `existing.attempt` 起循环到 `batch_retries`：成功则 `record_batch(READY, draft=...)`；异常则 `attempt += 1`，`failure_code = type(exc).__name__[:64]`，未到上限时 `record_batch(PENDING, attempt, failure_code)` 并 `await sleep(backoff_seconds * 2 ** (attempt - 1))`，到上限时 `record_batch(FAILED, ...)`。每批结束 `claim = await jobs.renew(...)`。
  3. `ready == 0` → `jobs.fail(failure_code="graph-extraction-failed")`；`failed > 0` → `complete(status="PARTIAL")`；否则 `complete(status="READY")`。
  4. 任一步抛 `GraphJobLeaseLost` → 计入 `lease_lost`，放弃该 claim，不发布。
  - 批次请求：`idempotency_key=f"{claim.request_digest}:batch:{index}"`、`document_title=claim.revision_id`（PR 1 没有文档标题来源，用修订 id 占位，PR 2 接入来源名）、`known_entities=known_entities_from(drafts_so_far)`。
  - `GraphWorkerRun` 新增字段 `partial: int`。
- Produces（设置）：`TapperSettings.graph_batch_size: int`（`TAPPER_GRAPH_BATCH_SIZE`，默认 10，1–50）、`graph_batch_retries: int`（`TAPPER_GRAPH_BATCH_RETRIES`，默认 3，1–10），`create_graph_worker_runtime` 传入 `GraphWorker(batch_size=..., batch_retries=...)`。
- `.env.example`：`TAPPER_GRAPH_EXTRACTION_MODE=model`，紧随其后新增 `TAPPER_GRAPH_BATCH_SIZE=10`、`TAPPER_GRAPH_BATCH_RETRIES=3`，并加一行注释说明 `fake` 仅供测试。`scripts/run-tapper-e2e.sh` 在 `export TAPPER_MODEL_BACKEND=fake` 之后加 `export TAPPER_GRAPH_EXTRACTION_MODE=fake`。

- [x] **Step 1: 写失败的测试**

```python
# tests/unit/graph/test_graph_fragments.py
def test_split_batches_respects_size_and_order(): ...        # 25 个切片、batch_size 10 → 3 批，长度 10/10/5
def test_assemble_fragment_merges_nodes_edges_and_evidence(): ...  # 同一 node_id 跨两批 → evidence 并集、aliases ≤ 5；同一 edge_id → confidence 取最大
def test_known_entities_keep_the_newest_two_hundred(): ...
```

```python
# 追加到 tests/unit/graph/test_graph_worker.py（Artifacts 返回 25 个切片；CountingExtractor 记录每次请求并可按批次号抛错）
@pytest.mark.asyncio
async def test_worker_extracts_in_batches_and_publishes_ready():
    # batch_size=10 → extractor 收到 3 次请求，batch_index 0/1/2，第 2 次请求的 known_entities 含第 1 批节点，idempotency_key 以 ":batch:1" 结尾
    # 断言 job READY、snapshot.status == "READY"、result.partial == 0

@pytest.mark.asyncio
async def test_failed_batch_retries_with_backoff_then_partial():
    # 第 1 批始终抛 RuntimeError，batch_retries=3，sleep 为记录器 → sleep 调用参数 [1.0, 2.0]；
    # 该批 record_batch 最终 FAILED、attempt == 3、failure_code == "RuntimeError"；job READY、snapshot.status == "PARTIAL"、result.partial == 1

@pytest.mark.asyncio
async def test_resume_skips_ready_batches():
    # 预先 record_batch(index 0, READY)；运行后 extractor 只收到 batch_index 1、2

@pytest.mark.asyncio
async def test_all_batches_failed_fails_the_job():
    # 所有批次抛错 → job FAILED、failure_code == "graph-extraction-failed"、snapshot.status == "FAILED"

@pytest.mark.asyncio
async def test_lease_lost_between_batches_stops_without_publishing():
    # jobs.renew 在第 1 批后抛 GraphJobLeaseLost（用子类覆盖 renew）→ result.lease_lost == 1，job 仍 RUNNING，extractor 只被调用 1 次
```

```python
# 设置测试
def test_graph_batch_settings_defaults_and_bounds():
    settings = TapperSettings.from_mapping({**_minimal_env()})
    assert settings.graph_batch_size == 10 and settings.graph_batch_retries == 3
    with pytest.raises(ValueError):
        TapperSettings.from_mapping({**_minimal_env(), "TAPPER_GRAPH_BATCH_SIZE": "0"})
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/graph/test_graph_fragments.py tests/unit/graph/test_graph_worker.py -v`
Expected: FAIL（`ModuleNotFoundError: fragments`、`TypeError: batch_size`）

- [x] **Step 3: 实现 `fragments.py`、重写 `GraphWorker.run_once`、新增设置与接线、改 `.env.example` 与 `run-tapper-e2e.sh`**

- [x] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/graph tests/unit -k "graph or settings" -v`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/modules/graph/application apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py apps/tap-ai-backend/tests .env.example scripts/run-tapper-e2e.sh
git commit -m "feat: extract graph fragments in resumable batches with retries"
```

---

### Task 7: 文档、全量检查与 E2E

**Files:**
- Modify: `docs/superpowers/specs/2026-10-06-knowledge-graph-reasoning-design.md`（5.1 迁移编号：`0028_graph_fragment_batch` 为本 PR，项目图迁移改为 `0029_project_graph`）
- Modify: `docs/architecture.md`（第 4 节"图谱"数据流：按批抽取、PARTIAL 状态、规则式假抽取仅供测试；第 5 节已知差距表更新图谱行）
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md`（子项目 4、5 合并为"知识图谱脉络分析 —— 进行中（PR 1 抽取分批）"，链接本计划与 spec）
- Modify: `docs/superpowers/plans/2026-10-01-product-roadmap.md`（对应行同步）

- [x] **Step 1: 更新四份文档；按 Global Constraints 对客户企业名称做一次大小写不敏感的全文检索，必须无输出**

- [x] **Step 2: 全量检查**

Run（仓库根目录）: `make check && make test && git diff --check`
Expected: 全部通过；`make test` 在无 MySQL 时集成测试按现有约定跳过，有 MySQL 时 Task 2、3 的集成测试运行并通过

- [x] **Step 3: 隔离 E2E**

Run: `make demo-e2e`
Expected: `knowledge-graph.spec.ts` 通过（夹具句子 "Tapper refund requests above five thousand units require finance review." 经规则式抽取得到 `REQUIRES` 边，`edges[0].origin == "EXTRACTED"`），其余 journey 不劣于当前 main（已知的上传卡住与 Stop 竞态失败项如仍存在，记录在 PR 描述中）

- [x] **Step 4: 提交并整理 PR 描述**

```bash
git add docs
git commit -m "docs: record graph extraction batching in architecture and roadmap"
```

PR 描述列出：意图、受影响文档、`make check/test/demo-e2e` 结果、`GRAPH_EXTRACTION_PROFILE_DIGEST` 变化导致已有片段需在 PR 2 的 `graph rebuild` 后重建。
