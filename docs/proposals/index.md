# 提案索引

本目录保存 RFC、待评审设计和未决输入。

- [待确认项](2026-08-20-open-questions.md)：汇总实现前仍需确认的产品、平台与运行输入。
- [RFC-001：受控 Codex Agent Runtime](2026-08-21-rfc-001-codex-agent-runtime.md)（`rejected`）：保留旧 Phase 1.5 Runtime 设计的历史记录；当前范围和安全契约由 RFC-007 重新收敛。
- [RFC-002：TAP 文档信息架构](2026-08-22-rfc-002-document-information-architecture.md)（`implemented`）：定义六类文档目录、生命周期、命名与迁移规则。
- [RFC-003：Phase 1 应用工程结构](2026-08-23-rfc-003-phase-1-application-structure.md)（`accepted`，产品范围已重排）：单仓、模块边界、契约生成和多运行角色约束继续有效；原 Knowledge Chat/企业 RAG 出口已由 RFC-007/ADR-019 后置。
- [RFC-004：以 Milvus 为实验默认的可替换检索后端](2026-08-24-rfc-004-provider-neutral-search-backends.md)（`draft`）：定义 Milvus 本地实验默认、Azure 可选、共同检索契约与 `doc` 纵向实验门禁。
- [RFC-005：Athena 本地知识工作区 Demo](2026-08-27-rfc-005-athena-local-knowledge-demo.md)（`implemented`）：已通过 mandatory deterministic/local-middleware 与实际手工视觉/键盘验收；定义本地上传、可恢复 ingestion、来源限定问答、可定位引用与成熟产品对标的纵向 Demo。
- [RFC-006：Athena 本地可选 Codex CLI 回答后端](2026-08-31-rfc-006-athena-local-codex-answer-backend.md)（`implemented`）：拆分 Embedding/回答端口，保持百炼向量空间，并为单机 Demo 交付精确 `0.149.0`、单智能体、无工具、无 fallback 的 Codex 回答路径与模型链路诊断。
- [RFC-007：Phase 1 Intelligence Layer 探索](2026-09-02-rfc-007-phase-1-intelligence-layer-exploration.md)（`accepted`）：先用可选资料、仓库与失败材料验证可追溯的 AI 理解、自动化设计、长期任务、候选工程和 Review Package，再建设 BrowserStack-like 测试平台主体。
- [RFC-008：TAP 产品壳层与 Low Code Automation 交互原型](2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md)（`accepted`，范围重排评审中）：记录两级产品导航、Automation 资产工作区和 Athena 编排体验；RFC-009 若被接受，将把第一阶段收窄为 Web-only/Jenkins-first，并把 Mobile 与跨类型推断后移，具体冲突清单以 RFC-009 §22.2 为准。
- [RFC-009：Athena 知识与 Web 测试自动化平台设计](2026-09-04-rfc-009-athena-knowledge-web-automation-platform.md)（`in-review`）：把可信知识问答与 Knowledge Graph 放在最前面，定义多 Project RBAC、Web LCA/Recorder、Playwright Revision、Jenkins Execution Provider 和结果闭环；当前是待书面确认的目标设计，不代表生产能力已实现。
- [RFC 模板](rfc-template.md)：提供新 RFC 的必填元数据与正文结构。
