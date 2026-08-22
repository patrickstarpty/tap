---
id: ADR-002
status: accepted
date: 2026-08-20
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-002：企业部署基线采用 Azure 现有技术栈

- **状态**：已确认。
- **决策**：`AKS + PaaS MySQL + PaaS Redis + Azure AI Search + Blob Storage + Key Vault + LiteLLM`。
- **原因**：企业云为 Azure，现有数据库栈是 PaaS MySQL/Redis，并且 Azure AI Search 可用；无需新增 PostgreSQL PaaS 或让 Redis 承担检索主引擎。
- **后果**：本地 Lab 可以使用轻量替代品，但企业接口和数据职责以此基线设计。
