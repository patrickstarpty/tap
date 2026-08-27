# RAG 架构索引

本目录保存 RAG Foundation 及其专项设计。

- [Phase 1：RAG Foundation](2026-08-21-foundation.md)：定义第一阶段范围、流水线、评测与验收标准。
- RAG 知识问答简图：[可编辑 draw.io 源](2026-08-27-rag-knowledge-business-flow.drawio) · [SVG 预览](2026-08-27-rag-knowledge-business-flow.svg)，以知识建设和在线问答两条主线展示数据来源、检索生成、引用回答与持续保障。
- [数据切片与端到端溯源](2026-08-21-chunking-and-provenance.md)：定义分型切片、稳定身份、revision lineage、删除与重建。
- [Azure AI Search 索引设计](2026-08-21-ai-search-index.md)：定义四类物理索引、字段、ACL、向量与蓝绿升级。
- [检索调优方案](2026-08-21-retrieval-tuning.md)：定义 BM25、Vector、Hybrid、RRF 与 Rerank 的实验阶梯。
