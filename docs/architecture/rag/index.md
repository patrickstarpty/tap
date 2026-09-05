# RAG 架构索引

本目录保存当前 Knowledge-first 基线与历史 RAG 专项设计。当前规范性方向由 [RFC-009](../../proposals/2026-09-04-rfc-009-tapper-knowledge-web-automation-platform.md) 与 [ADR-021](../../decisions/2026-09-04-adr-021-knowledge-first-web-automation-delivery.md) 确定：先按 V0–VG 验证 Milvus `doc` 检索、MySQL Knowledge Graph 与可核验引用，再进入 P0 身份/RBAC/多 Project 和 P1 生产加固。Azure AI Search 四索引内容保留为 2026-08-21 的 provider-specific 历史设计，不是当前交付基线。

- [RAG Foundation](2026-08-21-foundation.md)：保存原 Azure 四索引范围、流水线、评测与验收设计，并以独立插页记录当前本地 Milvus `doc` 切片事实；现行规范仍以 RFC-009 和当前架构为准。
- RAG 知识问答简图：[可编辑 draw.io 源](2026-08-27-rag-knowledge-business-flow.drawio) · [SVG 预览](2026-08-27-rag-knowledge-business-flow.svg)，以知识建设和在线问答两条主线展示数据来源、检索生成、引用回答与持续保障。
- [数据切片与端到端溯源](2026-08-21-chunking-and-provenance.md)：当前文档 provenance 原则，以及历史四类语料切片设计。
- [Azure AI Search 索引设计](2026-08-21-ai-search-index.md)：历史/provider-specific 的四类物理索引、字段、ACL、向量与蓝绿升级方案。
- [检索调优方案](2026-08-21-retrieval-tuning.md)：可复用于当前 Milvus `doc` 路径的验证原则，以及历史 Azure BM25、Vector、RRF 与 Rerank 实验阶梯。
