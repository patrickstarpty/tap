---
id: ADR-013
status: superseded
date: 2026-08-21
supersedes: []
superseded-by:
  - ADR-019
related-rfcs:
  - RFC-007
---

# ADR-013：Phase 1 交付 Codex/Claude Code 式 Knowledge Chat

- **状态**：历史决策；已于 2026-09-02 被 [ADR-019](2026-09-02-adr-019-phase-1-intelligence-layer-exploration.md) 替代。
- **决策**：交付 `TAP Knowledge Chat`，采用 Project/Conversation、流式可观察阶段、停止、排队追问、`@resource`、逐条 Citation 和 Sources/Claims/Trace 侧栏的交互模式。参考 Codex 与 Claude Code 的交互原则，不复制品牌视觉，也不展示模型隐藏思维链。
- **原因**：API/离线指标不足以验证真实用户是否能理解范围、发现降级、追查证据并反馈检索遗漏；Chat 是 Phase 1 的正式验收面。
- **后果**：浏览器只连接 BFF，不直连 AI Search/LiteLLM；公共 DTO 不接受 ACL 字段。历史答案、Citation 和 Trace 每次按当前身份重授权。Phase 3 在这一外壳上增加 Test IR editor、Run、审批，而不是重新建设另一套 Chat。
