# TAP 架构决策

本文记录当前有效决策及被后续决策替代的历史。RFC-009 已在 2026-09-04 接受，当前交付、部署、知识、资产版本和执行基线以 ADR-020–025 为准。

## 当前平台基线

- [ADR-020：采用 Validation-first 交付顺序](2026-09-04-adr-020-validation-first-delivery.md)（`accepted`）：先在固定可信 Scope 中完成 V0–VG，之后才实施 P0 身份/RBAC/多 Project和 P1 生产加固。
- [ADR-021：当前交付主线采用 Knowledge-first Web Automation](2026-09-04-adr-021-knowledge-first-web-automation-delivery.md)（`accepted`）：替代 ADR-014/019 的当前交付授权；知识问答、Graph、测试设计、Web LCA/Recorder、Playwright/Jenkins 和结果闭环依次交付，隔离 Runtime 仅保留为 P1 后可重新决策的原则。
- [ADR-022：首个正式交付采用自托管 Docker Compose 基线](2026-09-04-adr-022-self-hosted-compose-delivery-baseline.md)（`accepted`）：Compose、MySQL、Redis、MinIO、Milvus、LiteLLM 与外置 Jenkins 组成当前交付拓扑。
- [ADR-023：知识后端采用 Milvus 文档投影与 MySQL Knowledge Graph](2026-09-04-adr-023-milvus-mysql-knowledge-backend.md)（`accepted`）：TAP 管 parsing/chunking/provenance，Milvus 管可重建 `doc` 投影，MySQL 管 Graph 事实与证据。
- [ADR-024：Automation Revision 由 TAP 管理](2026-09-04-adr-024-tap-managed-automation-revisions.md)（`accepted`）：替代 ADR-001/004/008；MySQL 管权威资产/Revision，MinIO 管 Bundle/Evidence，Git 是可选同步 Adapter；Test IR、统一 Evidence 与确定性门禁保留。
- [ADR-025：Jenkins 作为首个 Execution Provider](2026-09-04-adr-025-jenkins-first-execution-provider.md)（`accepted`）：provider-neutral 核心之后先接 Jenkins/Playwright Web，Mobile、Azure DevOps 与 BrowserStack 后置。

## 仍有效的早期方向

- [ADR-003：主 Agent 同时支持固定 DAG 与 Agentic Loop](2026-08-20-adr-003-dag-and-agentic-loop.md)
- [ADR-007：DeepSeek Harness 仅作参考或候选 Runtime](2026-08-20-adr-007-deepseek-harness-not-core-runtime.md)

## 本次整理补充的工程基线

- [ADR-009：MySQL Outbox + Redis 分发，按至少一次处理](2026-08-20-adr-009-mysql-outbox-redis-delivery.md)（`accepted`）：状态与 Outbox 同事务，Redis 只作至少一次低延迟分发。
- [ADR-010：控制面先保持模块化，Worker 独立扩缩](2026-08-20-adr-010-modular-control-plane-independent-workers.md)（`accepted`）：控制面保持模块化，耗时和隔离工作负载使用独立 Worker。
- [ADR-011：第一交付阶段专注 RAG Foundation](2026-08-20-adr-011-phase-1-rag-foundation.md)（`superseded`）：保留 RAG 先行的历史决策；由 ADR-019 替代。
- [ADR-013：Phase 1 交付 Codex/Claude Code 式 Knowledge Chat](2026-08-21-adr-013-phase-1-knowledge-chat.md)（`superseded`）：保留 Knowledge Chat 优先级的历史决策；由 ADR-019 替代。
- [ADR-015：前端采用 React/TypeScript，后端采用 Python/FastAPI，按运行角色隔离](2026-08-21-adr-015-react-typescript-python-fastapi.md)

## 本地 Demo 决策

- [ADR-017：Athena 本地回答端口可选 Codex CLI](2026-08-31-adr-017-athena-local-codex-answer-backend.md)（`superseded`）：保留曾接受 Ultra 内部委派的历史决策；由 ADR-018 替代。
- [ADR-018：Athena 本地 Codex 回答固定为单智能体、无工具](2026-09-01-adr-018-athena-local-codex-tool-free-answer.md)（`accepted`）：精确固定 CLI/model/catalog 能力契约，任何漂移均无 fallback 地 fail closed。

## 被替代的历史基线

- [ADR-001：平台核心采用 Test IR + Git 版本化 + 统一执行证据](2026-08-20-adr-001-platform-core-test-ir-git-evidence.md)（`superseded`）：由 ADR-024 替代；Test IR 与统一 Evidence 被重述并保留，Git 强制事实源被取消。
- [ADR-002：企业部署基线采用 Azure 现有技术栈](2026-08-20-adr-002-azure-enterprise-deployment-baseline.md)（`superseded`）：由 ADR-022 替代。
- [ADR-004：数据主权按存储职责拆分](2026-08-20-adr-004-storage-responsibility-boundaries.md)（`superseded`）：由 ADR-024 替代并重述仍有效的数据职责。
- [ADR-005：RAG 使用四个 Azure AI Search 索引和多路检索](2026-08-20-adr-005-four-azure-ai-search-indexes.md)（`superseded`）：由 ADR-023 替代。
- [ADR-006：内网自建执行网格，BrowserStack 作为能力标杆与可选 Provider](2026-08-20-adr-006-self-hosted-execution-grid.md)（`superseded`）：由 ADR-025 替代；provider-neutral 和统一证据原则被重述并保留。
- [ADR-008：确定性门禁与 Agent 建议分离](2026-08-20-adr-008-deterministic-gates-and-agent-advice.md)（`superseded`）：由 ADR-024 替代；确定性门禁、Agent provenance/evidence 原则被重述并保留，Git-required 去向被取消。
- [ADR-012：TAP 管切片与溯源，Azure AI Search 管索引与检索](2026-08-21-adr-012-tap-managed-chunking-and-provenance.md)（`superseded`）：由 ADR-023 替代；TAP 管切片与溯源原则被重述并保留。
- [ADR-014：Codex 作为可选、隔离的 Specialist Runtime](2026-08-21-adr-014-codex-specialist-runtime.md)（`superseded`）：由 ADR-021 替代；未来可选隔离 Runtime 原则保留，旧 P1.2/P1.3 授权取消。
- [ADR-019：Phase 1 优先探索 Intelligence Layer](2026-09-02-adr-019-phase-1-intelligence-layer-exploration.md)（`superseded`）：由 ADR-021 替代；durable task、artifact 和 validator 模式继续作为实现参考。

## 文档治理决策

- [ADR-016：采用文档信息架构](2026-08-22-adr-016-adopt-document-information-architecture.md)

## 历史记录与模板

- [被后续讨论覆盖的旧方案](2026-08-20-superseded-options.md)：保存迁移前决策汇总中的历史方案及最终状态。
- [ADR 模板](adr-template.md)：提供新 ADR 的必填元数据与正文结构。
