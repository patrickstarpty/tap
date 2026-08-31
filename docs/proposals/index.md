# 提案索引

本目录保存 RFC、待评审设计和未决输入。

- [待确认项](2026-08-20-open-questions.md)：汇总实现前仍需确认的产品、平台与运行输入。
- [RFC-001：受控 Codex Agent Runtime](2026-08-21-rfc-001-codex-agent-runtime.md)（`in-review`）：定义可选、隔离、异步的 Specialist Runtime。
- [RFC-002：TAP 文档信息架构](2026-08-22-rfc-002-document-information-architecture.md)（`implemented`）：定义六类文档目录、生命周期、命名与迁移规则。
- [RFC-003：Phase 1 应用工程结构](2026-08-23-rfc-003-phase-1-application-structure.md)（`accepted`）：定义前后端目录、Athena 嵌入边界、契约生成及 REST/SSE 工程基线。
- [RFC-004：以 Milvus 为实验默认的可替换检索后端](2026-08-24-rfc-004-provider-neutral-search-backends.md)（`draft`）：定义 Milvus 本地实验默认、Azure 可选、共同检索契约与 `doc` 纵向实验门禁。
- [RFC-005：Athena 本地知识工作区 Demo](2026-08-27-rfc-005-athena-local-knowledge-demo.md)（`implemented`）：已通过 mandatory deterministic/local-middleware 与实际手工视觉/键盘验收；定义本地上传、可恢复 ingestion、来源限定问答、可定位引用与成熟产品对标的纵向 Demo。
- [RFC-006：Athena 本地可选 Codex CLI 回答后端](2026-08-31-rfc-006-athena-local-codex-answer-backend.md)（`draft`）：拆分 Embedding/回答端口，保持百炼向量空间，并为单机 Demo 增加受限、显式选择的 Codex 回答路径与模型链路诊断。
- [RFC 模板](rfc-template.md)：提供新 RFC 的必填元数据与正文结构。
