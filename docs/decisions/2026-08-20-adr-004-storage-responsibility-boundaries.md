---
id: ADR-004
status: accepted
date: 2026-08-20
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-004：数据主权按存储职责拆分

- **状态**：已确认方向；同步协议需细化。
- **决策**：MySQL 是项目、权限、Test IR catalog/projection、版本映射、运行、自愈、RCA、审批和审计的 operational SoR；Git 是测试内容版本源；AI Search 是可重建索引；Redis 是运行态与缓存；Blob 是原件与证据；Key Vault 是秘密存储。
- **原因**：避免 Redis/向量库/对象存储成为隐性主记录，也避免 Git 与数据库对同一字段无规则双写。
- **后果**：所有 Run 固定 Git commit；MySQL 记录 asset/revision/hash；投影和索引失败由 Reconciler 恢复。
