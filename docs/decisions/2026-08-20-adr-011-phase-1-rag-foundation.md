---
id: ADR-011
status: superseded
date: 2026-08-20
supersedes: []
superseded-by:
  - ADR-019
related-rfcs:
  - RFC-007
---

# ADR-011：第一交付阶段专注 RAG Foundation

- **状态**：历史决策；已于 2026-09-02 被 [ADR-019](2026-09-02-adr-019-phase-1-intelligence-layer-exploration.md) 替代。
- **决策**：第一阶段交付可评测、权限安全、可追溯的 RAG 垂直切片，包括四索引、分型解析/切片、增量索引、hybrid retrieval、RRF/rerank、Parent/Child、多跳检索、引用、Retrieval API/Inspector 和正式的 Knowledge Chat。
- **非目标**：Agentic Loop、Test IR 编译器、Browser/Device Grid、自愈/RCA 和自动代码修改不作为 Phase 1 出口条件。
- **原因**：检索质量、权限和数据治理是后续 Agent、测试生成与 RCA 的共同地基，应先独立验证。
- **后果**：后续组件只能通过稳定 Retrieval/Citation Contract 使用 RAG；不得在各 Agent 内重复实现私有检索链路。Chat 只做知识问答，不提前引入工具执行或测试工作台。
