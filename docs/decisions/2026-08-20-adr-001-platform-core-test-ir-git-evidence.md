---
id: ADR-001
status: superseded
date: 2026-08-20
supersedes: []
superseded-by:
  - ADR-024
related-rfcs: []
---

# ADR-001：平台核心采用 Test IR + Git 版本化 + 统一执行证据

- **状态**：历史决策；已由 [ADR-024](2026-09-04-adr-024-tap-managed-automation-revisions.md) 替代。Test IR 与统一 Evidence 原则由新 ADR 重述并保留，Git 强制事实源不再有效。
- **决策**：自然语言、BDD 和低代码编辑首先生成或修改 Test IR，再编译为 Selenium、Playwright、Appium、Cucumber 或 API/Contract 执行资产。BDD、IR、生成代码、Locator、Fixture、Hook 和数据模板进入 Git；所有执行端输出统一 Evidence Manifest。
- **原因**：避免 `Prompt → Framework Code` 的不可控生成；同时获得结构化编辑、语义 diff、跨框架编译、稳定 Test ID 和完整审计。
- **后果**：Test IR Schema、编译器、Git layout 和 migration 成为首要基础设施。
