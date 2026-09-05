---
id: ADR-012
status: superseded
date: 2026-08-21
supersedes: []
superseded-by:
  - ADR-023
related-rfcs: []
---

# ADR-012：TAP 管切片与溯源，Azure AI Search 管索引与检索

- **状态**：历史/provider-specific 决策；已由 [ADR-023](2026-09-04-adr-023-milvus-mysql-knowledge-backend.md) 替代。TAP-managed provenance 原则由新 ADR 重述并保留。
- **决策**：TAP 在 AKS 自行完成 typed parsing/chunking、稳定 `logicalChunkId`、不可变 `chunkId`、ACL/provenance、Embedding、删除传播与 Push API 写入；Azure AI Search 负责倒排/向量索引、可信 filter、单索引 hybrid/RRF 和可选 semantic ranker。Phase 1 不用 Document Indexer/Skillset/Index Projection 写 active corpus；它们只作为后续通用 Office/PDF 文档的隔离 POC。
- **原因**：代码、BDD、Failure 和精确结构 anchor 需要应用层 typed pipeline；AI Search 的 index projection 只适用于 Indexer + Skillset，自动 projected key 也不满足 TAP 跨 revision 的稳定逻辑身份。
- **后果**：每个 chunk 重复 parent、ACL 与 lineage 字段，不依赖 query-time join；Writer 写物理索引，Reader 查 alias。Schema/chunker/embedding 不兼容升级通过新物理索引、同一 Golden Dataset 和 alias 切换完成。未来 projection POC 必须引入独立 `searchDocumentKey` 与 `chunkId` 映射，不能假设服务生成的 key 是业务身份。
