# 图谱查询扩展设计

日期：2026-11-03。V1 子项目 4（能力 2），见 [V1 总纲](../plans/2026-09-29-v1-roadmap.md)。子项目 5（[图谱脉络分析](2026-11-03-graph-context-analysis-design.md)）复用本设计的缓存与反向索引。

## 现状

- `MysqlGraphStore._memory()` 每次查询把整个快照的节点、边、证据、推理来源全部读入并构造 `InMemoryGraphStore`。
- 查询能力：标签/规范键搜索（`*` 为全部）、有界邻居、有界最短路径。
- 前端 Library 只展示单个来源的“已发布来源图谱”；“领域总览”在 [前端清理](../plans/2026-09-30-tap-ai-frontend-demo-cleanup.md) 中因无真实数据被移除。

## 目标

1. 按快照缓存邻接索引，查询不再每次整快照载入。
2. 本地基准约 1 万节点、5 万边下，搜索、邻居（深度 2）、路径查询 p95 < 300ms。
3. 按项目已发布知识生成“领域总览”。

## 非目标

- 图查询语言、向量化节点搜索、跨进程共享缓存（Redis）。
- 快照增量更新：快照不可变，新的发布产生新快照。

## 1. 快照索引与缓存

### `SnapshotIndex`（`graph/domain/index.py`，纯内存、不可变）

- `nodes: dict[node_id, GraphNode]`、`edges: dict[edge_id, GraphEdge]`、`evidence: dict[evidence_id, GraphEvidence]`。
- `adjacency: dict[node_id, tuple[edge_id, ...]]`（无向，按边 ID 排序保证确定性）。
- `label_tokens: dict[token, frozenset[node_id]]`：标签与规范键小写分词（含 CJK 单字与二元组），搜索先按 token 求交再做子串确认。
- `chunk_facts: dict[chunk_id, tuple[fact_id, ...]]`：证据切片 → 节点/边的反向索引（子项目 5 使用）。
- `degree: dict[node_id, int]`。

现有搜索、邻居、路径语义（排序、上限、错误）保持不变，由 `SnapshotIndex` 实现，`InMemoryGraphStore` 改为委托给它，保证两种存储行为一致。

### `CachedSnapshotIndexes`（`graph/application/cache.py`）

- 以 `(project_id, snapshot_id)` 为键的 LRU，按估算条目数（节点+边+证据）计容量，默认上限 200 万条目；单快照超过上限时不缓存、直接构建一次性索引并记告警。
- single-flight：同一快照并发未命中只加载一次。
- 只缓存 `READY` 快照；授权与项目范围检查每次照常执行，缓存命中不绕过 `require_project_scope`。
- span `graph.index.load`（命中/未命中、条目数、耗时）。

`MysqlGraphStore` 的查询方法改为：校验范围 → 取快照元数据 → `cache.get_or_load(snapshot, loader)` → 在索引上查询。loader 复用现有分表查询，一次构建。

## 2. 领域总览

### 语义

项目范围内，所有当前已发布来源各自的活跃 `READY` 快照合并成一张概览图：

- 节点按 `(node_type, canonical_key)` 合并，合并节点 ID 为 `overview:` + 键摘要，保留来源快照与原节点 ID 列表。
- 边按 `(合并后源, 关系类型, 合并后目标)` 合并，记录支持的原边数与来源数。
- 按合并后度数取前 `node_limit`（默认 150，上限 300）个节点及其之间的边。
- 统计：节点类型分布、来源数、快照 ID 列表。

### 接口

- `POST /api/v1/projects/{project_id}/knowledge/graph/overview`，请求 `{nodeLimit?}`，权限 `knowledge.read`；响应 `GraphOverview`：`nodes`（`overviewNodeId`、`label`、`nodeType`、`degree`、`members[{snapshotId,nodeId,sourceId}]`）、`edges`（`overviewEdgeId`、`source`、`target`、`relationType`、`support`）、`stats`、`snapshotIds`。
- 已发布来源集合复用 `PublishedKnowledgeAuthority`，只纳入当前已发布且授权的来源；无快照时返回空图与 `stats.sourceCount=0`。
- 结果按快照 ID 集合与 `nodeLimit` 进程内缓存（快照不可变，集合变化即失效）。

## 3. 前端

`apps/tap-ai-frontend` Library 图谱区：

- 视图切换“领域总览 / 来源图谱”，默认领域总览；来源图谱保持现有行为。
- 总览节点详情列出成员来源，点击跳转到对应来源图谱并选中原节点。
- 空态：“尚无已发布知识生成的图谱”；无项目时沿用“未选择项目”。
- `/prototype` Library 已有领域总览设计；如字段展示与本设计不一致，同步 fixture，比较前后截图。

## 4. 基准

- `tests/perf/test_graph_query_benchmark.py`（标记 `perf`，不进入 `make test`）：生成确定性随机图（1 万节点、5 万边、幂律度分布），经 `CachedSnapshotIndexes` 预热后各执行 200 次搜索、深度 2 邻居、路径查询，断言 p95 < 300ms，并输出 p50/p95。
- `make graph-bench` 运行；`TAP_RUN_MYSQL_INTEGRATION=1` 时追加 MySQL 加载耗时报告（冷加载不计入 p95）。

## 5. 测试与验收

- 单元：`SnapshotIndex` 与旧 `InMemoryGraphStore` 结果一致性（现有图谱存储契约测试套件对两者都运行）、分词、反向索引、LRU 容量与 single-flight。
- 契约：overview 接口（合并、排序、上限、空图、授权）。
- MySQL 集成：第二次查询不再访问节点表（计数 loader 调用）。
- 前端：视图切换、总览渲染、跳转来源图谱。
- `make graph-bench` 达标；`make check`、`make test` 通过；更新 `docs/architecture.md`。
