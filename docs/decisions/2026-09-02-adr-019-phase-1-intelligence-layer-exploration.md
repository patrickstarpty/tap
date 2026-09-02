---
id: ADR-019
status: accepted
date: 2026-09-02
supersedes:
  - ADR-011
  - ADR-013
superseded-by: []
related-rfcs:
  - RFC-007
---

# ADR-019：Phase 1 优先探索 Intelligence Layer

## 背景

TAP 的长期主体是 BrowserStack-like Test Automation Platform：管理测试资产、自动化、运行、浏览器与设备、执行证据和分析。当前 Athena 只实现了一条 loopback、无认证、`doc`-only 的资料摄取、限定来源回答和引用核验切片。它验证了不可变来源和引用基础，却还没有回答 AI 能否真正改善测试工程工作。

用户也不一定拥有正式需求、Release、产品源码或测试仓库。如果将这些对象设成开始 AI 工作的强制父级，会把零散目标、无源码调查和 UiPath-like 流程自动化等有效场景错误排除。反之，若直接投入完整测试管理、设备矩阵和执行调度，又会在 AI 差异化价值尚未证明时提前扩大平台工程。

## 决策

1. Phase 1 的当前产品重点改为单用户、loopback 的 `TAP Intelligence Lab`，用于验证 Grounded Understanding、Automation Design 和 Durable Agent Task 是否对测试工程有稳定收益。
2. 创建任务时只强制要求 `goal`。P1.0–P1.2 的公开 wire contract 只接受 goal、可选人工步骤和用户明确选择的 `ready` Knowledge Source；零散 Requirement 文本可以写入 goal/步骤或作为资料来源，但尚不提供 typed Requirement、Release、Project、仓库或失败材料关联。缺少资料时进入 `assumption-first` 路径，不得伪造事实、引用或执行状态；这些 typed relation 在后续阶段加入且不成为强制父对象。
3. Phase 1 以版本化的 `AutomationBrief`、`ContextSnapshot`、私有 `RuntimeContextPacket`/`RuntimeInvocationEnvelope`、`InputManifest`、共享 Runtime lineage、固定 proposal/root Schema、`IntelligenceReport`、`AssumptionRegister`、`AutomationBlueprint`、不可变 `IntelligenceArtifact`、append-only Validation、派生 Artifact View、`ReviewPackage` 和 `ArtifactReview` 作为正式记录。聊天只能补充上下文，不是系统记录。
4. `IntelligenceTask`、`TaskStep` 和 `Attempt` 使用独立持久化身份和状态机，不复用 Chat Turn 或 Knowledge Ingestion Job。MySQL 是事实来源并持有 lease/fencing；Redis 只做可重建唤醒；每个 Attempt 固定完整有界输入与 root Schema 的 canonical bytes/ref，输出必须经过可信 Artifact Broker 封存和独立 Validator 核验。
5. 交付顺序为 P1.0 契约与评测、P1.1 Grounded Intelligence、P1.2 Durable Agent Task、P1.3 条件开启的失败分析与候选工程实验、P1.4 retain/revise/stop 阶段决策。P1.3 不得成为 P1.0–P1.2 的出口条件。
6. Codex 是首个实验 `AgentRuntime` Adapter，但必须位于 provider-neutral port 之后，可被关闭或替换，不作为用户可见的“模式”。P1.2 只允许 read-only Profile；P1.3 的 workspace-write 必须先通过独立安全门禁。
7. Phase 1 不交付 Test Management、真实 Browser/Device 执行、Execution Provider、Release Management、正式 Test Asset、远程 Git/PR、缺陷写入或多租户生产治理。它的 `execution_status` 固定为 `not_run`；没有真实 Provider Attempt 和 Evidence Manifest 就不得宣称测试已运行、通过或验证。
8. Athena 已实现的文档 revision/hash/anchor、引用、Outbox、lease 和可恢复 worker 模式作为可复用基础保留；它的固定 demo policy、Knowledge Ingestion Job、Chat Turn 和 tool-free Answer Adapter 不得泛化成 Intelligence 控制面。
9. 本决策替代 ADR-011 和 ADR-013 对“Phase 1 产品验收优先级”的选择。两份旧 ADR 保留为历史记录；RAG Foundation 和 Knowledge Chat 仍是后续平台的 Knowledge Plane 设计，但不再是当前 Phase 1 的出口条件。
10. Runtime 必须使用 `prepare -> register -> reauthorize -> activation_intent -> activate` 两阶段启动。在 `prepare` 前和 launcher 登记后、发送 invocation bytes 前，都必须按当前 actor/scope 和输入中的精确 source revision/hash/anchor 即时重授权；未激活 launcher 不得获得 Context 正文或发起模型请求。撤权或来源替换后不得把旧 Context 文本发给模型，只能要求创建新的 Context Snapshot。

## 考虑过的方案

- **继续完成完整 Knowledge Chat 和企业 RAG**：能延续当前工程，但优先验证的仍是“会回答”，不是结构化测试工程产物和长任务价值。
- **先建设完整 BrowserStack-like 主体**：长期方向正确，但资产、调度、设备和证据面投入过大，会延迟 AI 价值验证。
- **只做 Research Agent**：安全边界最小，但容易变成更慢的 Chatbot，无法验证 Blueprint、Review Package 和工程候选价值。
- **直接建设 UiPath-like 通用 RPA 或多 Agent 平台**：场景广但边界、工具和评测目标失控，且偏离测试自动化主线。
- **把 Codex、Claude Code、Manus 等暴露为产品模式**：演示直观，但将供应商细节泄漏到公共契约，造成锁定和不一致治理。

## 后果

- Phase 1 可以在没有 Requirement、Release、Project 或源码时开始；P1.0–P1.2 不谎称已接入这些 typed relation，结果仍以可审查 Artifact 而非临时聊天交付。
- 工程优先级转向 Intelligence 契约、评测数据、可恢复 Task 和 Review UX；完整 RAG、Test Management 与真实执行暂后移。
- 需要新增独立 Intelligence bounded context、公共 API/可恢复事件投影、Artifact 存储、Validator 和 Evaluation Harness，并为 Runtime 隔离承担明确安全成本。
- P1.3 可能在安全或价值门禁不达标时被停止；这不会使 P1.0–P1.2 失去价值。
- BrowserStack-like 平台仍是后续主线，但只消费经 P1.4 决定保留的 Artifact 契约和 AI 能力。
- 必须同步更新路线图、总体架构、核心契约和 README，并用独立 Plan 执行 P1.0–P1.2。
