---
id: ADR-005
status: accepted
date: 2026-08-20
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-005：RAG 使用四个 Azure AI Search 索引和多路检索

- **状态**：已确认。
- **决策**：使用 `kb-doc-v1`、`kb-code-v1`、`kb-bdd-v1`、`kb-failure-v1`；BM25、Vector、AST/Symbol、轻量代码—测试依赖图召回，经 RRF 与 Reranker 排序。文档用 Parent/Child 和 Document Summary/Section Summary/Leaf Chunk 多粒度索引；代码不转 Markdown。
- **原因**：文档、代码、BDD、失败的 Schema、切片、更新频率和排序信号不同；近万文档和代码库规模已超过 Markdown + FTS 的舒适范围。
- **后果**：权限字段 `tenantId/projectId/allowedGroupIds/classification/environment` 必须在检索前过滤；不建设完整通用知识图谱。
