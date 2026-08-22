---
id: ADR-006
status: accepted
date: 2026-08-20
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-006：内网自建执行网格，BrowserStack 作为能力标杆与可选 Provider

- **状态**：已确认约束，本次补充端口形式。
- **决策**：个人 Lab/内网使用 Selenium Grid 4、Appium Device Farm 和 API/Contract Runner；企业可在 AKS/KEDA 扩展。BrowserStack 用于外部矩阵、能力对标或明确允许的场景，通过 `ExecutionProvider` 适配。
- **原因**：内网隔离环境不能把 BrowserStack/Manus 当必需运行依赖。
- **后果**：统一证据与 Test IR 不能使用 BrowserStack 私有数据模型作为核心；Local Tunnel 必须短生命周期和最小路由。
