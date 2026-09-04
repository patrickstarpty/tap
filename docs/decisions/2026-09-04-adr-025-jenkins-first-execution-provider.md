---
id: ADR-025
status: accepted
date: 2026-09-04
supersedes:
  - ADR-006
superseded-by: []
related-rfcs:
  - RFC-009
---

# ADR-025：Jenkins 作为首个 Execution Provider

## 背景

ADR-006 以 Selenium Grid、Appium Device Farm 和可选 BrowserStack 为执行路线。当前方案已收窄为 Web-only，并希望优先复用免费、自托管且企业常见的 Jenkins Pipeline Agent；Azure DevOps Agent、移动设备和外部 BrowserStack 不是首个闭环的必要条件。

## 决策

核心保持 provider-neutral `ExecutionProvider`。首个正式 Adapter 使用 Jenkins Remote Access API 与版本化 Jenkinsfile/Pipeline 参数，在用户选择的受限 Pipeline Agent Label 上执行 Published Automation Revision 的 Playwright + TypeScript Bundle。Athena AI Agent 负责理解、生成和调整；Jenkins Pipeline Agent 只负责确定性执行，两者必须分开建模、授权和呈现。

TAP 是 Run、Attempt、状态、关联快照和 Evidence 的权威源。每次提交使用稳定 `submission_key`，Pipeline 开始前校验并 claim Run/Attempt/Revision/Bundle digest；未知提交进入 `SUBMIT_UNKNOWN`，Reconciler 通过 `ExecutionProvider.reconcile_submission(target, submission_key)` 查询统一的 `NOT_FOUND | QUEUED | STARTED` 结果，不调用 Adapter 私有 API、不盲目重试。Jenkins callback 与 polling 进入同一个幂等 Result Normalizer，产出统一 Evidence Manifest 和 BDD/Test IR Step Result。

只要正式 Run 开始时 Automation 与 Test Plan 已关联，同一 Run 就投影到 Automation 与 Test Plan 历史；未关联 Run 不回写 Test Plan。Mobile/Appium、Azure DevOps 和 BrowserStack 只能作为未来 Adapter 通过独立设计加入。

## 考虑过的方案

- **TAP 自建 Selenium Grid 调度器**：控制更细，但会在首期重复 Jenkins 已有的队列、Agent 和凭据能力。
- **Azure DevOps Pipeline Agent**：原型已探索其交互，但当前团队倾向 Jenkins，且不希望首期绑定付费平台。
- **在 API 进程直接启动 Playwright**：实现短，但隔离、资源控制、Secret 和故障恢复边界不足。

## 后果

- Jenkins Job、Agent Label、Runner Image、参数 Schema、API Token 和 callback secret 必须 allowlist、版本化并可轮换。
- TAP 必须处理至少一次 callback、重复 polling、未知提交、终态竞态、取消和证据完整性，不能把 Jenkins build 状态原样当领域状态。
- Debug Run 与 Published Revision 正式 Run 分离；只有后者可进入 Test Plan 执行历史。
- 新 Execution Provider 必须通过同一 conformance、幂等、证据和授权测试，不能改变 Test IR/Run 公共契约。
