---
id: ADR-003
status: accepted
date: 2026-08-20
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-003：主 Agent 同时支持固定 DAG 与 Agentic Loop

- **状态**：已确认。
- **决策**：确定、受监管的流程走 DAG；开放、需要观察和反思的目标走 `Plan → Act → Observe → Reflect → Adjust`。检索、执行和审查子 Agent 由同一 Orchestrator 管理并统一汇总。
- **原因**：单纯多 Agent 分工无法同时满足稳定流程与动态探索。
- **后果**：Loop 内的动态动作也必须映射为类型化 Task/Attempt，不能绕过状态、权限和预算。
