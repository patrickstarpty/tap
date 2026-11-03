# 图谱查询扩展 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 V1 能力 2 的工程完成项：按快照缓存邻接索引、1 万节点/5 万边 p95 < 300ms、按项目已发布知识生成领域总览。

**Architecture:** 新增不可变 `SnapshotIndex`（领域层）承载搜索、邻居、路径语义；`InMemoryGraphStore` 与 `MysqlGraphStore` 都委托给它；`CachedSnapshotIndexes` 在进程内按快照 LRU 缓存；新增 `GraphOverviewService` 合并已发布来源的活跃快照；前端 Library 增加视图切换。

**Tech Stack:** Python 3.13、SQLAlchemy async、FastAPI、pytest；React/TypeScript/Vitest；Playwright 截图（原型）。

**Spec:** [图谱查询扩展设计](../specs/2026-11-03-graph-query-extension-design.md)

## Global Constraints

- 现有搜索、邻居、路径的排序、上限与错误语义不变；现有图谱存储契约测试必须对新实现全部通过。
- 缓存键 `(project_id, snapshot_id)`，只缓存 `READY` 快照，容量默认 200 万条目（节点+边+证据），single-flight；每次查询仍执行 `require_project_scope`。
- 领域总览：节点按 `(node_type, canonical_key)` 合并，ID 为 `overview:` + 键摘要；默认 `nodeLimit` 150，上限 300；权限 `knowledge.read`；只纳入当前已发布且授权的来源。
- 基准：1 万节点、5 万边，搜索、深度 2 邻居、路径各 200 次，p95 < 300ms；标记 `perf`，不进入 `make test`。
- 契约变更后 `make contracts` 并提交生成物；UI 改动同步 `/prototype` 基准并比较前后截图（`AGENTS.md`）。
- 提交：Conventional Commit 小写祈使句，带 `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`。

---

### Task 1: SnapshotIndex 与存储委托

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/graph/domain/index.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/application/queries.py`（`InMemoryGraphStore` 的查询改为基于 `SnapshotIndex`）
- Test: `apps/tap-ai-backend/tests/unit/graph/test_snapshot_index.py`；现有 `tests/contract/test_graph_store_contract.py` 必须保持通过

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class SnapshotIndex:
      snapshot: GraphSnapshot
      nodes: Mapping[str, GraphNode]
      edges: Mapping[str, GraphEdge]
      evidence: Mapping[str, Evidence]
      adjacency: Mapping[str, tuple[str, ...]]      # node_id -> 排序后的 edge_id
      label_tokens: Mapping[str, frozenset[str]]    # token -> node_ids
      chunk_facts: Mapping[str, tuple[str, ...]]    # chunk_id -> 排序后的 node_id/edge_id
      degree: Mapping[str, int]
      @classmethod
      def build(cls, draft: GraphSnapshotDraft) -> "SnapshotIndex": ...
      @property
      def size(self) -> int: ...                     # 节点+边+证据
      def search(self, query: GraphSearchQuery) -> GraphSubgraph: ...
      def node_detail(self, node_id: str) -> GraphSubgraph: ...
      def evidence_detail(self, evidence_id: str) -> Evidence: ...
      def neighbors(self, query: NeighborQuery) -> GraphSubgraph: ...
      def bounded_path(self, query: PathQuery) -> GraphSubgraph: ...
  def tokenize(text: str) -> frozenset[str]   # 小写；ASCII 按非字母数字切词；CJK 单字与相邻二元组
  ```
  方法缺失事实时抛 `GraphFactNotFound`（与现状一致）。`GraphNode`/`GraphEdge`/`Evidence` 用 `graph/domain/models.py` 中现有类型名（以代码为准）。

- [ ] **Step 1: 读现有 `InMemoryGraphStore` 的 search/neighbors/bounded_path/node_detail/evidence 实现与契约测试**，记录排序与上限规则。
- [ ] **Step 2: 失败测试**：`tokenize("Login Service 登录服务")` 含 `login`、`service`、`登`、`登录`、`录服`；`SnapshotIndex.build` 的 adjacency、degree、chunk_facts（证据 `chunk_id` → 事实）正确；对同一 draft，`SnapshotIndex` 的 search（含 `*`、子串、上限）、neighbors（深度 1/2、节点上限）、bounded_path（可达/不可达/超跳数）结果与重构前 `InMemoryGraphStore` 完全一致——在重构前先把旧实现的结果用若干固定 draft 记录为期望值写入测试。
- [ ] **Step 3: 实现** `index.py`；搜索 = token 交集候选 → 子串确认（label 或 canonical_key，大小写不敏感）→ 沿用旧排序；`*` 返回全部按旧排序截断。
- [ ] **Step 4: `InMemoryGraphStore` 委托**：保存 `SnapshotIndex` 而非重复遍历逻辑。
- [ ] **Step 5: 运行** `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests -q -k graph`。
- [ ] **Step 6: 提交** `refactor: index graph snapshots for bounded queries`

### Task 2: CachedSnapshotIndexes 与 MySQL 存储

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/cache.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql.py`（`_memory` 约 479 行起改为构建 `SnapshotIndex` 并经缓存；查询方法 620–642 行）、`entrypoints/tapper_runtime.py`（`MysqlGraphStore` 构造处注入进程级缓存，API 与 worker 各一个）
- Test: `tests/unit/graph/test_snapshot_cache.py`、`tests/integration/test_graph_index_cache_mysql.py`

**Interfaces:**
- Consumes: `SnapshotIndex.build`、`.size`。
- Produces:
  ```python
  class CachedSnapshotIndexes:
      def __init__(self, *, max_entries: int = 2_000_000) -> None: ...
      async def get_or_load(
          self, key: tuple[str, str], loader: Callable[[], Awaitable[SnapshotIndex]]
      ) -> SnapshotIndex: ...
      def stats(self) -> dict[str, int]  # hits, misses, entries, evictions
  ```
  `MysqlGraphStore(sessions, *, indexes: CachedSnapshotIndexes | None = None)`（`None` 时内部新建一个）。span `graph.index.load`，属性 `tap.graph.cache_hit`、`tap.graph.entries`、`tap.graph.snapshot_id`。

- [ ] **Step 1: 失败测试（单元）**：命中不再调用 loader；容量超限按 LRU 淘汰；单个超过容量的索引不缓存但返回；10 个并发 `get_or_load` 同键只调用一次 loader；loader 异常不缓存并向所有等待者传播。
- [ ] **Step 2: 失败测试（MySQL 集成）**：发布快照后连续两次 `search`，用计数包装的 loader 或统计 `stats()["misses"] == 1`；不同项目同 snapshot_id 不共享（范围检查失败抛现有异常）；`CANDIDATE`/`FAILED` 快照不进入缓存。
- [ ] **Step 3: 实现**；`MysqlGraphStore` 查询：`require_project_scope` → 读快照行校验 `READY` 与项目 → `indexes.get_or_load((project_id, snapshot_id), loader)` → 调用索引方法。
- [ ] **Step 4: 运行** 单元与 `TAP_RUN_MYSQL_INTEGRATION=1` 集成测试、`-k graph` 全部。
- [ ] **Step 5: 提交** `feat: cache graph snapshot indexes per process`

### Task 3: 查询基准

**Files:**
- Create: `apps/tap-ai-backend/tests/perf/__init__.py`、`tests/perf/test_graph_query_benchmark.py`、`tests/perf/graph_fixture.py`（确定性图生成）
- Modify: `apps/tap-ai-backend/pyproject.toml`（注册 marker `perf`，默认 `addopts` 排除：`-m "not perf"`，若已有 addopts 则合并）、`Makefile`（`graph-bench` 目标与 `.PHONY`）

**Interfaces:**
- Produces: `def synthetic_draft(*, nodes: int = 10_000, edges: int = 50_000, seed: int = 7) -> GraphSnapshotDraft`（幂律度：按 `random.Random(seed).paretovariate(1.5)` 权重选端点；每条边 1 条证据，证据 `chunk_id` 在 2000 个切片中循环）。
- Make：
  ```make
  graph-bench: ## graph query latency benchmark (1e4 nodes, 5e4 edges)
  	uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/perf -m perf -q -s
  ```

- [ ] **Step 1: 写基准**：预热 `CachedSnapshotIndexes` 后，各 200 次：随机标签片段搜索（`node_limit=50`）、深度 2 邻居（`node_limit=200`）、随机节点对路径（`max_hops=4`）；计算 p50/p95 并打印；断言三项 p95 < 0.3s。另测 `SnapshotIndex.build` 耗时并打印（不断言）。
- [ ] **Step 2: 运行** `make graph-bench`；若任一 p95 超标，剖析并优化索引（例如邻居 BFS 用 deque 与提前截断、搜索候选先按 token 频率最小集合求交），不得放宽阈值。
- [ ] **Step 3: 确认** `make test` 不运行 perf（`pytest apps/tap-ai-backend/tests -q --co -m perf` 仅在显式 `-m perf` 时收集）。
- [ ] **Step 4: 提交** `test: benchmark graph queries at ten thousand nodes`

### Task 4: 领域总览接口

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/overview.py`
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/routes/knowledge_graph.py`（新路由与视图模型）、HTTP 服务装配（`grep -n "def graph_service" -r apps/tap-ai-backend/src/tap/interfaces`）、`entrypoints/tapper_runtime.py`
- Run: `make contracts`
- Test: `tests/unit/graph/test_overview.py`、`tests/contract/test_graph_overview_http.py`

**Interfaces:**
- Consumes: `CachedSnapshotIndexes`、`GraphStorePort.active_snapshot`。
- Produces:
  ```python
  class PublishedGraphSources(Protocol):
      async def published_source_revisions(self, scope: ProjectScopeContext) -> tuple[PublishedSource, ...]: ...
  @dataclass(frozen=True)
  class PublishedSource: source_id: str; source_revision_id: str
  @dataclass(frozen=True)
  class OverviewMember: snapshot_id: str; node_id: str; source_id: str
  @dataclass(frozen=True)
  class OverviewNode: overview_node_id: str; label: str; node_type: str; degree: int; members: tuple[OverviewMember, ...]
  @dataclass(frozen=True)
  class OverviewEdge: overview_edge_id: str; source: str; target: str; relation_type: str; support: int
  @dataclass(frozen=True)
  class GraphOverview: nodes: tuple[OverviewNode, ...]; edges: tuple[OverviewEdge, ...]; node_type_counts: Mapping[str, int]; source_count: int; snapshot_ids: tuple[str, ...]
  class GraphOverviewService:
      def __init__(self, store: GraphStorePort, sources: PublishedGraphSources, indexes: CachedSnapshotIndexes) -> None: ...
      async def overview(self, scope: ProjectScopeContext, *, node_limit: int = 150) -> GraphOverview: ...
  ```
  `PublishedGraphSources` 的生产实现：复用前端当前流程同一数据来源——项目内状态为已发布（ready）的知识来源及其当前来源修订（见 `routes/knowledge_sources.py` 使用的服务），每个修订以 `active_snapshot(scope, (source_revision_id,))` 取快照；按 `PublishedKnowledgeAuthority` 校验已发布与授权（无权限或已撤回的跳过）。
  HTTP：`POST /api/v1/projects/{project_id}/knowledge/graph/overview`，`operation_id="graph_overview"`，请求 `GraphOverviewRequest{nodeLimit: int = 150, 1..300}`，响应 `GraphOverviewView{nodes[{overviewNodeId,label,nodeType,degree,members[{snapshotId,nodeId,sourceId}]}], edges[{overviewEdgeId,source,target,relationType,support}], stats{sourceCount,nodeTypeCounts}, snapshotIds}`。

- [ ] **Step 1: 失败测试（单元，内存存储 + 假来源）**：两个来源快照中 `(ENTITY,"login-service")` 合并为一个节点、成员两条；重复关系边合并 `support=2`；按合并后度数降序、同度数按 label 再按 ID 排序；`node_limit` 截断后只保留端点都在集合内的边；无来源 → 空图 `source_count=0`；同一快照集合与 `node_limit` 第二次调用命中结果缓存（计数）。合并节点 label 取成员中出现最多的 label，平局取字典序最小。
- [ ] **Step 2: 实现服务**；结果缓存键 `(project_id, tuple(sorted(snapshot_ids)), node_limit)`，LRU 64。
- [ ] **Step 3: 失败测试（契约）**：HTTP 200 结构与字段名、`nodeLimit` 越界 422、无 `knowledge.read` 403（沿用现有授权测试写法，见 `tests/contract/test_graph_http.py`）。
- [ ] **Step 4: 路由与装配；`make contracts`**。
- [ ] **Step 5: 运行** `pytest apps/tap-ai-backend/tests -q -k "graph or contract"` 与 `uv run --project apps/tap-ai-backend python scripts/export_contracts.py --check`。
- [ ] **Step 6: 提交** `feat: serve a project domain overview graph`

### Task 5: 前端领域总览视图与原型同步

**Files:**
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/LibraryWorkspace.tsx`（约 70–159 行）、图谱 API hooks（`grep -rn "graph_search\|knowledge/graph" apps/tap-ai-frontend/src --include=*.ts*`）、文案文件（`widgets/tap/workspace/copy*`）
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.tsx` 中 Library 图谱区（仅在字段展示与设计不一致时同步 fixture）
- Test: 同目录 `*.test.tsx`

**Interfaces:**
- Consumes: `graph_overview` 生成类型。
- Produces: 视图状态 `graphView: "overview" | "source"`，默认 `"overview"`；选中总览节点时成员列表可跳转 `graphView="source"` 并选中对应来源与节点。

- [ ] **Step 1: 失败测试**：默认渲染领域总览（请求 overview）；切换到来源图谱保持现有行为；总览为空显示“尚无已发布知识生成的图谱”（英文 “No published knowledge has produced a graph yet”）；无项目显示现有“未选择项目”；点击成员跳转来源图谱并选中节点。
- [ ] **Step 2: 实现**，沿用现有图谱渲染组件；不展示演示开关或实现说明。
- [ ] **Step 3: 运行** `corepack pnpm --filter @tap/ai-frontend exec vitest run src/widgets/tap` 与 `corepack pnpm --filter @tap/ai-frontend run check`。
- [ ] **Step 4: 原型核对**：启动 `corepack pnpm --filter @tap/web run dev`，打开 `/prototype` Library，截图改动前后（如有改动），保存到 `docs/assets/`（仅当有实质改动时）；`corepack pnpm --filter @tap/web run check`。
- [ ] **Step 5: 提交** `feat: show the domain overview graph in tap ai library`

### Task 6: 文档与总纲

**Files:**
- Modify: `docs/architecture.md`（图谱查询缓存与领域总览）、`docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 2 工程完成勾选；子项目 4 状态与 plan 链接）、`docs/superpowers/plans/2026-10-01-product-roadmap.md`（子项目 4 行）

- [ ] **Step 1: 验证** `make check`、`make test`、`make graph-bench`（记录 p95）。
- [ ] **Step 2: 更新文档**（基准结果写入 V1 总纲能力 2 下一行备注），`git diff --check`。
- [ ] **Step 3: 提交** `docs: record graph query extension completion`
