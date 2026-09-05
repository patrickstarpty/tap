# 计划索引

本目录保存实施、交付和路线图计划。

- [TAP 交付路线图](2026-08-20-roadmap.md)（`active`）：按 RFC-009 定义 V0–VG、P0、P1 的 Validation-first、Knowledge-first、Web-only、Jenkins-first 顺序与出口。
- [文档信息架构迁移实施计划](2026-08-22-document-information-architecture-migration.md)（`completed`）：定义本次文档迁移的任务、约束与验收方式。
- [Phase 1 应用实施计划](2026-08-23-phase-1-application-implementation.md)（`cancelled`）：保留旧 Azure/RAG/Knowledge Chat 工作流的历史任务；现行实施入口是 Athena 知识与 Web 自动化平台实施计划，不得继续执行本文未完成项。
- [Milvus 本地检索实验实施计划](2026-08-24-milvus-local-search-experiment.md)（`completed`）：以本地 Docker、脱敏 `doc` fixture、provider-neutral adapter 和真实 ACL/alias 门禁验证 Milvus 路径。
- [Athena 本地知识工作区 Demo 实施计划](2026-08-27-athena-local-knowledge-demo.md)（`completed`）：已用真实持久中间件、文档上传、可恢复 ingestion、来源限定问答、可定位引用、跨应用/Compose 重启的文档与 ingestion/index 状态恢复，以及来源优先 Web 工作区完成 local-only 验收；当前页面回答不作 history 恢复，这份计划不代表 RFC-009 的 V0/V1 已完成。
- [Athena 本地 Codex 回答后端实施计划](2026-08-31-athena-local-codex-answer-backend.md)（`completed`）：已修复 Embedding/ingestion 诊断、拆分向量与回答端口，并在保留百炼跨语言向量空间的同时通过精确单智能体、无工具 Codex 真实门禁；这份计划只完成 Athena Answer Adapter，不完成 Intelligence Runtime。
- [Phase 1 Intelligence Core 实施计划](2026-09-02-phase-1-intelligence-core-implementation.md)（`cancelled`）：独立 Intelligence Lab 计划在实施前由 RFC-009/ADR-021 取消；仅保留 P1.0–P1.2 的历史任务设计参考。
- [Athena 知识与 Web 自动化平台实施计划](2026-09-04-athena-knowledge-web-automation-platform.md)（`planned`，当前）：按 TDD 分解 V0 Validation Scope、V1 Knowledge、V2 Graph、V3 Test Design、V4 Web LCA/Recorder、V5 Jenkins、VG、P0 和 P1。
- [Athena 交互原型实施计划](2026-09-02-athena-interaction-prototype.md)（`completed`）：已用页面内状态验证一级产品 Rail、Athena 上下文 Sidebar、统一聊天入口、会话历史、Agent/Skills/Library 引用、知识图谱与双语交互；产品事实源为 RFC-008，后续正式实现由当前平台计划接管。
- [Low Code Automation 交互原型实施计划](2026-09-03-low-code-automation-interaction-prototype.md)（`completed`）：以内联实施方式交付稳定资产、BDD Step/动作映射、严格 Test Plan `1:1`、共享 Run 历史、可恢复 Conversation、Web/Mobile 模拟执行和 Athena Test Plan-first 编排。
- [Athena Library、知识图谱与视觉统一实施计划](2026-09-03-athena-library-graph-visual-unification.md)（`completed`）：以内联 TDD 完成可删除消息上下文、双层 Athena `A` 标识、Codex 式 Conversation 模型选择、Library 组合筛选、Graphify 式交互图谱，以及 LCA/Test Management 视觉统一。
- [Tapper 品牌与运行命名空间迁移实施计划](2026-09-05-tapper-brand-namespace-migration.md)（`planned`）：按 clean cut 迁移 TAP/Tapper 品牌、Web/Backend/运行标识、治理文档、图表及 40 张客户截图，并以零残留与完整回归门禁收口。
