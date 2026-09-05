---
id: ADR-006
status: superseded
date: 2026-08-20
supersedes: []
superseded-by:
  - ADR-025
related-rfcs: []
---

# ADR-006：内网自建执行网格，BrowserStack 作为能力标杆与可选 Provider

- **状态**：历史决策；已由 [ADR-025](2026-09-04-adr-025-jenkins-first-execution-provider.md) 替代。Provider-neutral 与统一 Evidence 原则由新 ADR 重述并保留。
- **决策**：个人 Lab/内网使用 Selenium Grid 4、Appium Device Farm 和 API/Contract Runner；企业可在 AKS/KEDA 扩展。BrowserStack 用于外部矩阵、能力对标或明确允许的场景，通过 `ExecutionProvider` 适配。
- **原因**：内网隔离环境不能把 BrowserStack/Manus 当必需运行依赖。
- **后果**：统一证据与 Test IR 不能使用 BrowserStack 私有数据模型作为核心；Local Tunnel 必须短生命周期和最小路由。
