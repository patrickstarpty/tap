# 图谱脉络分析设计

日期：2026-11-03。V1 子项目 5（能力 3），见 [V1 总纲](../plans/2026-09-29-v1-roadmap.md)。依赖 [图谱查询扩展](2026-11-03-graph-query-extension-design.md)（快照索引、切片反向索引）与 [Skills/Agents](2026-11-03-skills-agents-design.md)（工具注册表、有界工具循环），以及 [可靠问答](2026-11-03-reliable-answering-design.md) 的 golden set。

## 现状

- `GraphAnswerEnricher` 用问题文本在活跃快照上做标签搜索，取最多 20 个节点及相关事实作为 `knowledgeGraph` 上下文；与检索命中无关，不做多跳扩展。
- 回答引用只能指向原文切片；无法引用关系边，前端无法从引用定位图谱。

## 目标

1. 检索片段 → 实体 → 1–2 跳扩展，作为回答的图谱上下文。
2. 关系类问题进入有界 LangGraph 工具循环，可调用图谱工具继续探索。
3. 回答同时引用关系边与原文片段；点击关系引用在图谱中高亮路径。
4. golden set 含关系类问题子集。

## 非目标

- 新的图谱抽取或推理规则；多快照联合推理（只用选中来源的活跃快照）。
- 移除 `classify → admit → execute` 主图；工具循环作为 `execute` 内的子图。

## 1. 子图扩展

`GraphContextExpander`（`knowledge/application/graph_expansion.py`）替代 `GraphAnswerEnricher.enrich` 的搜索逻辑，授权、发布校验与状态语义保持不变：

1. 种子：检索命中的切片 ID 经快照索引 `chunk_facts` 映射到节点与边；再并上问题文本标签搜索的前 5 个节点（保留原行为作为补充）。
2. 扩展：从种子节点按度数升序（优先具体实体）做 BFS，`hops = 2`（规划为关系问题）或 `1`（其他），上限 40 节点、80 边；边按置信度降序截断。
3. 上下文：每条边带稳定标签 `R1..Rn`，包含源/目标标签、关系类型、来源（抽取/推理）、置信度与证据切片标签；节点不单独编标签。
4. span `graph.expand`：种子数、跳数、节点/边数、是否截断。

## 2. 关系问题与工具循环

- 规划器（`AnswerPlanner`）输出新增 `intent`：`relation` 表示问题关心实体之间的关系、依赖、影响或路径；规划 schema 与提示词同步更新，旧计划缺省为 `fact`。
- `intent=relation` 且图谱上下文 `READY` 时，`execute` 进入工具循环（Skills/Agents 设计第 5 节），工具集 = `graph.neighbors`、`graph.path`、`knowledge.search` ∩（Agent 白名单 ∪ 平台默认关系工具集）。未选 Agent 时使用平台默认关系工具集。
- 图谱工具（内置，`effect=read`）：
  - `graph.neighbors {nodeLabel|nodeId, depth≤2}`、`graph.path {from, to, maxHops≤4}`，在当前快照索引上执行，返回的新边追加 `R` 标签，新证据切片进入引用候选集。
  - 只能访问本 Turn 已授权的快照。
- 循环上限沿用工具循环：≤ 4 步。

## 3. 回答与引用

### 回答 schema

`claims[]` 每项新增可选 `relationLabels`（`R` 标签数组）。校验：

- `relationLabels` 必须来自本 Turn 上下文或工具结果中出现的边。
- 含 `relationLabels` 的 claim 至少同时有一个 `evidenceLabels`，或其引用边的证据切片自动补为原文引用（推理边无证据时只能与原文证据同用）。
- 校验失败的 claim 按现有规则剔除；全部剔除则拒答。

### 持久化

- 新表 `chat_relation_citation`：`citation_id`、Turn、`snapshot_id`、`edge_id`、`source_node_id`、`target_node_id`、`relation_type`、`origin`、`claim_index`、`path_edge_ids`（JSON，同一 claim 引用的连续边序列，按工具路径或扩展顺序）。
- 原文引用沿用现有 citation 快照；关系边的证据切片以普通原文引用保存，与关系引用通过 `claim_index` 关联。
- `ConversationTurnSummary`/回答响应新增 `relationCitations[]`；SSE 新增 `relation_citation.resolved` 事件（契约同步）。

## 4. 前端

- 回答中关系引用显示为关系芯片（`源 —关系→ 目标`），与原文引用并列。
- 点击芯片：打开 Library 图谱的来源图谱视图（路由参数 `snapshot`、`highlight=edgeId,...`），高亮路径上的节点与边并居中；快照已不是活跃快照时提示“图谱已更新，显示的是历史快照”，仍按快照 ID 查询（快照不可变）。
- 图谱查询接口新增 `POST .../knowledge/graph/subgraph {snapshotId, edgeIds[]}` 返回指定边及端点，供高亮直接加载。
- `/prototype` Tapper 回答同步关系芯片与高亮跳转（fixture），比较前后截图。

## 5. golden set

- `kb-golden.v1.jsonl` 增加 `category=relation` 用例（≥ 8 条），语料含可抽取的实体关系；运行器为这些用例构建图谱快照（假模型的确定性抽取）。
- 指标 `relationRecall`：回答引用的关系类型命中 `expect.relations` 的比例；纳入假模型基线。

## 6. 测试与验收

- 单元：种子映射、扩展上限与确定性排序、`R` 标签分配、`relationLabels` 校验与证据补全。
- 契约：规划 `intent`、工具循环调用 `graph.path` 后回答引用新边、关系引用持久化与接口字段、SSE 事件、subgraph 接口授权。
- 前端：关系芯片渲染、跳转参数、高亮。
- E2E：隔离旅程中提问关系类问题，回答含关系引用，点击后图谱高亮。
- golden 关系子集纳入回归；`make check`、`make test`、`make demo-e2e` 通过；更新 `docs/architecture.md`。
