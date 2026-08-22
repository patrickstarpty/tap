# 被后续讨论覆盖的旧方案

| 早期方案 | 最终状态 |
| --- | --- |
| 直接依赖 BrowserStack Grid/Device Grid | 被内网隔离约束覆盖；改为本地 Selenium/Appium，BrowserStack 为标杆/可选 Provider |
| `Markdown + Git + FTS`，不上向量检索 | 被更大文档量、代码库和语义检索需求覆盖；FTS/BM25 保留为混合召回通道 |
| 完全不需要图关系 | 调整为不做通用知识图谱，但保留轻量代码—测试资产依赖图 |
| PostgreSQL + pgvector | 被企业现有 PaaS MySQL/Redis 技术栈覆盖 |
| Azure PostgreSQL Flexible Server + pgvector | 被“不新增 PostgreSQL PaaS”覆盖 |
| Redis/RediSearch 或 Redis HNSW 作为向量主索引 | 被 Azure AI Search 可用条件覆盖；Redis 回归运行态与缓存 |
| PostgreSQL 同时保存元数据、IR、依赖与向量 | 最终拆为 MySQL operational SoR、Git 内容版本源、AI Search 派生索引 |
| MinIO/本地文件系统作为企业对象存储 | 企业方案改为 Azure Blob；本地 Lab 仍可用兼容替代 |
| Prompt 直接生成 Framework Code | 被 Test IR 中间层、现有资产检索与 Git review 覆盖 |
| 回避产品名中的“AI” | 用户已明确撤销；可直接使用 Azure AI Search、AI Agent 等名称 |
