# 提案索引

本目录保存 RFC、待评审设计和未决输入。

- [待确认项](2026-08-20-open-questions.md)：汇总实现前仍需确认的产品、平台与运行输入。
- [RFC-001：受控 Codex Agent Runtime](2026-08-21-rfc-001-codex-agent-runtime.md)（`rejected`）：保留旧 Phase 1.5 Runtime 设计的历史记录；当前范围和安全契约由 RFC-007 重新收敛。
- [RFC-002：TAP 文档信息架构](2026-08-22-rfc-002-document-information-architecture.md)（`implemented`）：定义六类文档目录、生命周期、命名与迁移规则。
- [RFC-003：Phase 1 应用工程结构](2026-08-23-rfc-003-phase-1-application-structure.md)（`accepted`，产品范围已重排）：单仓、模块边界、契约生成和多运行角色约束继续有效；原 Azure/Knowledge Chat/Intelligence-first 阶段语义均为历史，现行范围由 RFC-009 管理。
- [RFC-004：以 Milvus 为实验默认的可替换检索后端](2026-08-24-rfc-004-provider-neutral-search-backends.md)（`withdrawn`）：保留 Milvus 本地实验事实与 provider-neutral 原则；共享/生产选择已由 RFC-009、ADR-022 和 ADR-023 取代。
- [RFC-005：Athena 本地知识工作区 Demo](2026-08-27-rfc-005-athena-local-knowledge-demo.md)（`implemented`）：已通过 mandatory deterministic/local-middleware 与实际手工视觉/键盘验收；定义本地上传、可恢复 ingestion、来源限定问答、可定位引用与成熟产品对标的纵向 Demo。
- [RFC-006：Athena 本地可选 Codex CLI 回答后端](2026-08-31-rfc-006-athena-local-codex-answer-backend.md)（`implemented`）：拆分 Embedding/回答端口，保持百炼向量空间，并为单机 Demo 交付精确 `0.149.0`、单智能体、无工具、无 fallback 的 Codex 回答路径与模型链路诊断。
- [RFC-007：Phase 1 Intelligence Layer 探索](2026-09-02-rfc-007-phase-1-intelligence-layer-exploration.md)（`withdrawn`）：独立 Intelligence Lab 路线在实施前由 RFC-009/ADR-021 取代；仅保留可恢复 Task、Artifact、Validator 与 Review Package 的历史设计参考。
- [RFC-008：TAP 产品壳层与 Low Code Automation 交互原型](2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md)（`accepted`，范围已重排）：继续作为产品壳与交互事实源；正式实施按 RFC-009 收窄为 Web-only/Jenkins-first，Mobile 与跨类型推断后移到 P1 之后。
- [RFC-009：Athena 知识与 Web 测试自动化平台设计](2026-09-04-rfc-009-athena-knowledge-web-automation-platform.md)（`accepted`）：采用 validation-first 顺序，以固定可信 Scope 先验证知识问答、Graph、Web LCA/Recorder、Playwright/Jenkins 与结果闭环，验证通过后再产品化多用户认证、RBAC 和多 Project；该状态代表设计获批，不代表目标能力已经实现。
- [RFC-010：Tapper 品牌与运行命名空间迁移](2026-09-05-rfc-010-tapper-brand-namespace-migration.md)（`accepted`）：TAP 保持平台品牌，智能工作区一次性切换到 Tapper；本地验证状态可重建，不保留旧命名兼容层。
- [RFC 模板](rfc-template.md)：提供新 RFC 的必填元数据与正文结构。
