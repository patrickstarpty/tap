---
id: ADR-029
status: accepted
date: 2026-09-18
supersedes:
  - ADR-028
superseded-by: []
related-rfcs:
  - RFC-011
---

# ADR-029：LangGraph 统一编排 Tap AI 的 AI 交互与任务

## 背景

Tap AI 不只有低延迟知识问答，还包括测试方案生成、完整文件分析、根因分析、等待外部结果或人工确认等任务。这些任务有两个彼此独立的变化维度：执行可能在线立即完成，也可能需要异步、持久化和恢复；推理可能是直接生成、固定工作流，也可能需要受预算约束的 Agentic Loop。

[ADR-028](2026-09-18-adr-028-langgraph-unified-chat-orchestrator.md) 确定了统一 LangGraph、稳定领域端口、ModelGateway/LiteLLM 及 MySQL 检查点方向，但只区分 Simple fast path 与 Complex agentic loop，并把外部等待和长时间执行隐含在复杂路径中。这会把“耗时长”错误等同于“推理复杂”，也把测试管理等 Tap AI 页面发起的 AI 任务排除在 Project Chat 字面作用域之外。

## 决策

Tap AI 用户从 Project Chat、测试管理或其他 Tap AI 自有入口发起的 AI 交互与 AI 任务，统一进入 Tap AI 后端内的版本化 LangGraph。后端在入图前完成身份、Project、资源范围、幂等键和基础输入校验；图内的 Task Classification & Admission 节点记录任务类型、执行剖面、推理方式、模型层级建议和预算，并由确定性策略最终裁定。

统一图提供三种明确、可观测的执行剖面：

- **Fast Chat**：用于普通问答、单次受限检索、澄清或一次模型生成。默认在线执行，限制节点、工具、模型调用、时间和持久化开销；不进入反思、修复或多轮 Agentic Loop。
- **Durable Workflow**：用于步骤已知但耗时较长、需要后台处理、外部等待或人工确认的任务。使用类型化节点、MySQL checkpoint、Task/GraphRun 状态、Outbox、Redis 至少一次唤醒和独立 Worker；持续发布进度，支持暂停、恢复、取消、超时和幂等重试，但默认不启动 Agentic Loop。
- **Bounded Agentic Task**：用于需要规划、多步检索、RCA、受控工具组合、观察与有限修复的复杂目标。采用有上限的 `Plan → Act → Observe → Adjust`，限制步骤、时间、工具调用和费用；当运行超过在线预算、等待外部结果或需要人工确认时，使用 Durable Workflow 的持久化与 Worker 机制继续执行。

三种剖面不是三套服务。它们共享身份、权限、Conversation/Turn、Task/GraphRun、图版本、状态契约、审计、引用、响应核验和结束处理。执行时长与推理复杂度分别记录：`execution_mode = inline | durable`，`reasoning_mode = direct | workflow | agentic`。用户看到的三种剖面是常用组合；复杂任务可以是 durable，长任务不必是 agentic。Fast Chat 如在产生外部副作用前发现超出在线预算，可记录原因并提升为 Durable Workflow，不得静默截断或伪装为已完成。

路由职责保留但不拆成独立服务：

- Task Classification & Admission 是 LangGraph 内的节点和条件边，不部署独立 Query Router 服务。
- 图内的理解、计划、推理和生成节点经 `ModelGateway → LiteLLM → 模型供应商`。ModelGateway 根据能力、数据范围、逻辑模型层级、预算和策略裁定调用；LiteLLM 只把获准别名映射到供应商部署，不部署独立 Model Router 服务。
- 知识获取经 `Knowledge Search Tool → SearchPort → Milvus hybrid search → 可选的已授权 active MySQL Graph Snapshot 有界扩展`。
- 平台历史指标和 RCA 经 `Insights Tool → TAP Insights API → ClickHouse`；Tap AI 不直连或改写指标事实。
- 业务动作经 TAP Domain APIs；发布、审批、执行和质量规则继续由所属业务模块裁定。
- chDB 仅用于明确授权的隔离文件或快照计算，不是 Chat、Durable Workflow 或 RCA 的默认主链。

MySQL 保存 Conversation、Turn、Task、GraphRun、图版本、checkpoint、租约、策略、工具调用、模型用量、审计与幂等信息。状态、checkpoint、Domain Event 与 Outbox 在同一事务提交；Redis 只负责可重建的至少一次唤醒。等待期间释放 Worker，恢复前检查租约和外部副作用，避免重复执行。每个 GraphRun 固定创建时的图版本；旧 checkpoint 只能由兼容版本恢复或经过显式、可测试、可审计的迁移。

LangGraph 是 Tap AI 后端内部的编排库和运行契约，不建设为面向其他产品或任意租户的通用 Agent/Workflow 平台。TAP 非 AI 产品的自动化执行、负载执行和 Insights 数据处理继续由其领域服务与 Worker 管理；Tap AI 只能通过正式跨产品接口请求这些能力。

[ADR-018](2026-09-01-adr-018-tapper-local-codex-tool-free-answer.md) 的 RFC-006 legacy loopback Codex 回答组合不挂载 Tap AI Project API，是本决策作用域外的显式例外。本决策描述目标架构，不表示当前 V1 已实现 LangGraph、三种执行剖面、工具循环或模型层级策略。

## 考虑过的方案

- **继续只分 fast path 与 complex loop**：无法表达长时间但确定性的解析、生成、等待和审批任务，会迫使这些任务错误进入 Agentic Loop。
- **Fast Chat、长任务和复杂任务分别建设服务**：可以分别优化，但会复制权限、状态、事件、审计、引用和恢复合同，使同一任务升级或切换路径难以追溯。
- **新增 Query Router 与 Model Router 微服务**：增加网络跳数、可用性依赖和第二套策略状态；图内 Admission 节点与 ModelGateway 已覆盖所需职责。
- **把 LangGraph 建成全公司的通用 Agent 平台**：扩大产品、租户、插件、安全和运维责任，超出 Tap AI 当前范围，并会模糊 TAP 领域服务的权威边界。
- **所有任务都使用 Agentic Loop**：增加延迟、成本和不可预测性，也无法替代确定性长任务所需的可靠队列、租约、检查点与幂等语义。

## 后果

- API 与事件合同需要区分即时回答和后台任务，并携带 Conversation、Turn、Task、GraphRun、Graph Version、Checkpoint、Execution Mode、Reasoning Mode 和预算标识。
- Fast Chat 必须有明确延迟预算和工具上限；Durable Workflow 必须提供排队、进度、等待原因、恢复、取消与超时；Bounded Agentic Task 还必须显示计划阶段、工具调用、预算消耗和停止原因。
- 复杂任务使用 durable runtime 时，Agentic Loop 只负责受控决策，不负责替代可靠队列、状态机或外部副作用幂等。
- `ModelGateway`、`SearchPort`、TAP Insights API 和 TAP Domain APIs 是稳定端口；替换模型、Milvus 或 ClickHouse 实现不得改变图的领域契约。
- 图、状态 schema 和执行剖面必须版本化；实施和验收需分别覆盖 Fast Chat 延迟、Durable Workflow 恢复以及 Bounded Agentic Task 的预算与停止条件。
