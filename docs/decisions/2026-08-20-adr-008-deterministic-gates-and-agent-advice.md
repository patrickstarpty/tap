---
id: ADR-008
status: superseded
date: 2026-08-20
supersedes: []
superseded-by:
  - ADR-024
related-rfcs: []
---

# ADR-008：确定性门禁与 Agent 建议分离

- **状态**：已由 [ADR-024](2026-09-04-adr-024-tap-managed-automation-revisions.md) 替代；下述内容保留原决策记录。确定性门禁与 Agent 建议分离的原则已在 ADR-024 重述，Git-required 去向已取消。
- **决策**：模型负责理解、规划、归纳、失败分析和建议；测试执行、断言、重试、权限与发布判断保持确定性。自愈只生成候选 Test IR/代码 patch，经证据验证和审批后进入 Git。
- **原因**：防止假绿、prompt injection、模型漂移和不可复现结论污染发布流程。
- **后果**：Agent Finding 必须标记来源、置信度和 evidence refs，不能覆盖原始结果。
