---
id: ADR-031
status: accepted
date: 2026-10-09
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-031：项目级图替代按来源集合的快照，Agent 子图契约

## 背景

PR 2 把图谱从"每次查询按来源集合即时合并快照"改为项目级持久合并版本（`graph_project_version`/`graph_project_node`/`graph_project_edge` 等表，按 `(project_id, version)` 进程内缓存邻接表、节点字典与别名索引），解决了"每次查询把整个快照载入内存"的问题，但这一取舍（项目级版本 vs 按查询即时过滤的来源集合快照）跨 `knowledge`、`graph`、`chat` 三个模块，且 PR 3 在其上新增了关系分析 Agent 子图（`modules/ai/application/agents/relation_analysis.py`），两者都需要一个跨模块记录的决策，而不只是模块内文档。

## 决策

- 图谱查询以项目级合并版本为单位，而非按单次查询的来源修订集合即时合并快照：`ProjectGraphStorePort` 的每个读方法接受 `version:`（`None` 表示当前 `READY` 版本，显式版本号只在仍处于 `ProjectGraphCache` 保留窗口内时可用，否则抛出 `ProjectGraphVersionMismatch`），让一次回答的全程（种子 -> 扩展 -> 路径 -> 排序 -> 组装）都钉死在同一个图版本上，即使后台合并在此期间发布了更新版本。
- 来源可见性仍按请求级的已选来源修订集合过滤（`allowed_source_revision_ids`），与项目级图版本正交：版本决定"图长什么样"，选集决定"这次回答能引用图里的哪些节点/边"。
- 关系分析管线以 `AgentSubgraph` 契约的一个实现运行：

  ```python
  class AgentSubgraph(Protocol[InputT, OutputT]):
      name: str
      input_schema: type[InputT]
      output_schema: type[OutputT]
      async def run(self, context: AgentContext, value: InputT) -> OutputT: ...
  ```

  `AgentContext` 携带 scope、已授权来源修订集合、项目图版本与 span 根；子图在自己的 span 内运行，失败时返回带状态的输出（`RelationContext.status`：APPLIED/NOT_READY/STALE/FAILED/EMPTY）而不抛出。子图之间不直接通信，只经调用方传递类型化对象；证据对象原样传递，不经模型转述。子图在 `modules/ai/application/agents/` 下注册，调用方现阶段是 `knowledge_service` 的确定性检索流水线，Skills/Agents 子项目的 orchestrator 预期复用同一注册表。
- `ProjectGraphVersionMismatch` 是子图内部的可恢复信号（映射为 `RelationContext.status=STALE`），不是失败；只有图存储本身的异常才映射为 `FAILED`。

## 考虑过的方案

- 继续按查询即时合并来源集合快照：被 PR 2 否决，无法摊销合并成本，且无法支撑路径推理这类需要跨多来源稳定邻接关系的查询。
- 让关系分析管线直接嵌入检索服务（不经 Agent 子图协议）：会让 Skills/Agents 子项目之后无法复用同一注册表和失败语义，且使检索服务直接依赖图谱内部实现而非稳定契约。

## 后果

- 图谱读路径的重试/过期语义统一由 `ProjectGraphVersionMismatch` ->`STALE` 承担，调用方（检索服务、Agent 子图）据此决定是否以新版本重试，而不是把它当作错误上报。
- 关系分析之外的未来 Agent（Skills/Agents 子项目）若需要读图，复用同一 `ProjectGraphStorePort`/`AgentSubgraph` 契约，不需要重新设计版本钉定或失败语义。
- 本决策不改变 ADR-030（知识切片保存、索引、启用直接生效）的发布/审批前置关系；流程图图像来源仍按 `FlowchartPublicationGate` 单独把关（见[知识图谱脉络分析设计](../superpowers/specs/2026-10-06-knowledge-graph-reasoning-design.md)）。

设计与实施证据见[知识图谱脉络分析设计](../superpowers/specs/2026-10-06-knowledge-graph-reasoning-design.md)第 1.2–1.3、2.2–2.3 节。本决策获批不等于所有实现验收均完成。
