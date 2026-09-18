---
id: ADR-028
status: accepted
date: 2026-09-18
supersedes: []
superseded-by: []
related-rfcs:
  - RFC-011
---

# ADR-028：LangGraph 作为统一 Chat Orchestrator

## 背景

Tap AI Project Chat 既包含带引用的快速知识问答，也包含需要检索、指标分析、外部等待、人工确认或有限修复的复杂任务。[ADR-003](2026-08-20-adr-003-dag-and-agentic-loop.md) 已确定主 Agent 同时支持固定 DAG 与 Agentic Loop，但尚未确定 Project Chat 的统一运行入口和具体框架。

如果按功能分别引入 Query Router、Model Router、独立 Agent 服务或绕过业务接口的工具调用，会重复实现权限、来源范围、预算、审计、会话恢复和幂等控制，并使同一会话中的行为难以追溯。[ADR-023](2026-09-04-adr-023-milvus-mysql-knowledge-backend.md) 已确定知识检索采用 Milvus 文档投影与 MySQL Knowledge Graph；RFC-011 则需要把知识、历史指标和业务工具组织进一条可恢复的 Chat 调用链。

## 决策

所有经 Tap AI Project API 进入的 Chat 请求都进入同一个版本化 LangGraph。图在确认当前用户、项目、Conversation、Turn、授权范围和请求幂等键后，以图内条件边选择执行路径。首次意图理解与复杂度判定也是图内节点，不存在入图前的模型调用：

- **Simple fast path** 用于普通问答、单次受限检索和一次模型生成；不进入反思、修复或多轮 Agent loop，保持低延迟。
- **Complex agentic loop** 用于需要多步检索、受控工具组合、外部或人工等待、有限调查或修复的任务；每轮受最大步骤数、时间、调用次数和费用预算约束，并在不确定、权限不足、预算耗尽或需要人工确认时停止。
- 两类路径共享同一图的入口、状态契约、权限检查、审计、引用和结束处理。复杂度判定是图内的受控条件与策略，不部署独立 Query Router 服务。

图内节点只能通过稳定领域边界访问能力：

- 图内的意图理解、计划、推理与生成节点经 `ModelGateway → LiteLLM → 模型供应商`；不新增 Model Router 服务。`ModelGateway` 负责能力许可、模型层级策略、输入输出约束、超时、重试、用量和调用记录，LiteLLM 负责把获准别名映射到供应商部署。SearchPort 内部如需 Embedding 或 Reranking，也使用 ModelGateway 获准的对应能力，但不回到对话 agentic loop。
- 知识获取经 `Knowledge Search Tool → SearchPort`；SearchPort 先在 Milvus 做受 Project/Source 范围限制的 hybrid search，再可选地对已授权 active MySQL Graph Snapshot 做有界扩展，持续应用来源版本、发布状态和访问范围。检索结果、Graph Context 与引用定位进入 Turn 记录。
- 平台历史指标和 RCA 所需分析经 `Insights Tool → TAP Insights API → ClickHouse`；Chat 不直接连接 ClickHouse，也不自行定义或改写指标口径。该跨产品调用必须有版本化合同、服务身份、Project 范围授权、超时和审计；TAP 或 ClickHouse 不可用时返回明确的能力不可用，不伪造零结果，也不阻止不需指标的知识 Chat 在 Tap AI 内独立运行。ClickHouse 是 RFC-011 目标能力，不改写 [ADR-022](2026-09-04-adr-022-self-hosted-compose-delivery-baseline.md) 的当前 Compose 基线；跨产品合同按 [ADR-027](2026-09-15-adr-027-tap-ai-product-app-boundary.md) 单独验收。
- MySQL 保存业务状态、Conversation、Turn、图状态和检查点、策略决策、工具调用、来源版本、模型用量、审计记录及恢复所需的幂等信息。按 [ADR-009](2026-08-20-adr-009-mysql-outbox-redis-delivery.md)，状态、检查点、Domain Event 与 Outbox 在同一 MySQL 事务提交，Redis 只作可重建唤醒，消费者按至少一次与业务幂等处理。长等待时释放 Worker；恢复前检查外部副作用是否已发生，避免重复试跑、发布或写入。
- chDB 仅可在明确授权的隔离文件或快照计算任务中使用。它不是 Project Chat/RCA 的核心链路，不持有平台数据库凭证，也不作为会话、检查点、长期指标或根因结论的事实源。

LangGraph 是后端模块使用的编排库，不要求每个图节点成为独立服务。业务模块继续拥有发布、审批、执行、质量规则及其权威数据；图只组织 Chat 内的步骤、状态转换、暂停和恢复。每个运行固定启动时的 graph version；该版本至少保留到运行终止或取消。旧 checkpoint 只能由原版本恢复，或经显式、可测试、可审计的状态迁移后进入新版本，禁止把不兼容旧状态直接装入新图。

[ADR-018](2026-09-01-adr-018-tapper-local-codex-tool-free-answer.md) 的 RFC-006 legacy loopback Codex 回答组合不挂载 Tap AI Project API，是本决策作用域外的显式例外；它仍保持单智能体、无工具且回答不调 LiteLLM。本决策确定 Tap AI Project Chat 的目标架构，不表示当前 V1 已实现 LangGraph、工具循环或模型层级路由。

## 考虑过的方案

- **按简单和复杂请求分别建设 Chat 服务或 Orchestrator**：可分别优化，但会造成会话、权限、审计、恢复和引用契约分叉，并使路径切换难以追溯。
- **新增 Query Router 与 Model Router 服务**：看似能集中路由，却在当前范围内增加网络跳数、可用性依赖和第二套策略或审计状态；图内条件边与 `ModelGateway` 已覆盖所需职责。
- **只让复杂任务使用 LangGraph，普通 Project Chat 继续走独立流程**：短期改动较少，但无法保证所有 Project Chat 经过相同的授权、记录、引用和收尾边界。
- **让模型直接访问 Milvus、ClickHouse 或 chDB**：会绕过领域权限、发布范围、指标口径、资源限制与审计，不可接受。
- **将 chDB 置于 RCA 主链**：它适合一次性获准文件或结果快照计算，不能替代由 TAP Insights API 管理的 ClickHouse 历史指标与 RCA 事实。

## 后果

- Chat API 与事件需要携带稳定的 Conversation、Turn、Request、Graph Version 和 Checkpoint 标识；每个可恢复节点声明输入、输出、允许的副作用和幂等语义。
- 图发布必须包含版本保留、checkpoint 兼容性和显式迁移策略；无法安全恢复的旧运行必须停止并返回可审计原因，不能在新图上猜测继续。
- Simple fast path 仍须经过统一图，因此有固定的状态持久化和编排开销；通过限制节点数、工具数与持久化粒度控制延迟。
- Complex agentic loop 必须提供可观察的进度、暂停原因、预算消耗、失败位置和人工继续入口；恢复、取消、并发领取和外部副作用去重须以真实集成验证。
- `ModelGateway`、`SearchPort` 和 TAP Insights API 成为 Chat 图的稳定端口。替换 LiteLLM、Milvus 或 ClickHouse 实现不得改变 Chat 图的领域契约；TAP Insights API 的身份、授权、可用性与错误语义需在跨产品合同中独立验收。
- 本决策不把 LangGraph 扩展为通用工作流平台，也不改变 [ADR-010](2026-08-20-adr-010-modular-control-plane-independent-workers.md) 的 Worker 边界、[ADR-022](2026-09-04-adr-022-self-hosted-compose-delivery-baseline.md) 的部署基线或 [ADR-027](2026-09-15-adr-027-tap-ai-product-app-boundary.md) 的产品边界。
