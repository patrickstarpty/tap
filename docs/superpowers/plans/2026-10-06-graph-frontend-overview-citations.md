# 图谱总览、节点详情与边引用前端实施计划（PR 4/5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Library 的知识图谱默认显示项目级总览（真实社区、度数定大小、可加载更多），节点详情面板可追问与打开原件，对话中的边引用以 `[R2]` 芯片、悬停卡片与右侧证据面板呈现并能跳到 Library 高亮路径；后端退役 `GET /snapshots`；`ui:capture` 增加四张截图。

**Architecture:** 前端继续使用 `apps/tap-ai-frontend` 现有的 SVG 画布组件 `widgets/tap/workspace/KnowledgeGraph.tsx`（工具栏、缩放、社区栏、无障碍摘要均已就绪），把它的数据源从单来源假布局 `publishedGraphData.ts` 换成项目图 API 加 forceAtlas worker 的社区分区布局；`features/graph` 只负责 API、查询键、布局与高亮状态，`features/knowledge` 只负责引用芯片与证据面板（仓库规则 `no-feature-to-feature` 禁止两者互相导入，见 `apps/tap-ai-frontend/dependency-cruiser.cjs:50-56`），跨特性的接线全部放在 `widgets/tap/TapperWorkspace.tsx`。高亮态经 `window.history.state` 传递，与现有 `pushState` 导航方式一致（`TapperWorkspace.tsx:2433-2465`）。

**Tech Stack:** React 19、TypeScript 5.9、@tanstack/react-query 5、antd 6、graphology + graphology-layout-forceatlas2（已在 `package.json`）、Vitest 4 + Testing Library、Playwright 1.62；后端 FastAPI + pytest；`make contracts` 生成 `src/shared/api/generated/schema.ts`（禁止手改）。不新增任何依赖；`sigma` 的唯一使用者在本 PR 删除后由 Task 3 从 `package.json` 移除。

**Spec:** [知识图谱脉络分析设计](../specs/2026-10-06-knowledge-graph-reasoning-design.md) 第 3.1（仅退役 `GET /snapshots`）、3.2、3.3、3.4 节与 2.7 节的事件定义（PR 4 范围）。

## 依赖 PR 2/3 的契约

本计划假定 PR 2、PR 3 已合并，`contracts/openapi/api.json` 与 `schema.ts` 已包含以下内容；Task 1 第一步就是对照 `schema.ts` 核对名称，名称不同时以生成类型为准改 `features/graph/model/graph.ts` 的别名，不改本计划其他任务引用的本地接口名。

- 路由（前缀 `/api/v1/projects/{project_id}/knowledge/graph`）：`GET /project`、`GET /overview?sourceRevisionId[]&communityId[]&nodeLimit`、`POST /query`、`POST /neighbors`（请求体 `{ nodeId, depth, nodeLimit, sourceRevisionIds, graphVersion }`；旧的 `POST /nodes/{node_id}/neighbors` 仅服务片段图，前端不再使用）、`POST /path`、`GET /nodes/{node_id}`、`POST /highlight`、`POST /fragments/{revision_id}/retry`。所有响应带 `graphVersion: string`；请求体或查询参数可带 `graphVersion`，不符返回 409（problem type 以 `/graph-version-mismatch` 结尾）。
- 版本号：PR 2 契约中 `graphVersion` 是整数（`graph_version: int | null`），前端类型用 `number | null`；本计划示例里的 `v2`、`v3` 读作 `2`、`3`。409 的 problem `type` 以 `/graph-version-mismatch` 结尾，`detail` 含当前版本号。
- `GET /project` 响应（PR 2 `ProjectGraphView`）：`{ graphVersion, status: "EMPTY" | "MERGING" | "READY" | "FAILED", nodeCount, edgeCount, mergedAt: string | null, communities: [{ communityId, label, size }], extractingRevisionIds: string[], partialRevisionIds: string[] }`；本计划下文提到的 `fragments` 由这两个数组派生。原文：`fragments: [{ sourceRevisionId, documentRevisionId, status: "EXTRACTING" | "PARTIAL" | "FAILED", failedBatches, totalBatches }] }`；`fragments` 只列非 READY 的已发布修订，不在列表中的已发布修订视为 READY。
- 子图响应（`/overview`、`/query`、`/neighbors`、`/path`、`/highlight`）：`{ graphVersion, nodes: [{ nodeId, label, nodeType, canonicalKey, degree, communityId, aliases: string[] }], edges: [{ edgeId, sourceNodeId, targetNodeId, relationType, relationLabel, origin, confidence }], evidence: [{ ownerKind, ownerId, sourceRevisionId, documentRevisionId, chunkId, anchor, contentDigest, snippet }] }`（PR 2 `ProjectGraphSubgraphView`，没有 `truncated` 字段；"加载更多"以 `nodes.length >= nodeLimit` 判定还有更多）。
- `GET /nodes/{node_id}` 响应（PR 2 `ProjectGraphNodeDetailView`）：`{ graphVersion, node: 同上节点, community: { communityId, label, size } | null, sources: [{ sourceRevisionId, documentRevisionId, sourceName: string | null, evidence: [{ ..., snippet: string | null }] }], relations: [{ relationType, edges: [同上边] }], neighbors: [同上节点] }`。对端节点由 `edges[].sourceNodeId / targetNodeId` 在 `neighbors` 中查到；`sourceId` 不在响应中，Task 4 用 `usePublishedSourcesQuery` 的 `revisionId → sourceId` 映射得到"打开原件"的目标。
- 引用视图 `Citation` / `RetrievalCitation` 增加 `kind: "chunk" | "edge"`；`kind === "edge"` 时另有 `edgeId, graphVersion, subject { nodeId, label }, object { nodeId, label }, relationType, relationLabel, evidence { chunkId, sourceRevisionId, snippet, anchor }`。历史引用 `kind` 缺省视为 `chunk`。
- SSE 事件 `graph.context_ready`，payload `{ status: "APPLIED" | "NOT_READY" | "STALE" | "FAILED" | "EMPTY", seedCount: number, paths: string[][]（≤3，每条为 label 序列）, relationCount: number }`；同名事件也出现在 `GET /conversations/{id}/events` 的 `eventType` 枚举中。
- E2E 的 fake 模型后端在 `RelationContext` 非空时产出的 claim 引用 R 标签（Task 11 Step 2 验证；不成立时按该步骤处理）。

## Global Constraints

- 不新增 Node 或 Python 依赖；`sigma` 在 Task 3 删除其唯一使用者后从 `package.json` 移除并 `corepack pnpm install --frozen-lockfile=false` 更新锁文件。
- 总览默认 `nodeLimit = 150`，"加载更多"每次加 150，上限 500（沿用 `features/graph/model/graph.ts:44` 的 `MAX_GRAPH_NODES`）。
- 节点半径 `12 + Math.min(degree, 20) * 0.9`（SVG 单位，12–30）；颜色按社区取调色板 `communityColor(index)`，社区按 `size` 降序编号，"其他"社区固定灰色 `#64748b`。
- 边标签显示条件：悬停或选中节点的邻接边（现有逻辑 `KnowledgeGraph.tsx:500-505`）**或** 画布缩放 `zoom >= EDGE_LABEL_ZOOM = 1.25`（现有缩放范围 0.75–1.75，步长 0.25，`KnowledgeGraph.tsx:33-35`）。
- 社区列表来自 `GET /project` 的 `communities`，替换 `publishedGraphData.ts:11-21` 的文件名正则分组；去掉"Published source graph"说明与"图谱来源"下拉（`LibraryWorkspace.tsx:98-113,149-153`）；来源过滤复用 Library 顶部筛选后的 `visibleSources`（`LibraryWorkspace.tsx:204-214`），传入已发布修订 id。
- 节点详情面板必含：类型、别名、来源、按关系类型分组的关系（每组默认显示 10 条，超出显示"显示全部 (n)"）、证据片段与"打开原件"、"就此提问"。
- 对话：边引用句内渲染为 `[R2]`，与 `[1]` 并列且各自独立编号（展示顺序）；悬停卡片"A —关系→ B"加片段；点击打开右侧证据面板：上半部分迷你路径图（本回答全部边引用），下半部分切片原文；"在 Library 中查看"跳转并进入高亮态；摘要行"搜索 N 个来源 · M 段原文 · K 条关系"，展开可见种子数与路径；`graph.context_ready.status === "EMPTY"` 且 `querySeedCount >= 2` 时回答上方提示"未找到直接关系证据，以下为资料原文依据"（`querySeedCount` 为问题本身识别到的实体数，以"问题点名两个以上实体却无关系"作为关系问题的判定；`seedCount` 含证据种子，保留兼容）。
- 高亮不只靠颜色：画布旁以文字列出路径（`role="region"`，名称"高亮路径 / Highlighted path"）。
- 任一图谱请求返回 409 时使 `["graph", projectId]` 前缀的查询失效并重新获取，不弹错误。
- 文案全部放 `WORKSPACE_COPY`（`widgets/tap/workspace/copy.ts`）、`features/knowledge/copy.ts` 或 `GroundedAnswer.tsx` 的 `ANSWER_COPY`，EN 与中文成对；`copy.test.ts` 禁止"prototype/demo/原型/演示/示例"字样；文档、代码、夹具中不得出现客户企业名称。
- `ui:capture` 截图 1280×720、`deviceScaleFactor: 2`（PNG 2560×1440，`tests/e2e/ui-capture.spec.ts:7,112-114`），新增 `10-graph-overview`、`11-graph-node-detail`、`12-answer-edge-citations`、`13-graph-highlight`。
- `apps/web`（含 `/prototype`）一律不改。提交信息用小写祈使句 Conventional Commit；每个任务结束前 `git diff --check`。
- 前端测试命令在 `apps/tap-ai-frontend` 下用 `corepack pnpm exec vitest run <path>`；后端在 `apps/tap-ai-backend` 下用 `uv run pytest`。

## Review Focus

- 带边引用的历史回答，其 `graphVersion` 早于当前项目图：芯片必须照常渲染（不依赖任何请求），"在 Library 中查看"调用 `POST /highlight` 时不带 `graphVersion`，响应缺失的边 id 以"图谱版本已更新，部分关系不再可用"提示，剩余边仍高亮（Task 5 Step 1 的 `renders a version-updated notice when highlight omits requested edges`）。
- 新项目（`communities` 为空、`nodeCount === 0`）：显示空状态文案与"上传资料后自动建图"提示，而不是空白画布或 0 节点的 SVG（Task 3 Step 1 的 `shows an empty state for a project without communities`）。
- 一个节点有 40 条关系：面板按关系类型分组，每组只先显示 10 条并提供"显示全部 (n)"，DOM 中初始关系项不超过 10 × 组数（Task 4 Step 1 的 `groups forty relations by type and limits each group`）。
- 键盘：Tab 顺序为 `[1]`→`[R1]`→下一段；`[R1]` 上 Enter 打开证据面板且焦点进入面板标题，Escape/关闭后焦点回到芯片（Task 8 Step 1 的 `keeps citation chips in reading order and returns focus after closing the evidence panel`）。
- 中文界面：所有新增文案在 `locale="zh"` 下不出现英文回退，摘要行为"搜索 2 个来源 · 3 段原文 · 1 条关系"（Task 7 Step 1 的 `renders the Chinese answer summary line` 与 Task 3 Step 1 的 `renders Chinese overview copy`）。

---

### Task 1: 图谱客户端、查询键与 409 处理

**Files:**
- Modify: `apps/tap-ai-frontend/src/features/graph/model/graph.ts`（整体重写）
- Modify: `apps/tap-ai-frontend/src/features/graph/api/client.ts`（整体重写）
- Modify: `apps/tap-ai-frontend/src/features/graph/api/queries.ts`（整体重写）
- Test: `apps/tap-ai-frontend/src/features/graph/api/client.test.ts`（新建）、`apps/tap-ai-frontend/src/features/graph/api/queries.test.tsx`（新建）

**Interfaces:**
- Produces（`model/graph.ts`，全部为 `components["schemas"][...]` 的别名，名称在 Step 1 核对）：`GraphProject`、`GraphCommunity`、`GraphFragmentStatus`、`GraphNode`、`GraphEdge`、`GraphSubgraph`、`GraphNodeDetail`、`GraphNodeSource`、`GraphNodeRelation`；常量 `MAX_GRAPH_NODES = 500`、`OVERVIEW_PAGE = 150`；`boundedGraph(graph: GraphSubgraph): GraphSubgraph` 保留。
- Produces（`api/client.ts`）：
  - `class GraphVersionConflictError extends Error { readonly currentVersion: string | null }`（409 时从 problem body 的 `graphVersion` 读取，缺失为 `null`）。
  - `interface GraphClient { project(signal?): Promise<GraphProject>; overview(input: { sourceRevisionIds: string[]; communityIds: string[]; nodeLimit: number; graphVersion?: string }, signal?): Promise<GraphSubgraph>; query(input: { query: string; sourceRevisionIds: string[]; nodeLimit?: number; graphVersion?: string }, signal?): Promise<GraphSubgraph>; neighbors(nodeId: string, input: { depth?: 1 | 2; nodeLimit?: number; graphVersion?: string }, signal?): Promise<GraphSubgraph>; path(input: { sourceNodeId: string; targetNodeId: string; nodeLimit?: number; graphVersion?: string }, signal?): Promise<GraphSubgraph>; node(nodeId: string, graphVersion?: string, signal?): Promise<GraphNodeDetail>; highlight(edgeIds: string[], signal?): Promise<GraphSubgraph>; retryFragment(revisionId: string, idempotencyKey: string): Promise<void> }`
  - `createGraphClient(projectId: string, baseUrl = ""): GraphClient`；`highlight` 永不发送 `graphVersion`；`retryFragment` 发送 `Idempotency-Key` 头与 `Origin`（照抄 `features/knowledge/api/client.ts` 的写法）。
- Produces（`api/queries.ts`）：
  - `graphKeys = { all: (p) => ["graph", p], project: (p) => ["graph", p, "project"], overview: (p, version, sourceRevisionIds, communityIds, nodeLimit) => [...], search: (p, version, query, sourceRevisionIds) => [...], node: (p, version, nodeId) => [...], highlight: (p, edgeIds) => [...] }`（`sourceRevisionIds`、`communityIds`、`edgeIds` 先排序再入键）。
  - `useGraphProject(projectId)`、`useGraphOverview(projectId, graphVersion | null, { sourceRevisionIds, communityIds, nodeLimit })`、`useGraphSearch(projectId, graphVersion | null, query, sourceRevisionIds)`、`useGraphNode(projectId, graphVersion | null, nodeId | null)`、`useGraphHighlight(projectId, edgeIds)`、`useRetryFragmentMutation(projectId)`（成功后 `invalidateQueries(graphKeys.project)`）。
  - `useGraphVersionGuard(projectId, errors: unknown[])`：任一 error 是 `GraphVersionConflictError` 时 `invalidateQueries({ queryKey: graphKeys.all(projectId) })`，每个版本只触发一次。所有查询 `retry: false`。

- [ ] **Step 1: 核对生成类型**

Run（仓库根目录）: `make contracts && grep -n "GraphProject\|GraphOverview\|GraphNodeDetail\|GraphHighlight\|graph.context_ready\|\"kind\"" apps/tap-ai-frontend/src/shared/api/generated/schema.ts | head -40`
Expected: 能找到"依赖 PR 2/3 的契约"中的每个 schema 与路径；记下实际 schema 名用于 `model/graph.ts` 的别名。

- [ ] **Step 2: 写失败的测试**

```ts
// client.test.ts（用 vi.stubGlobal("fetch", ...) 记录请求）
it("sends graphVersion with overview and never with highlight", ...)  // overview 查询串含 graphVersion=v2&nodeLimit=150；highlight 请求体 {"edgeIds":[...]} 无 graphVersion
it("maps 409 to GraphVersionConflictError with the current version", ...)  // body {type:".../graph-version-mismatch", graphVersion:"v3"} → error.currentVersion === "v3"
it("retries a fragment with an idempotency key", ...)  // POST .../fragments/rev_x/retry，头 Idempotency-Key
// queries.test.tsx
it("invalidates every graph query once after a version conflict", ...)  // 第一次 overview 返回 409、project 第二次返回 v3 → overview 以 v3 重新请求；fetch 对 overview 恰好 2 次
```

- [ ] **Step 3: 运行测试确认失败**

Run: `corepack pnpm exec vitest run src/features/graph/api`
Expected: FAIL（`createGraphClient(...).project is not a function`）

- [ ] **Step 4: 重写 `model/graph.ts`、`client.ts`、`queries.ts`**

- [ ] **Step 5: 运行测试确认通过并编译**

Run: `corepack pnpm exec vitest run src/features/graph/api && corepack pnpm exec tsc -b`
Expected: 新测试 PASS；tsc 报错只来自 `LibraryWorkspace.tsx`、`KnowledgeGraphExplorer.tsx` 等旧调用方（Task 3 处理）

- [ ] **Step 6: 提交**

```bash
git add apps/tap-ai-frontend/src/features/graph/api apps/tap-ai-frontend/src/features/graph/model/graph.ts apps/tap-ai-frontend/src/shared/api/generated
git commit -m "feat: add project graph client with version conflict refetch"
```

---

### Task 2: 社区分区布局、调色板与 worker

**Files:**
- Create: `apps/tap-ai-frontend/src/features/graph/model/layout.ts`、`apps/tap-ai-frontend/src/features/graph/model/palette.ts`
- Modify: `apps/tap-ai-frontend/src/features/graph/workers/forceAtlas.worker.ts:5-24`
- Test: `apps/tap-ai-frontend/src/features/graph/model/layout.test.ts`（新建）

**Interfaces:**
- Produces（`palette.ts`）：`GRAPH_PALETTE: readonly string[]`（12 色，取自 `widgets/tap/workspace/graphLayout.ts:44-54` 的现有色值并补足到 12）、`OTHER_COMMUNITY_COLOR = "#64748b"`、`communityColor(index: number, isOther: boolean): string`。
- Produces（`layout.ts`）：
  - `type LayoutPosition = { id: string; x: number; y: number }`
  - `seedPositions(nodes: { id: string; communityId: string }[], communityOrder: string[]): LayoutPosition[]`：社区中心均匀分布在半径 0.6 的圆上（单社区居中），节点围绕中心按索引角度、半径 0.15 排布。
  - `scaleToCanvas(positions: LayoutPosition[], width: number, height: number, margin = 80): LayoutPosition[]`。
  - `useGraphLayout(nodes: GraphNode[], edges: GraphEdge[], communityOrder: string[]): Map<string, { x: number; y: number }>`：有 `Worker` 时发送 `{ nodes: [{id, communityId}], edges, communityOrder, reducedMotion }` 并在回包后 `scaleToCanvas`；无 `Worker`（jsdom）时同步返回 `scaleToCanvas(seedPositions(...))`。
- Produces（worker 消息）：入参新增 `communityOrder: string[]` 与节点 `communityId`，初始位置改用 `seedPositions`，`forceAtlas2.assign` 迭代次数 `Math.min(150, order * 2)`，其余不变。

- [ ] **Step 1: 写失败的测试**

```ts
it("seeds communities on separate arcs and keeps members near their center", ...)  // 3 个社区各 4 节点：同社区节点到其中心距离 ≤ 0.2，不同社区中心距离 ≥ 0.8
it("scales positions inside the canvas with margin", ...)                              // 所有 x ∈ [80, 1480]、y ∈ [80, 1040]（GRAPH_WIDTH 1560、GRAPH_HEIGHT 1120）
it("falls back to seeded positions without a Worker", ...)                             // renderHook 下 Map.size === nodes.length
```

- [ ] **Step 2: 运行测试确认失败**

Run: `corepack pnpm exec vitest run src/features/graph/model/layout.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `palette.ts`、`layout.ts`，修改 worker**

- [ ] **Step 4: 运行测试确认通过**

Run: `corepack pnpm exec vitest run src/features/graph/model`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-frontend/src/features/graph/model apps/tap-ai-frontend/src/features/graph/workers
git commit -m "feat: seed force layout by community for the graph overview"
```

---

### Task 3: 总览画布、社区列表与 Library 接线

**Files:**
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/graphLayout.ts`（类型重写：`GraphCommunity` 改为 `string`；删除 `COMMUNITY_ORDER`、`COMMUNITY_COLORS`、`GraphNodeKind`；节点新增 `color: string`、`communityLabel: string`、`nodeType: string`、`aliases: string[]`、`degree`；边新增 `relationType`，`label` 为 `relationLabel || relationType`）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/KnowledgeGraph.tsx`（整体重写为数据无关画布）
- Create: `apps/tap-ai-frontend/src/features/graph/components/CommunityList.tsx`、`apps/tap-ai-frontend/src/features/graph/components/GraphOverview.tsx`、`apps/tap-ai-frontend/src/features/graph/components/toOverviewData.ts`
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/LibraryWorkspace.tsx:29-34,52-164,504-521`
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/copy.ts`（`library` 段）、`apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.css:1664-1725`（删除 `.tap-project-graph-source/-controls`、`.tap-graph-live-workspace` 规则）
- Delete: `apps/tap-ai-frontend/src/widgets/tap/workspace/publishedGraphData.ts`、`apps/tap-ai-frontend/src/features/graph/components/{KnowledgeGraphExplorer,KnowledgeGraphCanvas,GraphInspector}.tsx` 及三者 `.test.tsx`、`apps/tap-ai-frontend/src/features/graph/components/graph.css`（`sigma` 随之从 `package.json` 移除）
- Test: `apps/tap-ai-frontend/src/widgets/tap/workspace/LibraryWorkspace.durableGraph.test.tsx`（整体重写）、`apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.interactions.test.tsx:45-55`（mock 改为 `useGraphProject/useGraphOverview/useGraphSearch/useGraphNode/useGraphHighlight`）

**Interfaces:**
- Consumes：Task 1 的 hooks 与 `GraphVersionConflictError`，Task 2 的 `useGraphLayout`、`communityColor`。
- Produces（`toOverviewData.ts`）：`toOverviewData(graph: GraphSubgraph, communities: GraphCommunity[], positions: Map<string, {x,y}>, otherLabel: string): { nodes: GraphNode[]; edges: GraphEdge[]; communityOrder: { communityId: string; label: string; color: string; size: number }[] }`（社区按 `size` 降序；`communityId` 不在 `communities` 中的节点归入 `otherLabel`）。
- Produces（`CommunityList` props）：`{ communities: { communityId; label; color; size }[]; selected: ReadonlySet<string>; onToggle(id): void; onSelectAll(all: boolean): void; footer: { extracting: number; partial: number }; copy: WorkspaceCopy["library"] }`，复用 `KnowledgeGraph.tsx:261-330` 的复选框结构。
- Produces（`GraphOverview` props）：`{ projectId: string; locale: "en" | "zh"; copy: WorkspaceCopy; query: string; sourceRevisionIds: string[]; highlight?: GraphHighlightState | null（Task 5）; onClearHighlight?(): void; onAskAboutNode?(label: string, sourceIds: string[]): void; onOpenSource?(sourceId: string): void }`。内部状态：`selectedCommunities`、`nodeLimit`（初始 150）、`selectedNodeId`；`query` 非空时改用 `useGraphSearch`，清空恢复总览；`truncated === true` 时渲染按钮 `copy.library.loadMore`。
- Produces（`KnowledgeGraph` 新 props）：`{ copy; nodes: GraphNode[]; edges: GraphEdge[]; communities: {communityId,label,color,size}[]; activeCommunities: ReadonlySet<string>; onToggleCommunity; onSelectAllCommunities; statusFooter: ReactNode; searchQuery: string; selectedNodeId: string | null; onSelectNode(id | null): void; highlight?: { edgeIds: ReadonlySet<string>; nodeIds: ReadonlySet<string> } | null; detailPanel: ReactNode; caption: string; onLoadMore?: () => void; loadMoreLabel?: string }`；`EDGE_LABEL_ZOOM = 1.25`；删除 `sources`、`publishedData`、`onViewSource`、`publishedCaption` props 与 `kindLabels/communityLabels` 固定映射。
- Produces（copy 新键，EN/中文）：`library.overviewCaption`（"Project knowledge overview · nodes and relationships come from the service." / "项目知识总览 · 节点与关系来自服务。"）、`loadMore`（"Load more" / "加载更多"）、`extractingSources(n)`（"{n} sources still extracting" / "{n} 个来源仍在抽取"）、`partialSources(n)`（"{n} partially failed" / "{n} 个部分失败"）、`graphEmpty`（"No knowledge graph yet." / "暂无知识图谱。"）、`graphEmptyHint`（"The graph is built automatically after sources are published." / "来源发布后会自动建图。"）、`graphMerging`（"The graph is being rebuilt…" / "图谱正在重建…"）、`otherCommunity`（"Other" / "其他"）、`nodeTypes: Record<"ENTITY"|"CONCEPT"|"REQUIREMENT"|"SYSTEM"|"ACTOR"|"PROCESS", string>`；删除 `sourceCommunity`、`newBusinessCommunity`、`servicingCommunity`、`claimsCommunity`、`codebaseCommunity`、`applicationCommunity`、`underwritingCommunity`、`partiesCommunity`、`testingCommunity`、`documentNode`、`conceptNode`、`entityNode`、`visibleDocuments`。
- `LibraryWorkspace`：删除 `ProjectKnowledgeGraph`；图谱面板直接渲染 `<GraphOverview projectId={graphProjectId} query={query} sourceRevisionIds={publishedRevisionIdsOf(visibleSources)} .../>`；新增 props `publishedSources?: readonly { sourceId: string; revisionId: string }[]`、`onAskAboutNode?`、`onOpenSource?`，由 `TapperWorkspace.tsx:2385-2402` 的 `ProjectLibraryWorkspace` 透传（`publishedSourcesQuery.data.items`）。

- [ ] **Step 1: 写失败的测试（重写 `LibraryWorkspace.durableGraph.test.tsx`，mock Task 1 的 hooks）**

```tsx
it("renders communities from the project graph and colors nodes by community", ...)  // project 夹具 2 社区（"Underwriting" 5、"Claims" 3）→ 复选框 "Underwriting · 5 nodes"；无 "Graph source" 下拉；无 "Published source graph" 文案；节点 role=button 名含 "Underwriting"
it("shows an empty state for a project without communities", ...)                    // nodeCount 0 → 文案 "No knowledge graph yet."，页面无 role=group 的 SVG
it("loads more nodes when the overview is truncated", ...)                             // truncated:true → 点击 "Load more" 后 useGraphOverview 最后一次调用 nodeLimit === 300
it("passes the filtered published sources to the overview", ...)                       // 状态筛选 "ready" 后 useGraphOverview 的 sourceRevisionIds 只含就绪来源的修订
it("shows extraction footer counts", ...)                                              // fragments 2 EXTRACTING、1 PARTIAL → 文案 "2 sources still extracting" 与 "1 partially failed"
it("renders Chinese overview copy", ...)                                               // locale="zh" → "项目知识总览" 与 "加载更多"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `corepack pnpm exec vitest run src/widgets/tap/workspace/LibraryWorkspace.durableGraph.test.tsx`
Expected: FAIL（mock 的 `useGraphProject` 不存在、仍渲染 "Graph source"）

- [ ] **Step 3: 重写 `graphLayout.ts`、`KnowledgeGraph.tsx`，新建 `CommunityList`、`GraphOverview`、`toOverviewData`，接线 `LibraryWorkspace`，删除旧文件与 copy 键，移除 `sigma`**

`KnowledgeGraph.tsx` 保留平移、缩放、全屏、搜索结果区、无障碍摘要（`visibleDocuments` 列表改为 `nodeTypes` 分组列表）；边标签 `showLabel = active || zoom >= EDGE_LABEL_ZOOM`；节点半径按 Global Constraints。

- [ ] **Step 4: 运行相关测试、架构检查与编译**

Run: `corepack pnpm exec vitest run src/widgets/tap/workspace src/features/graph && corepack pnpm run architecture && corepack pnpm exec tsc -b`
Expected: PASS；dependency-cruiser 无 `no-feature-to-feature` 违规

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-frontend/package.json apps/tap-ai-frontend/pnpm-lock.yaml pnpm-lock.yaml apps/tap-ai-frontend/src
git commit -m "feat: show the project graph overview with real communities"
```

---

### Task 4: 节点详情面板、就此提问与打开原件

**Files:**
- Create: `apps/tap-ai-frontend/src/features/graph/components/NodeDetailPanel.tsx`
- Modify: `apps/tap-ai-frontend/src/features/graph/components/GraphOverview.tsx`（`detailPanel` 渲染 `NodeDetailPanel`）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.tsx:702-770,2383-2402`（实现 `onAskAboutNode` 与 `onOpenSource`）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/copy.ts`
- Test: `apps/tap-ai-frontend/src/features/graph/components/NodeDetailPanel.test.tsx`（新建）、`apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.interactions.test.tsx`（追加）

**Interfaces:**
- Consumes：Task 1 的 `useGraphNode`、`GraphNodeDetail`。
- Produces（`NodeDetailPanel` props）：`{ projectId: string; graphVersion: string; nodeId: string; color: string; communityLabel: string; copy: WorkspaceCopy; locale: "en" | "zh"; onClose(): void; onAskAboutNode?(label: string, sourceIds: string[]): void; onOpenSource?(sourceId: string): void; onSelectNode(nodeId: string): void }`。常量 `RELATION_GROUP_PREVIEW = 10`。关系按 `relationType` 分组，组标题 `relationLabel` 众数 + 类型码；每项按钮点击 `onSelectNode(otherNode.nodeId)`。证据按 `sources[]` 分组，每条 `snippet` 后一个"打开原件"按钮 → `onOpenSource(sourceId)`。"就此提问"按钮 → `onAskAboutNode(node.label, sources.map(s => s.sourceId))`。
- Produces（copy 新键）：`library.aliases`（"Aliases" / "别名"）、`nodeSources`（"Sources" / "来源"）、`evidenceSnippets`（"Evidence" / "证据片段"）、`openOriginal`（"Open original" / "打开原件"）、`askAboutNode`（"Ask about this" / "就此提问"）、`showAllRelations(n)`（"Show all ({n})" / "显示全部 ({n})"）、`relationCount(n)`（"{n} relations" / "{n} 条关系"）、`nodeDetailsLoading`（"Loading node details…" / "正在加载节点详情…"）、`nodeDetailsError`（"Node details are unavailable. Try again." / "节点详情暂时无法加载，请重试。"）。
- `TapperWorkspace`：`onAskAboutNode(label, sourceIds)` = `setMessageDraft(label)`、把 `sourceIds` 中存在于 `publishedSourcesQuery.data.items` 的 id 合并进 `activeConversation.selectedSourceIds`（复用 `toggleSelection` 只做新增）、`selectModule("tapper")`、`setSourcesCollapsed(false)`；`onOpenSource(sourceId)` = `ProjectLibraryWorkspace` 现有 `onInspectSource(sourceId, trigger)` 路径（`TapperWorkspace.tsx:754-757`）。

- [ ] **Step 1: 写失败的测试**

```tsx
// NodeDetailPanel.test.tsx（mock useGraphNode 返回夹具）
it("shows type, aliases, sources and grouped relations", ...)        // 标题 "Health disclosure"、"CONCEPT" 的本地化名、别名 "HD"、来源 "policy-a.md"
it("groups forty relations by type and limits each group", ...)       // 20 REQUIRES + 20 APPLIES_TO → 两个组标题；初始 listitem 数 20；点击 "Show all (20)" 后 30
it("asks about the node with its sources", ...)                       // 点击 "Ask about this" → onAskAboutNode("Health disclosure", ["src_a","src_b"])
it("opens the original source from an evidence snippet", ...)         // 点击第一条片段的 "Open original" → onOpenSource("src_a")
// TapperWorkspace.interactions.test.tsx
it("fills the composer and source chips when asking about a graph node", ...)  // 触发 onAskAboutNode 后 textbox "Message Tapper" 值为 label，已选上下文含两个来源 chip，激活模块为 Tapper
```

- [ ] **Step 2: 运行测试确认失败**

Run: `corepack pnpm exec vitest run src/features/graph/components/NodeDetailPanel.test.tsx`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `NodeDetailPanel`，接入 `GraphOverview` 与 `TapperWorkspace`，补 copy**

- [ ] **Step 4: 运行测试确认通过**

Run: `corepack pnpm exec vitest run src/features/graph src/widgets/tap/TapperWorkspace.interactions.test.tsx -t "graph node|relations|node"`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-frontend/src
git commit -m "feat: add graph node detail panel with ask and open original"
```

---

### Task 5: 高亮态与历史状态传递

**Files:**
- Create: `apps/tap-ai-frontend/src/features/graph/model/highlight.ts`
- Modify: `apps/tap-ai-frontend/src/features/graph/components/GraphOverview.tsx`、`apps/tap-ai-frontend/src/widgets/tap/workspace/KnowledgeGraph.tsx`（`highlight` prop：路径节点 `data-highlighted`、其余 `data-dimmed`，并自动适配视口）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.tsx:930-932`（初始模块：`readGraphHighlight() !== null` 时为 `"library"`）、`:2383-2402`（把 `graphHighlight` 状态传给 Library）
- Test: `apps/tap-ai-frontend/src/features/graph/model/highlight.test.ts`（新建）、`apps/tap-ai-frontend/src/widgets/tap/workspace/LibraryWorkspace.durableGraph.test.tsx`（追加）

**Interfaces:**
- Produces（`highlight.ts`）：`interface GraphHighlightState { edgeIds: string[]; graphVersion: string; turnId: string | null }`；`GRAPH_HIGHLIGHT_PATH = "/library/graph"`；`pushGraphHighlight(state: GraphHighlightState): void`（`window.history.pushState({ graphHighlight: state }, "", GRAPH_HIGHLIGHT_PATH)`）；`readGraphHighlight(): GraphHighlightState | null`（校验 `edgeIds` 为非空字符串数组、长度 ≤ 20）；`clearGraphHighlight(): void`（`replaceState(null, "", "/")`）；`useGraphHighlightState(): [GraphHighlightState | null, clear: () => void]`（监听 `popstate`）。
- Produces（`GraphOverview`）：`highlight` 非空时调用 `useGraphHighlight(projectId, edgeIds)`；`missingEdgeIds = edgeIds − 响应边 id`；非空时在画布上方渲染 `role="status"` 的 `copy.library.versionUpdated`；高亮节点集合 = 响应边两端；画布旁 `<section role="region" aria-label={copy.library.highlightedPath}>` 以 `<ol>` 列出每条边 "A —relationLabel→ B"；按钮 `copy.library.clearHighlight` 调用 `onClearHighlight`。
- Produces（`KnowledgeGraph` 适配视口）：`fitToNodes(nodeIds)`：取高亮节点包围盒，`zoom = clamp(min(GRAPH_WIDTH / (w + 240), GRAPH_HEIGHT / (h + 240)), MIN_ZOOM, MAX_ZOOM)`，`pan` 使包围盒中心落在画布中心；`highlight` 变化时执行一次。
- Produces（copy 新键）：`library.highlightedPath`（"Highlighted path" / "高亮路径"）、`clearHighlight`（"Back to overview" / "返回总览"）、`versionUpdated`（"The graph has been updated; some relations are no longer available." / "图谱版本已更新，部分关系不再可用。"）、`highlightCaption`（"Relations cited by the answer" / "回答引用的关系"）。

- [ ] **Step 1: 写失败的测试**

```ts
// highlight.test.ts
it("round-trips a highlight through history state and rejects malformed state", ...)  // push 后 read 相等；history.state = {graphHighlight:{edgeIds:"x"}} → null
// LibraryWorkspace.durableGraph.test.tsx
it("highlights cited edges, dims the rest and lists the path as text", ...)            // highlight 2 条边 → 两个节点 data-highlighted="true"，其余 data-dimmed="true"；region "Highlighted path" 含 "Underwriting review —requires→ Health disclosure"
it("renders a version-updated notice when highlight omits requested edges", ...)       // 请求 ["e1","e2"]，响应只含 e1 → status 文案 "The graph has been updated…"，e1 仍高亮
it("fits the viewport to the highlighted nodes", ...)                                  // 高亮后 "Zoom level" status 不等于 "100%"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `corepack pnpm exec vitest run src/features/graph/model/highlight.test.ts src/widgets/tap/workspace/LibraryWorkspace.durableGraph.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现 `highlight.ts`、`GraphOverview` 高亮分支、`KnowledgeGraph` 的 `highlight` 与 `fitToNodes`，接线 `TapperWorkspace`**

- [ ] **Step 4: 运行测试确认通过**

Run: `corepack pnpm exec vitest run src/features/graph src/widgets/tap/workspace`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-frontend/src
git commit -m "feat: highlight cited graph paths from history state"
```

---

### Task 6: 文档详情的片段图状态与重试

**Files:**
- Modify: `apps/tap-ai-frontend/src/features/knowledge/api/client.ts`（`KnowledgeClient` 新增 `graphProject(signal?)`、`retryGraphFragment(revisionId, idempotencyKey)`）、`apps/tap-ai-frontend/src/features/knowledge/api/queries.tsx`（`useGraphProjectQuery(projectId)` 复用键 `["graph", projectId, "project"]`；`useRetryGraphFragmentMutation(projectId)`）、`apps/tap-ai-frontend/src/features/knowledge/testing/fakeKnowledgeClient.ts`
- Create: `apps/tap-ai-frontend/src/features/knowledge/components/GraphFragmentStatus.tsx`
- Modify: `apps/tap-ai-frontend/src/features/knowledge/components/DocumentDetail.tsx:124-142`（处理阶段之后插入 `<GraphFragmentStatus revisionId={detailQuery.data.revisionId} />`）、`apps/tap-ai-frontend/src/features/knowledge/copy.ts`
- Test: `apps/tap-ai-frontend/src/features/knowledge/components/GraphFragmentStatus.test.tsx`（新建）

**Interfaces:**
- Produces（`GraphFragmentStatus` props）：`{ revisionId: string; locale?: "en" | "zh" }`；状态映射：`fragments` 中无该修订 → `ready`；`EXTRACTING` → `extracting`；`PARTIAL` → `partial`（显示 `failedBatches/totalBatches` 与重试按钮）；`FAILED` → `failed`（显示重试按钮）；重试成功后按钮替换为 `retryQueued` 文案。
- Produces（copy 键，`COPY` 与 `CITATION_EN` 同级新增 `GRAPH_FRAGMENT_COPY = { zh: {...}, en: {...} }`）：`title`（"图谱抽取" / "Graph extraction"）、`extracting`（"抽取中" / "Extracting"）、`ready`（"就绪" / "Ready"）、`partial(f, t)`（"部分失败（{f}/{t} 批）" / "Partially failed ({f}/{t} batches)"）、`failed`（"失败" / "Failed"）、`retry`（"重试失败批次" / "Retry failed batches"）、`retryQueued`（"已重新排队" / "Retry queued"）。

- [ ] **Step 1: 写失败的测试**

```tsx
it("shows ready when the revision is not listed as a fragment", ...)
it("shows partial failure with counts and retries via the fragment route", ...)  // PARTIAL 2/5 → 文案 "部分失败（2/5 批）"；点击重试 → client.retryGraphFragment("rev_x", 任意 key) 被调用，随后显示 "已重新排队"
it("shows extracting without a retry button", ...)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `corepack pnpm exec vitest run src/features/knowledge/components/GraphFragmentStatus.test.tsx`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现客户端方法、hooks、组件，接入 `DocumentDetail`**

- [ ] **Step 4: 运行测试确认通过**

Run: `corepack pnpm exec vitest run src/features/knowledge`
Expected: PASS（`fakeKnowledgeClient` 已补两个方法，`client.test.ts` 覆盖请求路径 `/knowledge/graph/fragments/{revisionId}/retry`）

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-frontend/src/features/knowledge
git commit -m "feat: show graph fragment status with retry in document detail"
```

---

### Task 7: `graph.context_ready` 归约、回答摘要行与无关系提示

**Files:**
- Modify: `apps/tap-ai-frontend/src/features/conversations/model/stream.ts:6-35,111-172`
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/model.ts:40-62`（`AssistantTurn.graphContext?: GraphContextSummary | null`）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.tsx:163-262`（`AnswerActivity`）、`:521-647`（`AssistantResponse`）、`:1290-1310`（把 `latestTurnState(...).graphContext` 写入 turn）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/copy.ts`（`chat` 段）
- Test: `apps/tap-ai-frontend/src/features/conversations/model/stream.test.ts`、`apps/tap-ai-frontend/src/widgets/tap/AnswerActivity.test.tsx`

**Interfaces:**
- Produces（`stream.ts`）：`export interface GraphContextSummary { status: "APPLIED" | "NOT_READY" | "STALE" | "FAILED" | "EMPTY"; seedCount: number; paths: string[][]; relationCount: number }`；`StreamTurnState.graphContext: GraphContextSummary | null`（`emptyTurn()` 为 `null`）；`reduceStreamEvent` 对 `event.type === "graph.context_ready"` 校验 `status` 在枚举内、`seedCount`/`relationCount` 为非负整数、`paths` 为字符串数组的数组（最多取 3 条），不合法则忽略；`export function graphContextFromEvents(events: readonly { eventType: string; payload: Record<string, unknown> }[]): GraphContextSummary | null`（取最后一条 `graph.context_ready`，同样校验）。
- Produces（`AnswerActivity` 新 props）：`graphContext: GraphContextSummary | null`、`chunkCitationCount: number`（claims 引用的 `kind !== "edge"` 去重数）、`edgeCitationCount: number`；`<summary>` 改为 `copy.chat.answerSummary(sourceCount, chunkCitationCount, graphContext?.relationCount ?? edgeCitationCount)`；展开列表追加 `copy.chat.seedEntities(seedCount)` 与每条路径 `labels.join(" → ")`；原有行保留。
- Produces（`AssistantResponse`）：`graphContext.status === "EMPTY" && graphContext.querySeedCount >= 2` 时（`seedCount` 保留兼容）在 `GroundedAnswer` 之上渲染 `<Alert type="info" role="status">copy.chat.noRelationEvidence</Alert>`。
- Produces（copy 新键）：`chat.answerSummary(n, m, k)`（"Searched {n} sources · {m} passages · {k} relations" / "搜索 {n} 个来源 · {m} 段原文 · {k} 条关系"）、`seedEntities(n)`（"{n} seed entities" / "{n} 个种子实体"）、`relationPaths`（"Relation paths" / "关系路径"）、`noRelationEvidence`（"No direct relation evidence was found; the answer below is grounded in source passages." / "未找到直接关系证据，以下为资料原文依据"）。

- [ ] **Step 1: 写失败的测试**

```ts
// stream.test.ts
it("keeps the latest valid graph context and ignores malformed payloads", ...)  // 两条 graph.context_ready：第一条合法 EMPTY/seedCount 2；第二条 status "???" → graphContext 仍为第一条
// AnswerActivity.test.tsx
it("renders the Chinese answer summary line", ...)      // locale zh、sourceCount 2、chunk 3、relationCount 1 → summary 文本 "搜索 2 个来源 · 3 段原文 · 1 条关系"；展开后含 "2 个种子实体" 与 "核保流程 → 健康告知"
it("falls back to edge citation count without a graph event", ...)  // graphContext null、edgeCitationCount 2 → "… · 2 relations"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `corepack pnpm exec vitest run src/features/conversations/model/stream.test.ts src/widgets/tap/AnswerActivity.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现归约、`graphContextFromEvents`、`AnswerActivity` 与提示，补 copy**

- [ ] **Step 4: 运行测试确认通过**

Run: `corepack pnpm exec vitest run src/features/conversations src/widgets/tap/AnswerActivity.test.tsx`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-frontend/src
git commit -m "feat: summarize graph context in the answer activity line"
```

---

### Task 8: 边引用芯片、悬停卡片、证据面板与迷你路径图

**Files:**
- Create: `apps/tap-ai-frontend/src/features/knowledge/components/EdgeCitationChip.tsx`、`RelationHoverCard.tsx`、`EvidencePanel.tsx`、`MiniPathGraph.tsx`、`apps/tap-ai-frontend/src/features/knowledge/model/edgeCitation.ts`
- Modify: `apps/tap-ai-frontend/src/features/knowledge/components/GroundedAnswer.tsx:37-70,109-196,221-257`
- Modify: `apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.tsx:2252-2268,2311-2318`（`activeCitation.citation.kind === "edge"` 时渲染 `EvidencePanel`，`onViewInLibrary` 调用 Task 5 的 `pushGraphHighlight` 并 `selectModule("library")`）
- Modify: `apps/tap-ai-frontend/src/features/knowledge/copy.ts`、`apps/tap-ai-frontend/src/app/styles.css:347-370`（`.tapper-claim-citations .ant-btn[data-kind="edge"]` 配色区分）
- Test: `apps/tap-ai-frontend/src/features/knowledge/components/EdgeCitationChip.test.tsx`、`EvidencePanel.test.tsx`（新建）、`apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.interactions.test.tsx`（追加）

**Interfaces:**
- Produces（`model/edgeCitation.ts`）：`type RetrievalCitation = RetrievalAnswerResponse["citations"][number]`；`type EdgeCitation = Extract<RetrievalCitation, { kind: "edge" }>`；`isEdgeCitation(c: RetrievalCitation): c is EdgeCitation`（`kind === "edge"` 且 `edgeId`、`subject.label`、`object.label`、`relationType` 为非空字符串）；`relationText(c: EdgeCitation): string` = `` `${subject.label} —${relationLabel || relationType}→ ${object.label}` ``。
- Produces（`GroundedAnswer`）：`ValidAnswerGraph` 增加 `edgeNumberById: ReadonlyMap<string, number>`；编号时 chunk 与 edge 各自从 1 计数（`shown-order` 与 `source-order` 都如此）；`CitedClaim` 对 edge 引用渲染 `<EdgeCitationChip>`；`ANSWER_COPY` 新增 `edgeCitation(n)`（"Open relation citation R{n}" / "打开关系引用 R{n}"）。
- Produces（`EdgeCitationChip` props）：`{ number: number; citation: EdgeCitation; locale; onOpen(citationId: string, trigger: HTMLElement): void }`，渲染 antd `Button type="text" size="small" data-kind="edge"`，文本 `[R{n}]`，`aria-label` 为 `edgeCitation(n)`，外层 antd `Popover trigger={["hover","focus"]}` 内容为 `RelationHoverCard`。
- Produces（`RelationHoverCard` props）：`{ citation: EdgeCitation; locale }`：第一行 `relationText`，第二行 `evidence.snippet`（≤300 字，超出省略）。
- Produces（`EvidencePanel` props）：`{ active: { citation: EdgeCitation; id: string }; turnEdgeCitations: EdgeCitation[]; locale; onClose(): void; returnFocusTo?: HTMLElement | null; onViewInLibrary(edgeIds: string[], graphVersion: string): void }`；结构照 `CitationViewer.tsx:158-260`：`section.tapper-panel` + 标题 `evidenceTitle`（`id="evidence-heading"`，挂载时聚焦）、`<MiniPathGraph>`、`<ol aria-label={pathAsText}>` 文字路径、`blockquote` 的 `evidence.snippet`、按钮 `viewInLibrary`（传本回答全部边 id 与被点击引用的 `graphVersion`）。
- Produces（`MiniPathGraph` props）：`{ edges: EdgeCitation[]; activeEdgeId: string; locale }`：节点去重后均匀布在 320×200 的 SVG 椭圆上，边为直线加箭头，活动边 `data-active="true"`；`role="img"` 且 `aria-label` 为所有边的 `relationText` 以"；"连接。
- Produces（`features/knowledge/copy.ts` 新键，`COPY` 与 `CITATION_EN` 各加）：`evidenceTitle`（"关系依据" / "Relation evidence"）、`pathGraph`（"关系路径图" / "Relation path"）、`pathAsText`（"路径文字说明" / "Path as text"）、`supportingPassage`（"支撑原文" / "Supporting passage"）、`viewInLibrary`（"在 Library 中查看" / "View in Library"）、`closeEvidence`（"关闭关系依据" / "Close relation evidence"）。
- `TapperWorkspace`：`onOpenCitation` 不变（`activeCitation` 可容纳 edge 引用）；渲染分支 `isEdgeCitation(activeCitation.citation) ? <EvidencePanel .../> : <CitationViewer .../>`；`turnEdgeCitations` 取该 turn `response.citations.filter(isEdgeCitation)`；`onViewInLibrary` = `pushGraphHighlight({ edgeIds, graphVersion, turnId })` → `selectModule("library")`。

- [ ] **Step 1: 写失败的测试**

```tsx
// EdgeCitationChip.test.tsx（经 GroundedAnswer 渲染混合引用的回答）
it("numbers chunk and edge citations independently", ...)       // citations [chunk c1, edge e1, chunk c2] → 芯片文本依次 "[1]" "[R1]" "[2]"
it("shows the relation and snippet on hover", ...)              // hover "[R1]" → tooltip 含 "Underwriting review —requires→ Health disclosure" 与片段
it("still renders chips when the edge graph version is stale", ...)  // graphVersion "v1" 与当前无关 → 芯片存在且可点击
// EvidencePanel.test.tsx
it("draws every edge citation of the turn and lists the path as text", ...)  // 2 条边 → img 名含两段 relationText；ol 两项；活动边 data-active
it("hands the turn's edge ids to the library", ...)             // 点击 "View in Library" → onViewInLibrary(["e1","e2"], "v2")
// TapperWorkspace.interactions.test.tsx
it("keeps citation chips in reading order and returns focus after closing the evidence panel", ...)  // Tab 顺序 [1]→[R1]；Enter 打开后 document.activeElement 为 heading "Relation evidence"；关闭后焦点回到 [R1]
it("opens Library in highlight mode from an edge citation", ...)  // 点击 "View in Library" → history.state.graphHighlight.edgeIds 为 ["e1"]，Library 标签 "Knowledge Graph" 选中，region "Highlighted path" 可见
```

- [ ] **Step 2: 运行测试确认失败**

Run: `corepack pnpm exec vitest run src/features/knowledge/components/EdgeCitationChip.test.tsx src/features/knowledge/components/EvidencePanel.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现模型、四个组件、`GroundedAnswer` 编号与接线，补 copy 与样式**

- [ ] **Step 4: 运行测试、架构检查与编译**

Run: `corepack pnpm exec vitest run src/features/knowledge src/widgets/tap && corepack pnpm run architecture && corepack pnpm exec tsc -b`
Expected: PASS；`features/knowledge` 未导入 `features/graph`

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-frontend/src
git commit -m "feat: render edge citations with hover cards and an evidence panel"
```

---

### Task 9: 后端退役 `GET /snapshots` 与 E2E 允许列表

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/routes/knowledge_graph.py:1-56`（删除 `list_active_snapshots` 与两个导入）、`apps/tap-ai-backend/src/tap/contracts/http.py:1196-1206`（删除 `GraphSnapshotView`、`GraphSnapshotPage`）
- Modify: `apps/tap-ai-backend/tests/contract/test_graph_http.py:20-53`
- Modify: `apps/tap-ai-frontend/src/shared/testing/e2eRequestFailures.ts:18-24,108-136`、`apps/tap-ai-frontend/src/shared/testing/e2eRequestFailures.test.ts:44,279`
- Modify: `apps/tap-ai-frontend/tests/e2e/persistence.spec.ts:97-105`、`apps/tap-ai-frontend/tests/e2e/tapper.spec.ts:559-575`
- Modify: `docs/architecture.md:61-64`（图谱数据流：片段抽取 → 项目合并 → 总览/节点/高亮读接口；删除快照读路由的描述）

**Interfaces:**
- Produces（后端）：OpenAPI 不再含 `/knowledge/graph/snapshots`；`GraphService.active_snapshot` 若 `rg active_snapshot apps/tap-ai-backend/src` 只剩定义则一并删除，否则保留。
- Produces（`e2eRequestFailures.ts`）：`ClosedPathLabel` 的 `"graph-snapshots"` 改为 `"graph-read"`；`exactGraphRead` = `GET` 且路径为 `${projectPath}/knowledge/graph/project`（无查询串）或 `${projectPath}/knowledge/graph/overview`（查询键只允许 `sourceRevisionId`、`communityId`、`nodeLimit`，修订 id 满足 `^rev_[0-9a-f]{64}$`，`nodeLimit` 为 1–500 的整数）或 `${projectPath}/knowledge/graph/nodes/{node_id}`（`node_id` 满足 `^[A-Za-z0-9_:-]{1,128}$`，查询键只允许 `graphVersion`）。
- Produces（E2E 辅助，`tests/e2e/publicationFixture.ts` 新增）：`waitForProjectGraph(page, root, revisionIds: string[], timeout = 60_000): Promise<string>`：轮询 `GET ${root}/knowledge/graph/project` 直到 `status === "READY"` 且 `fragments` 不含任一 `revisionIds`，返回 `graphVersion`；`persistence.spec.ts` 与 `tapper.spec.ts` 用它替换快照轮询。

- [ ] **Step 1: 改契约测试**

```python
def test_graph_routes_are_project_scoped_and_bounded():
    assert "/api/v1/projects/{project_id}/knowledge/graph/snapshots" not in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/project" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/overview" in paths
    assert "/api/v1/projects/{project_id}/knowledge/graph/highlight" in paths
    ...  # 其余断言保留

def test_graph_outage_is_503_not_an_empty_graph():   # 改请求 GET .../knowledge/graph/project
def test_graph_project_path_cannot_widen_the_trusted_scope():   # 改请求 GET /api/v1/projects/other-project/knowledge/graph/project → 403
def test_snapshot_contracts_are_retired():
    schemas = client.app.openapi()["components"]["schemas"]
    assert "GraphSnapshotPage" not in schemas and "GraphSnapshotView" not in schemas
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/contract/test_graph_http.py -v`
Expected: `test_snapshot_contracts_are_retired` 与路由断言 FAIL

- [ ] **Step 3: 删除路由与契约，重新生成契约**

Run（仓库根目录）: `make contracts && corepack pnpm --dir apps/tap-ai-frontend run contracts:check`
Expected: `schema.ts` 不再含 `GraphSnapshotPage`；`grep -rn "graph/snapshots\|GraphSnapshotPage" apps contracts --include=*.ts --include=*.tsx --include=*.py --include=*.json` 只剩本任务待改的前端测试与 E2E 文件

- [ ] **Step 4: 改 `e2eRequestFailures` 与其测试、两份 E2E 规格、`architecture.md`**

```ts
// e2eRequestFailures.test.ts：允许项改为 /knowledge/graph/project、/knowledge/graph/overview?sourceRevisionId=rev_<64hex>&nodeLimit=150；
// 拒绝项改为 /knowledge/graph/overview?nodeLimit=secret → "GET outside-allowlist net::ERR_ABORTED"
```

- [ ] **Step 5: 运行后端与前端测试**

Run: `uv run pytest tests/contract/test_graph_http.py -v && (cd ../tap-ai-frontend && corepack pnpm exec vitest run src/shared/testing && corepack pnpm exec tsc -b)`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add apps/tap-ai-backend apps/tap-ai-frontend contracts docs/architecture.md
git commit -m "refactor: retire the graph snapshots route"
```

---

### Task 10: `ui:capture` 四张截图与隔离夹具

**Files:**
- Create: `apps/tap-ai-frontend/tests/e2e/graphCaptureFixture.ts`
- Modify: `apps/tap-ai-frontend/tests/e2e/ui-capture.spec.ts:48-101,146-156`
- Test: `apps/tap-ai-frontend/scripts/check-capture.test.mjs`（现有，列表断言不变）

**Interfaces:**
- Produces（`graphCaptureFixture.ts`，纯数据，不含客户企业名称，文件名用 `underwriting-guide.md`、`claims-handbook.md`）：`CAPTURE_GRAPH_PROJECT`（`graphVersion: "capture-v1"`，3 个社区 "Underwriting" 12、"Claims" 9、"Servicing" 6，`fragments: [{ status: "EXTRACTING" }]` 一条）、`CAPTURE_GRAPH_OVERVIEW`（27 节点、34 边，`truncated: false`，度数 1–9）、`CAPTURE_GRAPH_NODE`（"Health disclosure"：2 别名、2 来源各 2 片段、3 组关系共 8 条）、`CAPTURE_GRAPH_HIGHLIGHT`（2 条边 + 1 跳上下文 3 节点）、`CAPTURE_RELATION_CONVERSATION`（`summary`、`detail` 一个已完成 turn、`events` 含 `context.assembled`、`graph.context_ready{status:"APPLIED", seedCount:2, paths:[["Underwriting review","Health disclosure"]], relationCount:1}`、`turn.completed` 的回答：两段 claim，第一段引用 chunk `c1` 与 edge `e1`）。
- `ui-capture.spec.ts`：`beforeEach` 的路由表增加 `GET .../knowledge/graph/project`、`GET .../knowledge/graph/overview`、`GET .../knowledge/graph/nodes/{id}`，并允许 `POST .../knowledge/graph/highlight`（`expect(method).toBe("GET")` 改为对 `highlight` 路径放行 `POST`）；`knowledge/sources` 返回两条就绪来源；`published-sources` 返回对应两条。
- 新增测试 `captures the graph overview, node detail, edge citations and highlight`：① Library → "Knowledge Graph" → 等待复选框 "Underwriting · 12 nodes" → `capture("10-graph-overview")`；② 点击节点按钮 /Health disclosure/ → 等待 region "Node details" 含 "Aliases" → `capture("11-graph-node-detail")`；③ `page.unroute` 后按 Insights 测试的方式重路由（`ui-capture.spec.ts:161-277`）加载 `CAPTURE_RELATION_CONVERSATION`，`goto("/?projectId=project-capture")`，点击历史 "Relation question" → 等待按钮 "Open relation citation R1" → hover → `capture("12-answer-edge-citations")`；④ 点击 R1 → 等待 heading "Relation evidence" → 点击 "View in Library" → 等待 region "Highlighted path" → `capture("13-graph-highlight")`；四个哈希两两不等。

- [ ] **Step 1: 写夹具与新测试**

- [ ] **Step 2: 运行捕获**

Run: `corepack pnpm run ui:capture`
Expected: 新测试 PASS，`test-results/ui-capture/` 出现 `10-…` 到 `13-…` 四个 2560×1440 PNG；现有 `05-knowledge-graph` 现在显示总览而不是来源下拉（人工对比 PR 描述中的前后截图）

- [ ] **Step 3: 运行捕获清单检查**

Run: `node --test scripts/check-capture.test.mjs`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add apps/tap-ai-frontend/tests/e2e/graphCaptureFixture.ts apps/tap-ai-frontend/tests/e2e/ui-capture.spec.ts
git commit -m "test: capture graph overview, node detail, edge citations and highlight"
```

---

### Task 11: E2E 旅程、文档与全量检查

**Files:**
- Modify: `apps/tap-ai-frontend/tests/e2e/knowledge-graph.spec.ts`（整体重写）
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 2、3 的进度：PR 4 前端完成，待 PR 5 门禁）、`docs/superpowers/plans/2026-10-01-product-roadmap.md:53-54`（"知识图谱脉络分析"行标注 PR 4 合并）、`docs/architecture.md:86-87`（已知差距表：删除"每次查询把整个快照载入内存"与"无关系边引用与路径高亮"，改为"关系 golden set 门禁待 PR 5"）
- Modify（仅当 Step 2 不成立）: `apps/tap-ai-backend/src/tap/modules/ai/adapters/fake_model_gateway.py`（PR 3 的 fake 回答器所在文件以 `rg "R1" apps/tap-ai-backend/src` 为准）

**Interfaces:**
- 旅程（沿用 `knowledge-graph.spec.ts:6-49` 的上传与 `preparePublishedFixture` 写法，fixture 句子与 PR 1 规则式抽取的英文触发词一致）：
  1. 上传 `graph-a-{ts}.md`："# Underwriting guide\n\nUnderwriting review requires health disclosure." 与 `graph-b-{ts}.md`："# Claims handbook\n\nHealth disclosure is validated by the claims assessor."，等待两者 ready，`preparePublishedFixture([revA, revB])`，`waitForProjectGraph(page, root, [revA, revB])`。
  2. `GET /knowledge/graph/overview?sourceRevisionId=revA&sourceRevisionId=revB` 中存在 label 为 "health disclosure"（大小写不敏感）的节点，且 `GET /nodes/{id}` 的 `sources` 长度为 2（跨文档节点）。
  3. UI：Library → Graph 标签 → 等待 `GET …/graph/project` 200 → `figure` 内按钮 /health disclosure/i 可见。
  4. New chat → 在知识来源面板勾选两份来源 → 发送 "What is the relationship between underwriting review and health disclosure?" → 等待按钮 /Open relation citation R1|打开关系引用 R1/ → 点击 → heading /Relation evidence|关系依据/ → 点击 /View in Library|在 Library 中查看/ → 标签 "Knowledge Graph" `aria-selected=true` → region /Highlighted path|高亮路径/ 含 "underwriting review" 与 "health disclosure"。
  5. 清理两份来源并等待 404（照 `knowledge-graph.spec.ts:139-159`）。
- 删除旧规格对 `/graph/snapshots` 的 503 路由断言；图谱不可用的 503 断言改为拦截 `/knowledge/graph/project` 并期待 alert 文案 `nodeDetailsError` 以外的总览错误文案 `copy.library.graphUnavailable`（Task 3 已有键 "The knowledge graph is temporarily unavailable." / "知识图谱暂时无法加载。"，若 Task 3 未加则在此补）。

- [ ] **Step 1: 重写 `knowledge-graph.spec.ts`**

- [ ] **Step 2: 确认 fake 模型在关系上下文下产出 R 引用**

Run（仓库根目录）: `make demo-up && make demo-dev` 后在另一终端 `corepack pnpm --dir apps/tap-ai-frontend exec playwright test tests/e2e/knowledge-graph.spec.ts --config=playwright.config.ts`
Expected: 步骤 4 的 R1 按钮出现。若回答只有 `[1]`：在 fake 回答器中，当输入上下文含关系证据段落时让第一条 claim 的 `evidenceLabels` 包含 `"R1"` 且 claim 文本含两端 label（满足 `ports/answers.py` 校验），补一条后端单元测试 `test_fake_answer_cites_first_relation_when_present`，再重跑本步骤

- [ ] **Step 3: 更新三份文档；对客户企业名称做大小写不敏感全文检索，必须无输出**

- [ ] **Step 4: 全量检查**

Run（仓库根目录）: `make check && make test && git diff --check`
Expected: 全部通过（含 `contracts:check`、dependency-cruiser、`tsc -b`、Vitest、pytest）

- [ ] **Step 5: 隔离 E2E**

Run: `make demo-e2e`
Expected: `knowledge-graph.spec.ts`、`knowledge-conversation.spec.ts`、`persistence.spec.ts`、`tapper.spec.ts` 通过；其余 journey 不劣于当前 main（已知失败项记录在 PR 描述中）

- [ ] **Step 6: 提交并整理 PR 描述**

```bash
git add apps/tap-ai-frontend/tests/e2e docs
git commit -m "test: cover the graph overview to highlighted citation journey"
```

PR 描述列出：意图、受影响文档、`make check/test/demo-e2e` 与 `ui:capture` 结果、`05-knowledge-graph` 与新四张截图的前后对比、Step 2 是否改动了 fake 回答器、`GET /snapshots` 退役对外部调用方的影响（本仓库内无其他调用方）。
