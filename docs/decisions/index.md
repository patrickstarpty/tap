# TAP 架构决策

本文把 `engprod` 三条讨论中的最终选型、明确约束和本次整理补充的工程决策分开记录。除标为“已确认”的内容外，均需在实现评审中正式批准。

## 已确认的方向

- [ADR-001：平台核心采用 Test IR + Git 版本化 + 统一执行证据](2026-08-20-adr-001-platform-core-test-ir-git-evidence.md)
- [ADR-002：企业部署基线采用 Azure 现有技术栈](2026-08-20-adr-002-azure-enterprise-deployment-baseline.md)
- [ADR-003：主 Agent 同时支持固定 DAG 与 Agentic Loop](2026-08-20-adr-003-dag-and-agentic-loop.md)
- [ADR-004：数据主权按存储职责拆分](2026-08-20-adr-004-storage-responsibility-boundaries.md)
- [ADR-005：RAG 使用四个 Azure AI Search 索引和多路检索](2026-08-20-adr-005-four-azure-ai-search-indexes.md)
- [ADR-006：内网自建执行网格，BrowserStack 作为能力标杆与可选 Provider](2026-08-20-adr-006-self-hosted-execution-grid.md)
- [ADR-007：DeepSeek Harness 仅作参考或候选 Runtime](2026-08-20-adr-007-deepseek-harness-not-core-runtime.md)
- [ADR-008：确定性门禁与 Agent 建议分离](2026-08-20-adr-008-deterministic-gates-and-agent-advice.md)

## 本次整理补充的工程基线

- [ADR-009：MySQL Outbox + Redis 分发，按至少一次处理](2026-08-20-adr-009-mysql-outbox-redis-delivery.md)
- [ADR-010：控制面先保持模块化，Worker 独立扩缩](2026-08-20-adr-010-modular-control-plane-independent-workers.md)
- [ADR-011：第一交付阶段专注 RAG Foundation](2026-08-20-adr-011-phase-1-rag-foundation.md)
- [ADR-012：TAP 管切片与溯源，Azure AI Search 管索引与检索](2026-08-21-adr-012-tap-managed-chunking-and-provenance.md)
- [ADR-013：Phase 1 交付 Codex/Claude Code 式 Knowledge Chat](2026-08-21-adr-013-phase-1-knowledge-chat.md)
- [ADR-014：Codex 作为可选、隔离的 Specialist Runtime](2026-08-21-adr-014-codex-specialist-runtime.md)
- [ADR-015：前端采用 React/TypeScript，后端采用 Python/FastAPI，按运行角色隔离](2026-08-21-adr-015-react-typescript-python-fastapi.md)

## 本地 Demo 决策

- [ADR-017：Athena 本地回答端口可选 Codex CLI](2026-08-31-adr-017-athena-local-codex-answer-backend.md)（`superseded`）：保留曾接受 Ultra 内部委派的历史决策；由 ADR-018 替代。
- [ADR-018：Athena 本地 Codex 回答固定为单智能体、无工具](2026-09-01-adr-018-athena-local-codex-tool-free-answer.md)（`accepted`）：精确固定 CLI/model/catalog 能力契约，任何漂移均无 fallback 地 fail closed。

## 文档治理决策

- [ADR-016：采用文档信息架构](2026-08-22-adr-016-adopt-document-information-architecture.md)

## 历史记录与模板

- [被后续讨论覆盖的旧方案](2026-08-20-superseded-options.md)：保存迁移前决策汇总中的历史方案及最终状态。
- [ADR 模板](adr-template.md)：提供新 ADR 的必填元数据与正文结构。
