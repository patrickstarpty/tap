---
id: ADR-009
status: proposed
date: 2026-08-20
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-009：MySQL Outbox + Redis 分发，按至少一次处理

- **状态**：拟议，建议采纳。
- **决策**：状态、RunEvent、Outbox 同一 MySQL 事务；Redis Streams/Queue 负责低延迟分发。Webhook、Provider callback 和 Queue 都按至少一次处理，消费者幂等。
- **原因**：符合最终 MySQL/Redis 选型，同时避免 Redis 成为唯一审计事实。
