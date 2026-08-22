---
id: ADR-007
status: accepted
date: 2026-08-20
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-007：DeepSeek Harness 仅作参考或候选 Runtime

- **状态**：已确认“不直接选为核心”；适配器 POC 待定。
- **决策**：借鉴 Cordis 插件生命周期、append-only Session Log、恢复/Fork/Replay、工具 Guard 与审计；不直接 fork 或嵌入平台核心。若试用，置于 `AgentRuntime` 端口之后并固定版本、隔离进程。
- **原因**：项目仍是 Developer Preview；破坏性变更、企业成熟度、安全隔离和插件/Web 控制面风险尚不足以支持核心生产依赖。可逆 effect 也不等于安全隔离。
- **后果**：LangGraph + FastAPI 只作为 Agentic Test Lab 的实验基线，企业 Runtime 选型未定；框架选择不得改变 TAP 领域契约。
