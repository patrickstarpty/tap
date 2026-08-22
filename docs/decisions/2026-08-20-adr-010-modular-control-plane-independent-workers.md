---
id: ADR-010
status: proposed
date: 2026-08-20
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-010：控制面先保持模块化，Worker 独立扩缩

- **状态**：拟议，建议采纳。
- **决策**：API/BFF、Authoring、Orchestrator、Policy、Result/RCA 可先以模块化部署单元交付；Agent、Browser、Device、API Worker 独立。按真实瓶颈再拆服务。
- **原因**：MVP 的主要风险是 Test IR、证据和可靠闭环，不是微服务数量。
