# 计划索引

本目录保存实施、交付和路线图计划。

- [TAP 交付路线图](2026-08-20-roadmap.md)（`active`）：定义从架构基线到可用 MVP 的阶段计划。
- [文档信息架构迁移实施计划](2026-08-22-document-information-architecture-migration.md)（`completed`）：定义本次文档迁移的任务、约束与验收方式。
- [Phase 1 应用实施计划](2026-08-23-phase-1-application-implementation.md)（`active`）：按可验证纵向切片交付工程工具链、RAG、Knowledge Chat、基础设施与验收门禁。
- [Milvus 本地检索实验实施计划](2026-08-24-milvus-local-search-experiment.md)（`completed`）：以本地 Docker、脱敏 `doc` fixture、provider-neutral adapter 和真实 ACL/alias 门禁验证 Milvus 路径。
- [Athena 本地知识工作区 Demo 实施计划](2026-08-27-athena-local-knowledge-demo.md)（`completed`）：已用真实持久中间件、文档上传、可恢复 ingestion、来源限定问答、可定位引用、跨应用/Compose 重启的文档与 ingestion/index 状态恢复，以及来源优先 Web 工作区完成 local-only 验收；当前页面回答不作 history 恢复，完整 Phase 1 仍为 `active`。
- [Athena 本地 Codex 回答后端实施计划](2026-08-31-athena-local-codex-answer-backend.md)（`completed`）：已修复 Embedding/ingestion 诊断、拆分向量与回答端口，并在保留百炼跨语言向量空间的同时通过精确单智能体、无工具 Codex 真实门禁；完整 Phase 1 仍为 `active`。
- [Athena 交互原型实施计划](2026-09-02-athena-interaction-prototype.md)（`active`）：先以页面内状态验证统一聊天入口、会话历史、Agent/Skills/Library 引用、知识图谱与双语交互；正式总体设计待原型确认后更新。
