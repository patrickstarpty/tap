# 关系分析子图与边引用实施计划（PR 3/5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让有依据回答在检索之后确定性地跑一遍"种子 → 扩展 → 路径 → 排序"的关系分析子图，把项目图里的边编号为 R1..R20 供模型逐句引用，R 引用经校验后与切片引用一起持久化、流式下发，并把"主要关系"加入推荐问题输入。

**Architecture:** 关系分析实现为第一个契约式 Agent 子图（`modules/ai/application/agents/`），全程不调模型；它在 `AuthorizedRetrieval.answer` 的证据定稿之后、模型生成之前运行（证据种子依赖检索结果，检索增强又要在 S 标签定稿前补切片，所以调用点必须在这里，而不是现有 `GraphAnswerEnricher` 所在的 `knowledge_service`）。`GraphAnswerEnricher` 与 `graph.enrich` span 由本 PR 退役。R 标签只在 `RelationContext` 内合法；`ports/answers.py` 校验 S 位置规则不变，新增 R 规则；校验失败回退为仅 S 引用而不是整段弃答。图谱状态永不阻塞回答。

**Tech Stack:** Python 3.13、SQLAlchemy async + Alembic（MySQL）、pydantic v2 契约、pytest-asyncio、OpenTelemetry span（`tap.platform.telemetry.tracing.span`）、`make contracts`。

**Spec:** [知识图谱脉络分析设计](../specs/2026-10-06-knowledge-graph-reasoning-design.md) 第 2.1–2.7、5.1、5.2 节（5.3 第 3 个 PR）。

## 依赖 PR 2 的接口

本计划按以下假设使用 PR 2 交付的 `ProjectGraphStore`（`tap.modules.graph.application.project_graph`）；PR 2 计划定稿后以其为准，在 Task 2 开始前修正本节与 Task 2/3/9 的引用：

以下签名已与 PR 2 计划（`2026-10-06-graph-project-merge.md` Task 3）核对一致，端口为 `ProjectGraphStorePort`（`tap.modules.graph.ports.project_store`）：

- `async get_current(scope) -> ProjectGraphVersion | None`；`ProjectGraphVersion.version` 为 `int`，`status ∈ {MERGING, READY, FAILED}`。本 PR 一律用 `str(version)` 作为对外 `graph_version`。
- 所有查询方法接受关键字 `version: int | None = None`；本 PR 在子图开始时取 `current = await store.get_current(scope)`，之后每次调用都传 `version=current.version`；任一调用抛 `ProjectGraphVersionMismatch` → `RelationContext.status = STALE`。
- `async nodes_for_chunks(scope, chunk_ids: tuple[str, ...], *, version) -> tuple[str, ...]`（返回 node_id，按首次出现去重）；`async nodes(scope, node_ids, *, version) -> tuple[ProjectNode, ...]` 把 id 变成节点。
- `async match_aliases(scope, text: str, *, version) -> tuple[AliasMatch, ...]`，`AliasMatch(alias_norm, node_id, start, end)`，最长匹配、遮蔽区间、同一 node_id 只返回首次；再经 `nodes()` 取节点。
- `async neighbors(scope, node_id: str, *, depth: int = 1, node_limit: int = 50, source_revision_ids: tuple[str, ...] = (), version) -> ProjectSubgraph`：单个种子；本 PR 对每个种子各调一次后合并，`source_revision_ids=tuple(sorted(allowed_source_revision_ids))`。
- `async path(scope, source_node_id, target_node_id, *, max_hops: int = 3, source_revision_ids, version) -> ProjectSubgraph`：BFS 最短路径子图，超过 `max_hops` 返回空子图；本 PR 从返回子图沿边由起点走到终点重建有序的 `RelationPath`。
- `ProjectSubgraph(graph_version: int, nodes: tuple[ProjectNode, ...], edges: tuple[ProjectEdge, ...], evidence: tuple[EdgeEvidence | NodeSource, ...])`；来源过滤语义由 PR 2 保证（节点至少一条来源、边两端可见且至少一条证据在集合内）。
- `ProjectEdge(edge_id, source_node_id, target_node_id, relation_type, relation_label, origin, confidence, evidence: tuple[EdgeEvidence, ...])`，`EdgeEvidence(source_revision_id, document_revision_id, chunk_id, anchor: Mapping, content_digest)`。
- `ProjectNode(node_id, label, node_type, canonical_key, aliases: tuple[str, ...], degree, community_id)`。
- 表对象 `graph_project_node`、`graph_project_edge`、`graph_project_edge_evidence` 从 `tap.modules.graph.adapters.mysql` 导出，列名如 spec 1.2。
- PR 2 把 `GraphAnswerEnricher` 改读项目图并保留 `tests/unit/knowledge/test_graph_enrichment.py`；本 PR 删除两者。
- PR 1 的 `tap.modules.graph.domain.vocabulary.normalize_key(text) -> str` 可用。
- 迁移链：PR 1 `0028_graph_fragment_batch` → PR 2 `0029_project_graph` → 本 PR `0030_edge_citations`。

## Global Constraints

- 不新增 Python 或 Node 依赖。
- 扩展：全部种子 1 跳；种子少于 3 个时 2 跳；节点上限 60、边上限 200；节点与边至少有一条证据落在授权来源修订内，并通过 `PublishedKnowledgeAuthority` 发布授权。
- 路径：问题种子两两之间、问题种子与证据种子之间，不超过 3 跳，路径数上限 10。
- 关系证据排序：路径上的边优先，其次按 `confidence × 种子邻接度`（边两端落在种子集内的端点数 0/1/2），同分按 `edge_id` 升序；截断为最多 20 条，编号 R1..R20。
- 关系证据的支撑切片已在 S 列表中则引用 S 标签；否则附切片片段（≤ 300 字）与锚点。
- 检索增强：邻居节点证据切片不在候选集中时最多补 5 条，走同样 ACL 与发布授权，参与融合排序并获得 S 标签；总证据仍 ≤ 20；`TAPPER_GRAPH_RETRIEVAL_AUGMENT`（默认 1）控制。
- R 标签校验：`R1..R20` 位置规则；必须对应本次 `RelationContext` 中的边；含 R 引用的 claim 文本须包含该边两端节点的 label 或任一别名（`normalize_key` 规范化后子串匹配）；边引用 `graph_version` 必须等于本次使用的版本。
- 校验失败处理：去掉该 claim 的非法 R 标签后按 S 标签重新校验；仍有合法标签（S 或合法 R）则保留并计入诊断 `invalid-relation-citation`；没有任何合法标签则丢弃该 claim；全部 claim 被丢弃才弃答。
- 状态：`APPLIED / NOT_READY / STALE / FAILED / EMPTY`；`relation` 意图且 EMPTY 时不生成额外 claim，"未找到直接关系"由 `graph.context_ready` 事件状态驱动前端。
- span：`graph.seed`、`graph.expand`、`graph.path`、`relation.rank`，挂在 `turn.execute` 下，属性记录数量与耗时；删除 `graph.enrich`。
- 事件：新增 `graph.context_ready`（payload：状态、种子数、最多 3 条路径 label 序列、R 数量）；`citation.resolved` 的 payload 带 `kind`。
- 迁移 `0030_edge_citations`：`knowledge_citation_snapshot` 新增可空列 `citation_kind`（server_default `chunk`）、`graph_version`、`edge_id`、`subject_node_id`、`object_node_id`、`relation_type`、`relation_label`；历史行读出为 `chunk`。
- 配置：`TAPPER_GRAPH_RETRIEVAL_AUGMENT` 默认 1；`TAPPER_GRAPH_REASONING` 默认 1，为 0 时整段跳过（回滚手段）。
- 文档、代码、夹具与测试中不得出现客户企业名称（英文缩写或中文全称）。
- 提交信息用小写祈使句 Conventional Commit；每个任务结束前 `git diff --check`。
- 测试命令在 `apps/tap-ai-backend` 目录下用 `uv run pytest` 运行。

## Review Focus

- claim 引用了 R 但文本没有同时包含两端实体：该 R 被剥离，claim 凭剩余 S 保留并记 `invalid-relation-citation`；若没有 S 则整条丢弃（Task 4 Step 1 的 `test_relation_claim_without_both_endpoints_is_stripped_or_dropped`）。
- 种子阶段与回答阶段之间图版本切换：子图返回 STALE，回答退化为纯切片且不带任何 R 引用（Task 3 Step 1 的 `test_version_change_between_seed_and_rank_is_stale`）。
- `relation` 意图下 EMPTY：模型即使编造关系 claim 也会因 R 标签不存在被丢弃，事件状态为 EMPTY，无额外 claim（Task 5 Step 1 的 `test_empty_relation_context_yields_no_relation_claim_and_empty_event`）。
- R 证据的支撑切片已在 S 列表：复用 S 标签，不附片段，不产生重复切片引用（Task 2 Step 1 的 `test_assemble_reuses_s_label_without_snippet`）。
- 检索增强只能补授权且已发布来源的切片：未选来源或未发布的邻居切片被过滤，总证据 ≤ 20（Task 5 Step 1 的 `test_augmentation_never_adds_unauthorized_chunks`）。

---

### Task 1: Agent 子图契约与注册表

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/ai/application/agents/__init__.py`、`protocol.py`、`registry.py`
- Test: `apps/tap-ai-backend/tests/unit/ai/test_agent_registry.py`（新建）

**Interfaces:**
- Produces（`protocol.py`）：
  - `@dataclass(frozen=True, slots=True) class AgentContext: scope: ProjectScopeContext; source_revision_ids: frozenset[str]; graph_version: str | None; turn_id: str | None = None; parent_context: opentelemetry.context.Context | None = None`
  - `class AgentSubgraph(Protocol[InputT, OutputT]): name: str; input_schema: type[InputT]; output_schema: type[OutputT]; async def run(self, context: AgentContext, value: InputT) -> OutputT`
  - 约定（docstring 写明）：`run` 不抛异常，失败以带状态的输出返回；子图之间不互相调用。
- Produces（`registry.py`）：`class AgentRegistry: def register(self, agent: AgentSubgraph[Any, Any]) -> None`（重名抛 `ValueError`）；`def get(self, name: str) -> AgentSubgraph[Any, Any]`（缺失抛 `KeyError`）；`def names(self) -> tuple[str, ...]`（按注册顺序）。`__init__.py` 导出三者。

- [ ] **Step 1: 写失败的测试**

```python
def test_registry_registers_once_and_resolves_by_name(): ...   # register 同名两次 → ValueError；get("missing") → KeyError；names() 保序
def test_agent_context_is_frozen_and_scopes_sources(): ...     # AgentContext(..., source_revision_ids=frozenset({"r1"})) 不可赋值；graph_version 可为 None
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/ai/test_agent_registry.py -v` Expected: FAIL（`ModuleNotFoundError: agents`）

- [ ] **Step 3: 实现 `protocol.py`、`registry.py`、`__init__.py`**

- [ ] **Step 4: 运行确认通过** Run: 同 Step 2 Expected: PASS

- [ ] **Step 5: 提交** `git commit -m "feat: add agent subgraph contract and registry"`

---

### Task 2: 关系分析类型与确定性流水线函数

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/knowledge/application/relation_analysis.py`
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_relation_analysis.py`（新建；含 `FakeProjectGraphStore`，实现"依赖 PR 2 的接口"五个方法，数据在内存字典中）

**Interfaces:**
- Produces（类型）：
  - `class RelationContextStatus(StrEnum): APPLIED, NOT_READY, STALE, FAILED, EMPTY`
  - `@dataclass(frozen=True, slots=True) class EvidenceRef: label: str; chunk_id: str; source_revision_id: str; document_revision_id: str`
  - `class RelationAnalysisInput: query: str; source_revision_ids: frozenset[str]; evidence: tuple[EvidenceRef, ...]; graph_version: str | None`
  - `class SeedNode: node_id: str; label: str; origin: Literal["evidence", "query"]`
  - `class RelationSupport: chunk_id: str; source_revision_id: str; document_revision_id: str; content_digest: str; anchor: Mapping[str, object]; evidence_label: str | None = None; snippet: str | None = None`（校验：`evidence_label` 与 `snippet` 不同时非空；`snippet` ≤ 300 字）
  - `class RelationEvidence: label: str; edge_id: str; subject_node_id: str; subject_label: str; subject_aliases: tuple[str, ...]; object_node_id: str; object_label: str; object_aliases: tuple[str, ...]; relation_type: str; relation_label: str; origin: str; confidence: float; support: tuple[RelationSupport, ...]; on_path: bool`（校验：`label` 匹配 `R(?:[1-9]|1[0-9]|20)`，`support` 非空）
  - `class RelationPath: node_ids: tuple[str, ...]; node_labels: tuple[str, ...]; edge_ids: tuple[str, ...]`（`len(node_ids) == len(edge_ids) + 1`）
  - `class RelationContext: status: RelationContextStatus; graph_version: str | None; seeds: tuple[SeedNode, ...] = (); relations: tuple[RelationEvidence, ...] = (); paths: tuple[RelationPath, ...] = (); augment_chunks: tuple[RelationSupport, ...] = (); diagnostics: Mapping[str, int] = MappingProxyType({})`；校验：非 APPLIED 时 `relations == ()`；APPLIED 时 `relations` 非空且 `graph_version` 非空。方法：`by_label() -> Mapping[str, RelationEvidence]`；`rebind(evidence: tuple[EvidenceRef, ...]) -> RelationContext`（把 `support.chunk_id` 已出现在 `evidence` 中的支撑改为 S 标签并清空 snippet，`augment_chunks` 去掉已入 S 的切片）；`to_prompt_records() -> tuple[dict[str, object], ...]`（每条 `{label, subject, relationType, relationLabel, object, origin, confidence, evidence: [{label} | {chunkId, snippet, anchor}]}`）；`path_summaries(limit=3) -> tuple[tuple[str, ...], ...]`。
- Produces（流水线函数，全部确定性；`store` 为 PR 2 的 `ProjectGraphStore`）：
  - `async def seed_from_evidence(store, scope, version: int, evidence: tuple[EvidenceRef, ...]) -> tuple[ProjectNode, ...]`（`store.nodes_for_chunks(..., version=version)` 后 `store.nodes(...)`；按 `node_id` 去重，保持首次顺序）
  - `async def seed_from_query(store, scope, version: int, query: str) -> tuple[ProjectNode, ...]`（`store.match_aliases(scope, query, version=version)` 的 `node_id` 经 `store.nodes()` 取节点；`match_aliases` 内部已做 `normalize_key`）
  - `def expansion_depth(seed_count: int) -> int`（`2 if seed_count < 3 else 1`）
  - `async def expand(store, scope, version, seeds, *, allowed_source_revision_ids: frozenset[str], node_limit: int = 60, edge_limit: int = 200) -> ProjectSubgraph`（对每个种子调用 `store.neighbors(scope, seed.node_id, depth=expansion_depth(len(seeds)), node_limit=node_limit, source_revision_ids=tuple(sorted(allowed_source_revision_ids)), version=version)`，按 node_id/edge_id 合并去重，节点超过 `node_limit` 时保留种子及按发现顺序的前 `node_limit` 个，边超过 `edge_limit` 时种子相邻的边优先）
  - `async def find_paths(store, scope, version, query_seeds, evidence_seeds, *, allowed_source_revision_ids, max_hops: int = 3, path_limit: int = 10) -> tuple[RelationPath, ...]`（先问题种子两两，再问题×证据；每对调用 `store.path(scope, a, b, max_hops=max_hops, source_revision_ids=..., version=version)`，返回子图为空则跳过，否则 `path_from_subgraph(subgraph, a, b) -> RelationPath` 沿边由 `a` 走到 `b` 重建有序路径；按发现顺序截断到 `path_limit`）
  - `def rank_edges(subgraph: ProjectSubgraph, paths: tuple[RelationPath, ...], seed_ids: frozenset[str], *, limit: int = 20) -> tuple[tuple[ProjectEdge, bool], ...]`（返回 `(edge, on_path)`；排序规则见 Global Constraints）
  - `def assemble(ranked, nodes_by_id: Mapping[str, ProjectNode], evidence: tuple[EvidenceRef, ...], snippets: Mapping[str, str], *, allowed_source_revision_ids) -> tuple[RelationEvidence, ...]`（只保留落在授权修订内的证据；支撑切片在 S 内用 `evidence_label`，否则 `snippet=snippets[chunk_id][:300]`；无可用支撑的边跳过并由调用方计入 `relation-support-unresolvable`；编号 R1..Rn 按 ranked 顺序）
  - `def snippet_chunk_refs(ranked, evidence) -> tuple[tuple[str, str], ...]`（需要读片段的 `(document_revision_id, chunk_id)`，排除已在 S 内的）

- [ ] **Step 1: 写失败的测试（夹具：节点 A/B/C/D，边 A→B REQUIRES 0.9、B→C PRECEDES 0.8、C→D USES 0.7、A→D RELATED_TO 0.95；证据切片 chunk-1 支撑 A→B）**

```python
async def test_seed_from_evidence_dedupes_and_seed_from_query_matches_aliases(): ...  # evidence 两条同切片 → 1 个种子；query "核保与健康告知" 命中别名 → 2 个种子
def test_expansion_depth_is_two_below_three_seeds():  assert expansion_depth(2) == 2 and expansion_depth(3) == 1
async def test_expand_respects_limits_and_authorized_sources(): ...   # node_limit=2 → ≤2 节点；未授权修订的边不出现
async def test_find_paths_bounds_hops_and_count(): ...                # A↔C 经 B 得 1 条 2 跳路径；max_hops=1 → 0 条；path_limit=1 → 1 条
def test_rank_edges_prefers_path_edges_then_confidence_times_adjacency(): ...
    # 路径 A→B→C，种子 {A, C}：顺序 [A→B(on_path), B→C(on_path), A→D(0.95×1), C→D(0.7×1)]，A→D 在 C→D 前
def test_assemble_reuses_s_label_without_snippet(): ...
    # chunk-1 在 evidence 中为 S2 → support.evidence_label == "S2" 且 snippet is None；未在 S 的 chunk-9 → snippet 为 snippets["chunk-9"][:300]
def test_assemble_drops_edges_without_resolvable_support_and_numbers_r_labels(): ...  # 无 snippet 且不在 S → 跳过；其余 label == ("R1","R2",...)
def test_rebind_moves_augmented_chunks_into_s_labels(): ...  # rebind 后 chunk-9 的 support 变 S 标签，augment_chunks 不再含 chunk-9
```

- [ ] **Step 2: 运行确认失败** Run: `uv run pytest tests/unit/knowledge/test_relation_analysis.py -v` Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `relation_analysis.py`**

`rank_edges` 的排序键：`(0 if on_path else 1, path_position if on_path else 0, -confidence × adjacency, edge_id)`；`adjacency = len({source_node_id, target_node_id} & seed_ids)`，为 0 的边仍可入选（扩展 2 跳时出现）。

- [ ] **Step 4: 运行确认通过** Run: 同 Step 2 Expected: PASS

- [ ] **Step 5: 提交** `git commit -m "feat: add deterministic relation analysis pipeline"`

---

### Task 3: `RelationAnalysisAgent`、片段读取与状态/span

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/ai/application/agents/relation_analysis.py`
- Create: `apps/tap-ai-backend/src/tap/modules/knowledge/ports/snippets.py`、`apps/tap-ai-backend/src/tap/modules/knowledge/adapters/artifact_snippets.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/mysql_documents.py:1663`（在 `load_citation` 旁新增 `load_chunk_locators`）
- Delete: `apps/tap-ai-backend/src/tap/modules/knowledge/application/graph_enrichment.py`、`tests/unit/knowledge/test_graph_enrichment.py`
- Test: `apps/tap-ai-backend/tests/unit/ai/test_relation_analysis_agent.py`（新建）、`tests/integration/test_citation_snapshot_transaction.py`（追加）

**Interfaces:**
- Produces（`ports/snippets.py`）：`class ChunkSnippetReader(Protocol): async def snippets(self, refs: tuple[tuple[str, str], ...]) -> Mapping[str, str]`（键 `chunk_id`，值原文；缺失的键不出现）。
- Produces（`artifact_snippets.py`）：`class ArtifactChunkSnippets(ledger: DocumentLedger, artifacts: CitationArtifactStore)`，按 `document_revision_id` 分组，`ledger.load_chunk_locators(revision_ids)` 取 `chunks_blob_locator`，每个修订 `read_chunks(locator)` 一次，按 `chunk_id` 取 `content`。
- Produces（ledger）：`async def load_chunk_locators(self, revision_ids: tuple[str, ...]) -> Mapping[str, ArtifactLocator]`（读 `knowledge_document_revision.chunks_blob_locator`，带 `scope_predicates`，列定义见 `mysql_documents.py:227`）。
- Produces（agent）：`class RelationAnalysisAgent: name = "relation-analysis"; input_schema = RelationAnalysisInput; output_schema = RelationContext; def __init__(self, store: ProjectGraphStore, snippets: ChunkSnippetReader, *, publication_authority: PublishedKnowledgeAuthority | None = None)`；`async def run(context: AgentContext, value: RelationAnalysisInput) -> RelationContext`：
  1. `get_current` 为 None 或 status ≠ READY → NOT_READY；`value.graph_version` 非空且 ≠ `str(current.version)` → STALE。
  2. `graph.seed`（属性 `tap.graph.evidence_seeds`、`tap.graph.query_seeds`）→ `graph.expand`（`tap.graph.node_count`、`tap.graph.edge_count`、`tap.graph.depth`）→ `graph.path`（`tap.graph.path_count`）→ `relation.rank`（`tap.relation.count`）；每个 span 用 `context=context.parent_context` 挂到 `turn.execute` 下；排序后再 `get_current` 一次，版本变化 → STALE。
  3. 种子为空或 ranked 为空 → EMPTY（带 `graph_version`）；发布授权（`authorize_evidence` 对每条 support）失败或任何异常 → FAILED，`diagnostics["failure"] = 1`。
  4. `augment_chunks` = 扩展子图中节点证据切片不在 `value.evidence` 内者，按 `edge` 排序顺序去重，最多 5 条。

- [ ] **Step 1: 写失败的测试（复用 Task 2 的 `FakeProjectGraphStore`，加 `set_version(version, status)` 与 `FakeSnippets`）**

```python
async def test_agent_applies_with_spans_under_parent(span_recorder): ...   # 状态 APPLIED；span 名集合 == {"graph.seed","graph.expand","graph.path","relation.rank"}；无 "graph.enrich"
async def test_agent_not_ready_without_current_version(): ...              # get_current → None → NOT_READY，relations == ()
async def test_version_change_between_seed_and_rank_is_stale(): ...        # store 在第 2 次 get_current 返回 version+1 → STALE，relations == ()
async def test_agent_empty_when_no_seed_matches(): ...                     # query 无别名命中且 evidence 无反查 → EMPTY，graph_version == "7"
async def test_agent_failed_on_store_exception_without_raising(): ...      # neighbors 抛 RuntimeError → FAILED，diagnostics["failure"] == 1
async def test_agent_caps_augment_chunks_at_five(): ...                    # 8 个邻居证据切片不在 S → len(augment_chunks) == 5
```

```python
# 追加到 tests/integration/test_citation_snapshot_transaction.py
async def test_load_chunk_locators_returns_only_scoped_ready_revisions(): ...  # 两个修订一个有 locator → 映射仅含该修订
```

- [ ] **Step 2: 运行确认失败** Run: `uv run pytest tests/unit/ai/test_relation_analysis_agent.py -v` Expected: FAIL

- [ ] **Step 3: 实现 agent、port、adapter、ledger 方法；删除 `graph_enrichment.py` 及其测试；`grep -rn graph_enrichment src tests` 必须无输出**

- [ ] **Step 4: 运行确认通过** Run: `uv run pytest tests/unit/ai tests/unit/knowledge -v` Expected: PASS

- [ ] **Step 5: 提交** `git commit -m "feat: run relation analysis as a contract agent subgraph"`

---

### Task 4: 引用模型 `kind`、R 标签校验与 claim 回退

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/domain/models.py:534-555`（`Citation`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/ports/answers.py:61-98`（`CitationSnapshot`）、`:138-231`（`from_response`）、`:279-370`（`_validate_gateway_response`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/grounded_output.py:9-70`
- Create: `apps/tap-ai-backend/src/tap/modules/knowledge/application/relation_claims.py`
- Test: `tests/unit/knowledge/test_grounded_output.py`、`tests/unit/knowledge/test_answer_service.py`、`tests/unit/knowledge/test_relation_claims.py`（新建）

**Interfaces:**
- Produces（domain）：`@dataclass(frozen=True, slots=True) class EdgeCitation: edge_id: str; graph_version: str; subject_node_id: str; subject_label: str; object_node_id: str; object_label: str; relation_type: str; relation_label: str`；`Citation` 末尾新增 `kind: Literal["chunk", "edge"] = "chunk"`、`edge: EdgeCitation | None = None`；`__post_init__`：`(kind == "edge") == (edge is not None)`。边引用的 `chunk_id / logical_chunk_id / source / chunk_content_hash` 为首条支撑切片的事实（最终仍锚定原文）。
- Produces（`CitationSnapshot` 末尾新增）：`citation_kind: str = "chunk"`、`graph_version: str | None = None`、`edge_id`、`subject_node_id`、`object_node_id`、`relation_type`、`relation_label`（均 `str | None = None`）；校验：`citation_kind ∈ {"chunk","edge"}`，edge 时七个字段全非空且 `relation_label` ≤ 64，chunk 时全为 None。`from_response` 从 `item.edge` 填充。
- Produces（`_validate_gateway_response`）：`kind == "chunk"` 的引用必须排在前面且标签为 `S{position}`（位置按 chunk 引用计数）；`kind == "edge"` 的引用紧随其后，标签为 `R{position}`、匹配 `R(?:[1-9]|1[0-9]|20)`；全部 edge 引用的 `graph_version` 相同；总数仍 ≤ 20。
- Produces（`parse_grounded_answer_payload` 新增关键字参数）：`extra_labels: frozenset[str] = frozenset()`，`allowed_labels = S 标签 ∪ extra_labels`。
- Produces（`relation_claims.py`）：
  - `@dataclass(frozen=True, slots=True) class ReconciledClaims: claims: tuple[GeneratedClaim, ...]; text: str; stripped: int; dropped: int`
  - `def reconcile_relation_claims(generation: AnswerGeneration, context: RelationContext | None, *, graph_version: str | None) -> ReconciledClaims`：对每个 claim 的每个 R 标签检查（a）`context.status is APPLIED` 且 `label in context.by_label()`；（b）`context.graph_version == graph_version`；（c）`normalize_key(claim.text)` 同时包含 subject 与 object 的 `normalize_key(label)` 或任一别名。不满足则剥离该标签（`stripped += 1`）；剩余标签为空 → 丢弃 claim（`dropped += 1`）；`text` 为保留 claim 按原顺序 `"\n\n".join`。
  - `def claim_mentions_both_endpoints(text: str, relation: RelationEvidence) -> bool`

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/knowledge/test_relation_claims.py（context 含 R1: 核保流程 —REQUIRES→ 健康告知，别名 ("核保",)）
def test_relation_claim_without_both_endpoints_is_stripped_or_dropped():
    # claim "核保流程需要先完成体检。" 引用 ("R1","S1") → 保留，citations == ("S1",)，stripped == 1
    # claim "核保流程需要先完成体检。" 引用 ("R1",) → 丢弃，dropped == 1，text 不含该句
def test_relation_claim_naming_alias_is_kept(): ...        # "核保需要健康告知。" 引用 ("R1",) → 保留
def test_r_label_outside_context_or_other_version_is_stripped(): ...  # "R2" 不存在 → 剥离；graph_version="8" vs context "7" → 剥离
def test_all_claims_dropped_leaves_empty_text(): ...       # claims == () and text == ""

# 追加到 tests/unit/knowledge/test_grounded_output.py
def test_grounded_output_accepts_relation_labels_only_when_supplied(): ...  # extra_labels={"R1"} 接受 "R1"；不传 → ValueError("unknown evidence label")

# 追加到 tests/unit/knowledge/test_answer_service.py
def test_snapshot_accepts_edge_citations_after_chunk_citations_with_one_graph_version(): ...  # S1,S2,R1 通过；R1 在 S1 之前 → ValueError；R1/R2 版本不同 → ValueError
def test_edge_citation_snapshot_requires_all_edge_fields(): ...  # CitationSnapshot(citation_kind="edge", edge_id=None) → ValueError；chunk 带 edge_id → ValueError
```

- [ ] **Step 2: 运行确认失败** Run: `uv run pytest tests/unit/knowledge/test_relation_claims.py tests/unit/knowledge/test_grounded_output.py tests/unit/knowledge/test_answer_service.py -v` Expected: 新测试 FAIL

- [ ] **Step 3: 实现 domain、ports/answers、grounded_output、relation_claims**

- [ ] **Step 4: 运行确认通过** Run: `uv run pytest tests/unit/knowledge -v` Expected: PASS

- [ ] **Step 5: 提交** `git commit -m "feat: add edge citations and relation label validation"`

---

### Task 5: 回答链路接线：提示词、上下文、检索增强、状态与 HTTP

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py:66-111`（提示词与 schema 注释）、`:301-409`（`answer`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/retrieve.py:132-174`、`:177-215`（构造器）、`:216-446`（`answer`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/planned_answer.py:16-30`（`AuthorizedAnswerExecution.intent: str = "factual_lookup"`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/domain/models.py:586-600`（`AnswerResponse.relation: RelationOutcome | None = None`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/api.py:223-258`、`apps/tap-ai-backend/src/tap/contracts/http.py:877-899`、`:1070-1073`
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py:96-125`、`:429-457`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/domain/conversations.py:54-62`（`GraphContextStatus` 加 `STALE`、`EMPTY`）
- Test: `tests/unit/knowledge/test_answer_service.py`、`tests/integration/test_knowledge_answer_http.py`、`tests/unit/knowledge/test_knowledge_http_service.py`

**Interfaces:**
- Produces（`litellm.py`）：常量 `_RELATION_ANSWER_PROMPT`，在 `_ANSWER_PROMPT` 之后、`_FLOWCHART_ANSWER_PROMPT` 之前加入 prompt（仅当 `relation_context` 为 APPLIED 或 `relation_first` 为真），文本为：
  `"Relation evidence items are structured records labelled R1..R20 with subject, relationType, relationLabel, object and supporting source labels; they are facts to cite, not sentences to quote. Any claim that states a relationship between two entities must cite the R label of the record that states that relationship, and may also cite the S labels of its supporting sources. Never state a relationship that no R record supports; if the records mention only one of the entities, answer from S evidence alone or return an empty answer and empty claims. Name both entities in a claim that cites an R label exactly as they appear in the record's subject and object. When the query asks about a relationship and no relation evidence is supplied, do not write a claim about the missing relationship; answer only what the S evidence states."`
  `answer()` 新增关键字参数 `relation_context: RelationContext | None = None`、`relation_first: bool = False`；`context_value["relations"] = relation_context.to_prompt_records()`（经 `_redact`），`relation_first` 时 `relations` 键置于 `evidence` 之前，否则之后；`parse_grounded_answer_payload(..., extra_labels=frozenset(relation_context.by_label()))`。
- Produces（domain）：`@dataclass(frozen=True, slots=True) class RelationOutcome: status: RelationContextStatus; graph_version: str | None; seed_count: int; paths: tuple[tuple[str, ...], ...]; relation_count: int; diagnostics: Mapping[str, int]`。
- Produces（`AuthorizedRetrieval.__init__` 新增关键字参数）：`relation_analysis: RelationAnalysis | None = None`、`retrieval_augment: bool = True`，其中 `class RelationAnalysis(Protocol): async def analyse(self, query: str, evidence: tuple[Evidence, ...], source_revision_ids: tuple[str, ...], *, turn_id: str | None) -> RelationContext`；运行时适配器 `RegisteredRelationAnalysis(registry: AgentRegistry, *, scope)` 由 `registry.get("relation-analysis")` 构造 `AgentContext` 并调用 `run`。
- `answer()` 的新顺序（在 `retrieve.py:369` 之后、`:370` 之前插入）：
  1. `relation_analysis` 非空 → `relation = await analyse(...)`；异常由 agent 吞掉，此处只处理 None。
  2. `retrieval_augment and relation.augment_chunks`：用邻居节点 label 拼接为查询，按 `retrieve.py:283-333` 的"业务规则补充检索"写法 `_retrieve(top_k=min(5, 20 - len(evidence)))`，只保留 `chunk_id ∈ augment_chunks` 的命中，追加 `S{len(merged)+1}` 标签，`authorize_selection` 后替换 `run`；再 `relation = relation.rebind(evidence_refs)`。
  3. 生成：`graph_context` 不再传图谱事实（只保留流程图 `flow_context`）；传 `relation_context=relation`、`relation_first=(answer_execution.intent == "relation")`。
  4. 生成后：`reconciled = reconcile_relation_claims(generation, relation, graph_version=relation.graph_version)`；`reconciled.claims == ()` → 弃答；否则用 `replace(generation, text=reconciled.text, claims=reconciled.claims)` 进入现有 `_resolve_generated_claims`，其 `citations_by_label` 合并 R 标签 → 边引用 citation_id。
  5. `citations = (*切片引用, *被任一 claim 引用的边引用)`；边引用 `Citation(kind="edge", edge=EdgeCitation(...), citation_id=id_factory(), evidence_label=R 标签, chunk 字段取首条支撑)`，支撑在 S 内时复制该 S 引用的 chunk 字段，否则由 `RelationSupport` 与所选修订的 `source_content_hash` 构造 `SourceRevisionRef`，锚点非 document 类型时跳过该支撑。
  6. `AnswerResponse.relation = RelationOutcome(...)`；`degradation_reasons` 加 `"invalid-relation-citation"`（stripped > 0）与 `"relation-support-unresolvable"`（diagnostics 中存在），`degraded_mode = missing or stripped > 0`。
- Produces（HTTP）：`RetrievalAnswerResponse.graph_context_status` Literal 加 `"STALE"`、`"EMPTY"`（`http.py:890-892`、`:1070-1072`、`chat_stream.py:150-152`）；新增 `graph_context: GraphContextSummaryView | None = None`（`status`、`graph_version`、`seed_count`、`paths: list[list[str]]`、`relation_count`）；`graph_snapshot_id` 在 APPLIED 时为 `graph_version`。`answer_response_to_http` 从 `response.relation` 推导，不再接受 `graph_context_status / graph_snapshot_id` 参数；`response.relation is None` → `"UNAVAILABLE"`（`TAPPER_GRAPH_REASONING=0` 或未接线）。
- `knowledge_service.py:429-457`：删除 `_graph_enricher` 分支与构造参数，`answer_frozen` 不再传 `graph_context`；`:401` 与 `:337` 的 `route != "retrieve"` 改为 `route not in {"retrieve", "graph"}`。

- [ ] **Step 1: 写失败的测试（`FakeRelationAnalysis` 返回预置 `RelationContext`；`FakeModelPort` 记录 `relation_context` 与 context 键顺序）**

```python
# 追加到 tests/unit/knowledge/test_answer_service.py
async def test_relation_records_precede_evidence_for_relation_intent(): ...   # intent="relation" → context JSON 键顺序 relations 在 evidence 前；否则在后
async def test_empty_relation_context_yields_no_relation_claim_and_empty_event(): ...
    # 模型返回 claim 引用 ("R1",) 与另一 claim 引用 ("S1",) → 回答只含 S1 claim；response.relation.status is EMPTY；citations 全为 kind "chunk"
async def test_stale_context_answers_chunk_only(): ...                        # STALE → prompt 不含 _RELATION_ANSWER_PROMPT，citations 无 edge
async def test_augmentation_never_adds_unauthorized_chunks(): ...
    # augment_chunks 含 3 条，其中 1 条来源修订未选、1 条 publication 拒绝 → 只补 1 条，标签 "S{n+1}"；evidence 总数 ≤ 20
async def test_augmentation_disabled_by_flag(): ...                          # retrieval_augment=False → 不发第二次检索
async def test_edge_citation_reuses_s_chunk_fields_and_gets_r_label(): ...   # R1 支撑为 S2 → 边引用 chunk_id == S2.chunk_id，evidence_label == "R1"，kind == "edge"

# 追加到 tests/integration/test_knowledge_answer_http.py
def test_http_answer_exposes_graph_context_summary_and_edge_citation_kind(): ...  # graphContextStatus == "APPLIED"，graphContext.relationCount == 1，citations[-1].kind == "edge"，edge.relationType == "REQUIRES"
```

- [ ] **Step 2: 运行确认失败** Run: `uv run pytest tests/unit/knowledge/test_answer_service.py tests/integration/test_knowledge_answer_http.py -v` Expected: 新测试 FAIL

- [ ] **Step 3: 实现 litellm、retrieve、planned_answer、domain、api、http 契约、knowledge_service、chat 域状态枚举**

- [ ] **Step 4: 运行确认通过并再生成契约** Run: `uv run pytest tests/unit/knowledge tests/integration/test_knowledge_answer_http.py tests/unit/knowledge/test_knowledge_http_service.py -v && (cd ../.. && make contracts)` Expected: PASS；`contracts/` 与前端 `schema.ts` 含 `graphContext`、`kind`、`STALE`、`EMPTY`

- [ ] **Step 5: 提交** `git commit -m "feat: cite relation edges in grounded answers with retrieval augmentation"`

---

### Task 6: 规划器 `relation` 意图与 `graph` 路由

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/domain/answer_plan.py:12-23`、`:148`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/plan_answer.py:24-61`、`:120-182`、`:206`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/adapters/model_gateway_planner.py:42-50`
- Test: `tests/unit/chat/test_answer_planning.py`

**Interfaces:**
- `INTENTS` 加 `"relation"`；`ROUTES` 加 `"graph"`；`AnswerPlan.__post_init__:148` 改为 `(self.route in {"retrieve", "graph"}) != bool(self.queries)`。
- `_TEMPLATE["relation"] = "explanation"`；`authorized_execution(plan)`：`route not in {"retrieve", "graph"}` 才抛；构造 `_AuthorizedAnswerExecution(..., intent=plan.intent)`。
- 启发式（在 `complex_query` 分支之前）：`re.search(r"什么关系|有何关系|之间.*关系|哪些.*(?:影响|依赖|触发)|之后是什么|下一步是|relationship between|how (?:does|do|is) .+ relate|what (?:follows|comes after)|which .+ (?:affect|depend on|trigger)", original, re.I)` → `intent, route = "relation", "graph"`；`graph` 路由与 `retrieve` 一样生成 `PlannedQuery`。模型规划：`:206` 的允许集合改为 `{"retrieve", "graph", "clarify"}`。
- `PLANNER_PROMPT` 末尾追加：`" Use intent relation and route graph when the question asks how two named things relate, what depends on or follows something, or which rules affect an entity."`

- [ ] **Step 1: 写失败的测试**

```python
@pytest.mark.parametrize("message", ["核保流程和健康告知是什么关系", "哪些规则影响理赔时效", "What is the relationship between underwriting and claims"])
async def test_relation_questions_route_to_graph_with_queries(message): ...  # intent == "relation"，route == "graph"，len(queries) == 1，template_id == "explanation"
def test_graph_route_requires_queries_like_retrieve(): ...                   # route="graph" 且 queries=() → ValueError
def test_authorized_execution_accepts_graph_route_and_carries_intent(): ...  # execution.intent == "relation"
def test_planner_schema_lists_relation_intent_and_graph_route(): ...         # PLANNER_SCHEMA enum 含两者
```

- [ ] **Step 2: 运行确认失败** Run: `uv run pytest tests/unit/chat/test_answer_planning.py -v` Expected: 新测试 FAIL

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行确认通过** Run: `uv run pytest tests/unit/chat tests/integration/test_answer_plan_execution.py -v` Expected: PASS（`test_worker_routes_in_graph_persists_plan_and_binds_completion` 参数表若按路由断言，追加一行 relation 问题 → `"graph"`）

- [ ] **Step 5: 提交** `git commit -m "feat: plan relation questions onto the graph route"`

---

### Task 7: 流事件 `graph.context_ready` 与 `citation.resolved` 的 `kind`

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/contracts/chat_stream.py:109-121`（`Citation`）、`:234-240` 之后新增事件、`:360-383`（联合类型）
- Modify: `apps/tap-ai-backend/src/tap/contracts/http.py:834-856`（`RetrievalCitation`）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py:60-110`（checkpoint 序列化加 `graphContext`）、`:187-247`（`stream_events`）
- Test: `tests/contract/test_worker_stream_events.py`、`tests/contract/test_conversation_http.py`、`tests/contract/test_generated_contracts.py`

**Interfaces:**
- Produces（两份契约的 Citation）：`kind: Literal["chunk", "edge"] = "chunk"`；`edge: EdgeCitationView | None = None`，`EdgeCitationView(edge_id, graph_version, subject: GraphEndpointView(node_id, label), object: GraphEndpointView, relation_type, relation_label)`；校验 `(kind == "edge") == (edge is not None)`。
- Produces（`chat_stream.py`）：`class GraphContextReadyPayload(StreamContractModel): status: Literal["APPLIED","NOT_READY","STALE","FAILED","EMPTY","UNAVAILABLE","NOT_SELECTED"]; graph_version: str | None = None; seed_count: int = Field(ge=0); paths: list[list[str]] = Field(max_length=3); relation_count: int = Field(ge=0, le=20)`；`class GraphContextReadyEvent: type: Literal["graph.context_ready"]; payload: GraphContextReadyPayload`，加入 `ChatStreamEvent` 联合。
- Produces（worker）：`stream_events` 在 `retrieval.hits_ready` 之后、`answer.delta` 之前追加 `{"type": "graph.context_ready", "payload": {...}}`，数据来自 `answer_response.graph_context`（为 None 时 status 取 `evidence.graph_context_status`，其余为 0/[]）；`citation.resolved` 的 `citation.model_dump` 自动带 `kind` 与 `edge`。`ProviderResult` 新增 `graph_context: dict | None`，checkpoint 序列化键 `graphContext`。

- [ ] **Step 1: 写失败的测试**

```python
# 追加到 tests/contract/test_worker_stream_events.py（_Knowledge 返回含一条 edge 引用与 graph_context 的 answer）
async def test_graph_context_ready_event_precedes_answer_and_citation_kind_is_emitted():
    # 事件类型顺序含 ["retrieval.hits_ready","graph.context_ready","answer.delta","citation.resolved"]
    # graph.context_ready payload == {"status":"APPLIED","graphVersion":"7","seedCount":2,"paths":[["核保流程","健康告知"]],"relationCount":1}
    # 最后一个 citation.resolved payload["citation"]["kind"] == "edge"；全部事件通过 ChatEventEnvelope.model_validate

# 追加到 tests/contract/test_conversation_http.py
def test_conversation_turn_summary_accepts_stale_and_empty_graph_status(): ...  # graphContextStatus "STALE"/"EMPTY" 通过 ConversationTurnSummary 校验
```

- [ ] **Step 2: 运行确认失败** Run: `uv run pytest tests/contract/test_worker_stream_events.py tests/contract/test_conversation_http.py -v` Expected: 新测试 FAIL

- [ ] **Step 3: 实现契约与 worker**

- [ ] **Step 4: 运行并再生成契约** Run: `uv run pytest tests/contract -v && (cd ../.. && make contracts && corepack pnpm --dir apps/tap-ai-frontend exec tsc -b)` Expected: PASS；`test_exporter_emits_closed_retrieval_intent_and_complete_chat_event_union` 的事件联合含 `graph.context_ready`；tsc 无错误（前端消费在 PR 4）

- [ ] **Step 5: 提交** `git commit -m "feat: stream graph context readiness and citation kind"`

---

### Task 8: 迁移 `0030_edge_citations` 与引用持久化往返

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0030_edge_citations.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/mysql_documents.py:363-389`（表定义）、`:1612-1633`（insert）、`:1663-1700`（`load_citation` 读新列）
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/adapters/mysql_conversations.py:206-262`、`:855-890`（SELECT 加新列，构造 `CitationSnapshot` 时传入；`citation_evidence_digest` 的输入不变，历史摘要不受影响）
- Modify: `apps/tap-ai-backend/src/tap/platform/db/schema.py:57`（列清单）
- Test: `tests/integration/test_citation_snapshot_transaction.py`、`tests/integration/test_schema_drift.py`（现有）

**Interfaces:**
- 迁移：`revision = "0030_edge_citations"`，`down_revision = "0029_project_graph"`；`upgrade()` 对 `knowledge_citation_snapshot` `add_column`：`citation_kind String(8) nullable server_default "chunk"`、`graph_version String(64)`、`edge_id String(128)`、`subject_node_id String(128)`、`object_node_id String(128)`、`relation_type String(32)`、`relation_label String(64)`；`downgrade()` 反向。
- 表定义同步加列；insert 写七列；`load_citation` 与 `resolve_citations` 读七列，`citation_kind` 为 NULL 时视为 `"chunk"`。

- [ ] **Step 1: 写失败的集成测试（需要 `TAP_DATABASE_URL`，沿用文件内 skip 约定）**

```python
async def test_edge_citation_round_trips_and_historical_rows_default_to_chunk(): ...
    # save_answer_with_citations 写入 S1(chunk) 与 R1(edge, relation_type "REQUIRES", graph_version "7")；
    # load_citation("R1 的 citation_id").citation.citation_kind == "edge" 且七列齐全；
    # 直接 UPDATE 把 S1 行的 citation_kind 置 NULL 后 load_citation(...).citation.citation_kind == "chunk"
async def test_resolve_citations_digest_is_unchanged_by_edge_columns(): ...  # 同一行加/不加边列 citation_digest 相同
```

- [ ] **Step 2: 运行确认失败** Run: `uv run pytest tests/integration/test_citation_snapshot_transaction.py -k "edge_citation or digest_is_unchanged" -v` Expected: FAIL（`Unknown column 'citation_kind'`）

- [ ] **Step 3: 写迁移并修改表定义、insert、两处读取、`schema.py`**

- [ ] **Step 4: 运行集成与 schema drift** Run: `uv run pytest tests/integration/test_citation_snapshot_transaction.py tests/architecture/test_migration_metadata.py -v && (cd ../.. && make schema-drift)` Expected: PASS；drift 无差异（无隔离 MySQL 时跳过并在 PR 描述注明）

- [ ] **Step 5: 提交** `git commit -m "feat: persist edge citation columns"`

---

### Task 9: 推荐问题输入加入主要关系

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/domain/suggestions.py:59-64`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/suggestion_ports.py:82-91`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/suggestions.py:29-32`、`:106-124`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/adapters/model_gateway_suggestions.py:46-58`、`:76-87`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/prompt_suggestion_knowledge.py:171-220`（其后新增 `main_relations`）
- Test: `tests/unit/chat/test_prompt_suggestion_service.py`、`tests/integration/test_prompt_suggestion_inputs_mysql.py`、`tests/unit/chat/test_model_gateway_suggestions.py`

**Interfaces:**
- Produces（domain）：`@dataclass(frozen=True, slots=True) class MainRelation: subject: str; relation_type: str; relation_label: str; object: str`；`SuggestionInputs.relations: tuple[MainRelation, ...] = ()`（末尾默认字段）。
- Produces（port）：`SuggestionKnowledge.main_relations(self, actor_id: str, *, limit: int) -> tuple[MainRelation, ...]`；`_RELATION_LIMIT = 20`；`refresh()` 在 `main_entities` 之后调用并填入 inputs。
- Produces（generator）：payload 增加 `"relations": [{"subject","relationType","relationLabel","object"}]`；`SUGGESTIONS_PROMPT` 追加 `" relations lists the knowledge base's main relationships; prefer questions about how two listed things relate."`
- Produces（`KnowledgeSuggestionSources.main_relations`）：`get_current` 为 None 或非 READY → `()`；否则查 `graph_project_edge` ⋈ `graph_project_edge_evidence`（`source_revision_id ∈ 就绪修订`）⋈ 两次 `graph_project_node`（主语、宾语），排除 `canonical_key` 以 `_STRUCTURAL_NODE_KEY_PREFIXES` 开头的端点，按 `(subject.degree + object.degree) × confidence` 降序、`edge_id` 升序取前 `limit`，按 `(subject, relation_type, object)` 去重。依据校验复用 `ConversationGroundingCheck`，关系问题自动走 Task 5/6 的 R 引用链路，无需改动。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/chat/test_prompt_suggestion_service.py（FakeKnowledge 加 relations_result）
async def test_generator_inputs_include_main_relations(): ...   # captured_inputs.relations == (MainRelation("核保流程","REQUIRES","需要","健康告知"),)
# tests/unit/chat/test_model_gateway_suggestions.py
async def test_generator_payload_serializes_relations(): ...    # context JSON "relations"[0]["relationType"] == "REQUIRES"
# tests/integration/test_prompt_suggestion_inputs_mysql.py（夹具写入 PR 2 项目图表的一个 READY 版本）
def test_main_relations_rank_by_degree_and_confidence_within_ready_sources(owned_project_mysql): ...
    # 三条边：度数积 4×0.9、2×0.95、6×0.5 → 顺序 [3.6, 3.0, 1.9]；来源修订未就绪的边不出现；limit=2 → 2 条
```

- [ ] **Step 2: 运行确认失败** Run: `uv run pytest tests/unit/chat/test_prompt_suggestion_service.py tests/unit/chat/test_model_gateway_suggestions.py -v` Expected: 新测试 FAIL

- [ ] **Step 3: 实现五处修改；`tests/unit/entrypoints/test_prompt_suggestion_grounding.py` 的 FakeKnowledge 若实现了 port，补 `main_relations` 返回 `()`**

- [ ] **Step 4: 运行确认通过** Run: `uv run pytest tests/unit/chat tests/unit/entrypoints tests/integration/test_prompt_suggestion_inputs_mysql.py -v` Expected: PASS

- [ ] **Step 5: 提交** `git commit -m "feat: feed main relations into prompt suggestion refresh"`

---

### Task 10: 配置开关、运行时接线、真实模型 smoke、文档与全量检查

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py:355-375`（设置解析）、`:1593-1600`（删除 `GraphAnswerEnricher`，构造 `AgentRegistry` 与 `RelationAnalysisAgent`）、`:1645-1655`（`KnowledgeHttpService` 去掉 `graph_enricher`；`AuthorizedRetrieval(relation_analysis=..., retrieval_augment=settings.graph_retrieval_augment)`）
- Modify: `.env.example:104` 之后
- Create: `apps/tap-ai-backend/tests/smoke/test_relation_answer_real_model.py`
- Modify: `docs/architecture.md:57-66`、`:86-87`；`docs/superpowers/plans/2026-09-29-v1-roadmap.md:25-27`；`docs/superpowers/plans/2026-10-01-product-roadmap.md:53`
- Test: `tests/unit/entrypoints/test_tapper_runtime.py`

**Interfaces:**
- `TapperSettings.graph_retrieval_augment: bool`（`TAPPER_GRAPH_RETRIEVAL_AUGMENT`，`_fixed_choice` 取 `{"0","1"}`，默认 `"1"`）、`graph_reasoning: bool`（`TAPPER_GRAPH_REASONING`，同法）。`graph_reasoning` 为假或无 `graph_sessions` 时 `relation_analysis=None`（HTTP 状态 `UNAVAILABLE`）。
- 注册：`registry = AgentRegistry(); registry.register(RelationAnalysisAgent(project_graph_store, ArtifactChunkSnippets(repository, artifact_store), publication_authority=publication_authority))`；`RegisteredRelationAnalysis(registry, scope=repository.scope)` 传给 `AuthorizedRetrieval`。
- `.env.example` 紧随图谱变量新增 `TAPPER_GRAPH_RETRIEVAL_AUGMENT=1`、`TAPPER_GRAPH_REASONING=1` 并各加一行注释。
- smoke（照抄 `tests/smoke/test_prompt_suggestions_real_model.py` 的 opt-in 与 runtime 构造）：`test_real_model_relation_question_cites_an_edge_or_reports_empty`：`TAP_RUN_TAPPER_REAL_MODEL_SMOKE != "1"` 跳过；取一个 READY 文档，问 "这份资料里主要流程之间是什么关系"；断言 `graph_context_status ∈ {"APPLIED","EMPTY","NOT_READY"}`，APPLIED 时至少一条 `kind == "edge"` 的引用且每条边引用的 claim 文本含两端 label。

- [ ] **Step 1: 写失败的设置测试**

```python
def test_graph_reasoning_flags_default_on_and_reject_other_values():
    settings = TapperSettings.from_mapping(_minimal_env())
    assert settings.graph_retrieval_augment is True and settings.graph_reasoning is True
    assert TapperSettings.from_mapping({**_minimal_env(), "TAPPER_GRAPH_REASONING": "0"}).graph_reasoning is False
    with pytest.raises(ValueError):
        TapperSettings.from_mapping({**_minimal_env(), "TAPPER_GRAPH_RETRIEVAL_AUGMENT": "yes"})
```

- [ ] **Step 2: 运行确认失败** Run: `uv run pytest tests/unit/entrypoints/test_tapper_runtime.py -k graph_reasoning -v` Expected: FAIL

- [ ] **Step 3: 实现设置、接线、`.env.example`、smoke；`grep -rn "GraphAnswerEnricher\|graph.enrich" apps docs` 仅允许出现在 `docs/archive/` 与本计划**

- [ ] **Step 4: 更新文档；按 Global Constraints 对客户企业名称做大小写不敏感全文检索，必须无输出**

`docs/architecture.md` 问答数据流第 2 步改为"检索 → 关系分析子图（种子/扩展/路径/排序，R1..R20）→ 检索增强 ≤ 5 条 → 生成 → R/S 校验"；已知差距表删除"仅关键词取节点"一行，改为"前端边引用渲染与高亮待 PR 4"。V1 总纲能力 3 勾选"回答同时引用关系边与原文片段"子项（高亮与 golden set 仍未勾）；产品路线图"知识图谱脉络分析"行标注 PR 3 完成。

- [ ] **Step 5: 全量检查** Run（仓库根目录）: `make check && make test && git diff --check` Expected: 全部通过；无 MySQL 时集成测试按约定跳过；smoke 默认一个 skip

- [ ] **Step 6: 提交** `git commit -m "feat: wire relation analysis flags and document edge citations"`

PR 描述列出：意图、受影响文档、`make check/test` 结果、`0030_edge_citations` 需在 `0029_project_graph` 之后执行、`GraphAnswerEnricher` 退役、前端消费 `graph.context_ready` 与 `kind` 由 PR 4 承接。
