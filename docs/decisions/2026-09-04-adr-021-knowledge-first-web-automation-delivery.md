---
id: ADR-021
status: accepted
date: 2026-09-04
supersedes:
  - ADR-014
  - ADR-019
superseded-by: []
related-rfcs:
  - RFC-009
---

> **命名归一化说明（2026-09-05）：** 本文只对产品和仓库标识做 Tapper 命名归一化，原日期、状态、决策、范围与评审结论未改变。命名归一化前的字节级原文以 Git commit `0eab801` 为准；下列命令或证据文本属于 identifier-normalized transcription，不再声明与该提交逐字节相同。

# ADR-021：当前交付主线采用 Knowledge-first Web Automation

## 背景

ADR-014 批准了 provider-neutral `AgentRuntime` 后的可选隔离 Runtime，并用旧 P1.2/P1.3 安排授权 Codex Specialist Runtime 实验；ADR-019 又把独立 Intelligence Lab 设为当前 Phase 1 出口，用于验证从不完整目标生成分析、Automation Blueprint 和 Review Package 的价值。后续产品设计已经收敛出更直接的客户闭环：Tapper 先提供有证据的知识问答和 Knowledge Graph，再生成可审查的 Test Plan/BDD，并把它转换为可录制、可执行、可回写结果的 Web Automation。

## 决策

当前交付顺序固定为：V0 Validation Scope 与可靠性基线，V1 可信知识问答，V2 Knowledge Graph，V3 AI 测试设计，V4 Web LCA 与 Recorder，V5 Jenkins 结果闭环，VG 方案验证出口；之后才进入 P0 身份与多 Project 产品化及 P1 生产加固。

独立 Intelligence Lab 不再作为当前交付出口。ADR-019 中可恢复 Task/Attempt、不可变 Artifact、确定性 Validator、事实/推断/假设/未知分离和人工 Review 等模式继续作为各里程碑的可复用工程原则，但不得形成与 Tapper、Test Management 和 LCA 平行的第二套产品事实源。

provider-neutral、可关闭、凭据隔离且不被 Knowledge API 依赖的 Specialist Runtime 仍可作为未来能力原则；ADR-014 的旧 P1.2/P1.3 Profile、权限与实施次序不再构成当前授权。只有完成 P1 后，经独立 RFC/ADR 重新界定用途、安全门禁和路线图，才能引入具体 Runtime Adapter。

当前路线只实现 Web Automation，目标代码为 Playwright + TypeScript，首个执行适配器为 Jenkins。Mobile、Appium、Azure DevOps、BrowserStack、Git Sync 和通用多 Agent 平台必须在 P1 之后以独立设计进入路线图。

## 考虑过的方案

- **继续先做独立 Intelligence Lab**：能验证通用研究产物，但客户价值与真实测试资产、录制和执行结果仍然割裂。
- **直接先做 LCA**：能快速展示录制和运行，但缺少可信知识、测试意图和证据来源。
- **Web 与 Mobile 同期交付**：覆盖面更广，但会同时引入设备、Appium 和平台差异，推迟首个可验证闭环。

## 后果

- 当前工程、路线图、验收和演示均以 V0–VG 为共同主线。
- 每个里程碑必须独立可验收；仅完成 UI、fake Adapter 或模拟 Run 不能越级宣称闭环完成。
- RFC-007、ADR-014 和 ADR-019 保留为历史设计与可复用模式来源，但不再授权其 P1.0–P1.4 或旧 P1.2/P1.3 Runtime 实施顺序。
- Knowledge、Conversation、Test Plan、Automation 与 Run 必须共享 Project scope、版本和证据语义。
