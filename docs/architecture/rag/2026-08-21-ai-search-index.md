# 后置 Knowledge Plane：Azure AI Search 索引设计

本文记录 2026-08-21 将 TAP 四个逻辑知识域映射为 Azure AI Search 索引、别名、字段属性、向量与 semantic configuration 的 provider-specific 历史方案；它不是当前架构契约基线。

> **当前范围（2026-09-04）**：[RFC-009](../../proposals/2026-09-04-rfc-009-athena-knowledge-web-automation-platform.md) 与 [ADR-021](../../decisions/2026-09-04-adr-021-knowledge-first-web-automation-delivery.md) 采用 Milvus `doc` 检索与 MySQL Knowledge Graph，并按 V0–VG、P0、P1 推进。本文正文中的 “Phase 1”、Azure AI Search、Entra、AKS 和四索引均指 2026-08-21 历史设计，不能描述为当前目标或已实现能力。可继续复用的原则限于 TAP 掌握稳定身份、provenance、Policy filter、manifest、删除传播、可重建与版本化评测；具体 Azure Schema/API 不自动约束当前 Milvus 实现。

## 1. 实现边界

| 能力     | TAP 负责                                                       | Azure AI Search 负责                                       |
| -------- | -------------------------------------------------------------- | ---------------------------------------------------------- |
| 数据理解 | source adapter、typed parser、结构化 chunk、稳定 ID            | Phase 1 不承担解析；indexer/skillset 仅为后续隔离 POC      |
| 安全     | Entra 身份解析、ACL Policy、脱敏、可信 filter 编译             | 在 filterable 字段上执行 query-time security filter        |
| 向量     | 选择模型、Embedding 版本/维度、迁移                            | 保存向量、HNSW/eKNN 查询、压缩能力                         |
| 排序     | query classification、exact path、跨索引融合、context/citation | 单索引 BM25、vector、RRF、semantic ranker、scoring profile |
| 生命周期 | manifest、删除传播、对账、corpus 发布                          | 批量 upsert/delete、物理索引、alias                        |
| 溯源     | source/chunk/index/retrieval/answer 账本                       | 返回被索引的 provenance 字段与 score/subscore              |

Phase 1 四个 active index 全部使用 TAP Push API。PDF/DOCX/PPTX 可以调用 Document Intelligence/Content Understanding 做结构提取，但由 TAP 生成 `ChunkEnvelope` 并发布。未来若评估 indexer + skillset + index projection，必须写入隔离物理索引；由于 projection document key 由 AI Search 生成，需新增独立 `searchDocumentKey` 并保存它到业务 `chunkId` 的映射，不能直接复用本页 active-index Schema。

## 2. 索引与别名

| Family  | Reader Alias        | 物理索引示例             | 内容                                                  |
| ------- | ------------------- | ------------------------ | ----------------------------------------------------- |
| doc     | `kb-doc-active`     | `kb-doc-v1-20260821`     | 文档 leaf/section/document summary、OpenAPI operation |
| code    | `kb-code-active`    | `kb-code-v1-20260821`    | source symbol、AST chunk、一跳依赖元数据              |
| bdd     | `kb-bdd-active`     | `kb-bdd-v1-20260821`     | Feature/Scenario/Step 与 Test IR ref                  |
| failure | `kb-failure-active` | `kb-failure-v1-20260821` | Incident、fingerprint、resolution 与 evidence refs    |

规则：

- Reader 只查询 alias，Writer 明确写物理索引。
- Indexer 的 target 使用物理索引，不使用 alias。
- 不兼容 Schema、analyzer、向量维度或 chunker 变化时新建物理索引。
- 新索引完成全量重建、ACL/hash 对账、离线评测与灰度双读后，逐个更新 family alias 并等待传播确认；Azure 不提供四个 alias 的跨 alias 原子事务。
- 旧索引只保留批准的回滚窗口，不能成为历史越权查询入口。
- 使用组织批准的稳定 REST/SDK 版本；任何 Preview 参数必须 feature flag 化，并有 GA fallback。

## 3. 公共字段

布尔列表示推荐的 Azure AI Search field attribution；`—` 表示关闭。

| 字段                                                   | Azure 类型               | key | searchable | filterable | sortable | facetable | retrievable | 说明                                                                          |
| ------------------------------------------------------ | ------------------------ | --- | ---------- | ---------- | -------- | --------- | ----------- | ----------------------------------------------------------------------------- |
| `chunkId`                                              | `Edm.String`             | ✓   | —          | ✓          | —        | —         | ✓           | 不可变 snapshot key；`h_` + SHA-256 lowercase hex，符合 document-key 字符规则 |
| `logicalChunkId`                                       | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | 跨 revision 的逻辑身份                                                        |
| `rootId` / `parentId`                                  | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | Parent/Child 回填                                                             |
| `tenantId` / `projectId`                               | `Edm.String`             | —   | —          | ✓          | —        | —         | —           | 服务端 security filter；不返回浏览器                                          |
| `allowedGroupIds`                                      | `Collection(Edm.String)` | —   | —          | ✓          | —        | —         | —           | group security trimming                                                       |
| `classification` / `environment`                       | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | 数据分级与环境范围                                                            |
| `aclVersion`                                           | `Edm.Int64`              | —   | —          | ✓          | —        | —         | —           | 撤权、缓存与审计                                                              |
| `sourceId` / `sourceType`                              | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | 逻辑来源                                                                      |
| `sourceUri` / `sourceRevision`                         | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | 仅后端 Citation Resolver 使用                                                 |
| `anchorKind`                                           | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | page/heading/line/symbol/BDD/incident                                         |
| `anchorJson`                                           | `Edm.String`             | —   | —          | —          | —        | —         | ✓           | 结构化位置序列化值                                                            |
| `sourceContentHash` / `chunkContentHash`               | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | 原始 snapshot / 当前 chunk 的完整性与对账                                     |
| `contentRole`                                          | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | `source` 或 `generated_summary`                                               |
| `derivedFromChunkIds`                                  | `Collection(Edm.String)` | —   | —          | ✓          | —        | —         | ✓           | 摘要回链                                                                      |
| `chunkKind` / `chunkLevel`                             | `Edm.String`             | —   | —          | ✓          | —        | ✓         | ✓           | function/scenario/leaf/section 等                                             |
| `title`                                                | `Edm.String`             | —   | ✓          | —          | —        | —         | ✓           | semantic title 候选                                                           |
| `content`                                              | `Edm.String`             | —   | ✓          | —          | —        | —         | ✓           | 可引用的脱敏文本                                                              |
| `embeddingTextHash`                                    | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | embedding 输入审计；默认不存完整输入副本                                      |
| `contentVector`                                        | `Collection(Edm.Single)` | —   | ✓          | —          | —        | —         | —           | 维度绑定 embedding model，绝不返回前端                                        |
| `language`                                             | `Edm.String`             | —   | —          | ✓          | —        | ✓         | ✓           | analyzer/query routing                                                        |
| `tags`                                                 | `Collection(Edm.String)` | —   | ✓          | ✓          | —        | ✓         | ✓           | 关键词与筛选                                                                  |
| `corpusVersion`                                        | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | active corpus 与 trace                                                        |
| `parserVersion` / `chunkerVersion` / `pipelineVersion` | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | 可重建性                                                                      |
| `embeddingModelVersion`                                | `Edm.String`             | —   | —          | ✓          | —        | —         | ✓           | 向量空间版本                                                                  |
| `indexedAt` / `sourceUpdatedAt`                        | `Edm.DateTimeOffset`     | —   | —          | ✓          | ✓        | —         | ✓           | 新鲜度与 scoring profile                                                      |

权限字段默认 `retrievable: false`。即使后端需要诊断，也从 Policy/Trace 账本读取脱敏值，不把原始 group IDs 放进普通 Search response。

## 4. Family 专用字段

### 4.1 `kb-doc-*`

| 字段                                | 类型与属性                                      | 用途                                 |
| ----------------------------------- | ----------------------------------------------- | ------------------------------------ |
| `documentId`                        | String, filterable/retrievable                  | 文档逻辑 ID                          |
| `docType`                           | String, filterable/facetable/retrievable        | requirement/design/manual/openapi 等 |
| `sectionPath`                       | Collection(String), searchable/retrievable      | heading breadcrumb                   |
| `pageStart/pageEnd`                 | Int32, filterable/retrievable                   | PDF/Office 定位                      |
| `offsetStart/offsetEnd`             | Int64, retrievable                              | 文本原件定位                         |
| `effectiveDate`                     | DateTimeOffset, filterable/sortable/retrievable | 版本与时效                           |
| `summaryModelVersion/promptVersion` | String, filterable/retrievable                  | 生成摘要 provenance                  |

### 4.2 `kb-code-*`

| 字段                               | 类型与属性                                 | 用途                                        |
| ---------------------------------- | ------------------------------------------ | ------------------------------------------- |
| `repositoryId` / `commitSha`       | String, filterable/retrievable             | Git revision                                |
| `path`                             | String, searchable/filterable/retrievable  | 文件定位                                    |
| `symbolId` / `symbolFqn`           | String, searchable/filterable/retrievable  | exact/symbol recall                         |
| `symbolKind` / `signature`         | String, searchable/filterable/retrievable  | 类型与签名                                  |
| `lineStart/lineEnd`                | Int32, filterable/retrievable              | 精确引用                                    |
| `callerIds/calleeIds/testAssetIds` | Collection(String), filterable/retrievable | 一跳关系扩展，仍需 ACL                      |
| `codeSummary`                      | String, searchable/retrievable             | 可选自然语言 rerank 输入；不是源码 citation |

### 4.3 `kb-bdd-*`

| 字段                                        | 类型与属性                                | 用途                  |
| ------------------------------------------- | ----------------------------------------- | --------------------- |
| `featureId` / `scenarioId` / `stableTestId` | String, searchable/filterable/retrievable | exact 与资产关联      |
| `featureTitle` / `scenarioTitle`            | String, searchable/retrievable            | 语义与 BM25           |
| `stepKeyword` / `stepOrder`                 | String / Int32, filterable/retrievable    | Step 定位与顺序       |
| `examplesJson`                              | String, retrievable                       | Scenario Outline 示例 |
| `testIrAssetId/testIrRevision`              | String, filterable/retrievable            | 后续 Test IR 连接     |

### 4.4 `kb-failure-*`

| 字段                                  | 类型与属性                                          | 用途                             |
| ------------------------------------- | --------------------------------------------------- | -------------------------------- |
| `incidentId` / `fingerprint`          | String, searchable/filterable/retrievable           | exact/fingerprint recall         |
| `runId` / `attemptId` / `testAssetId` | String, filterable/retrievable                      | 运行定位                         |
| `errorType` / `status`                | String, searchable/filterable/facetable/retrievable | 分类与筛选                       |
| `environmentMatrixJson`               | String, retrievable                                 | 浏览器/设备/环境摘要             |
| `symptom` / `resolution`              | String, searchable/retrievable                      | 语义检索与 semantic ranker       |
| `evidenceRefsJson`                    | String, retrievable                                 | Blob refs + hash；不含原始大日志 |
| `firstSeenAt/lastSeenAt`              | DateTimeOffset, filterable/sortable/retrievable     | 新鲜度与趋势                     |

## 5. Analyzer 与 exact 字段

- ID、hash、commit、path、FQN、fingerprint 使用 filterable keyword 字段；不要指望语言 analyzer 保持其精确性。
- 人类语言字段选择 analyzer 前必须用 Analyze Text API 对真实中英文语料验证。混合语言首期可使用统一 baseline，并按 `language` 路由到可选的 `contentZh/contentEn` 字段实验。
- 代码字段把 exact symbol/path 与自然语言 `codeSummary` 分开；不可让 analyzer 改写原始 source。
- 同义词只用于经领域负责人审查的自然语言术语，不能把 Test ID、错误码或 API path 加入模糊同义扩展。
- analyzer、synonym map 或字段拆分属于 `retrievalProfileVersion`/schema 变更，必须跑相同评测集。

## 6. Vector 配置

- `contentVector.dimensions` 与 `embeddingModelVersion` 一一绑定。
- Azure OpenAI embedding 使用 cosine 时，vector profile 选择 cosine；其他模型按其官方指标配置。
- HNSW 是在线基线；评测抽样使用 `exhaustive: true` 生成近邻 ground truth，再测 HNSW Recall。
- `m`、`efConstruction`、`efSearch`、压缩、oversampling 与 rescoring 全部进入版本化 Vector Profile；不以默认值冒充已调优值。
- 向量压缩只在质量基线稳定后实验；任何索引节省都不能绕过 Recall 和 citation 回归。
- Query 与 indexing 必须使用相同 embedding space；维度/模型不兼容时新建索引，不原地混用。

## 7. Semantic Configuration

每个 family 独立配置，并通过评测决定是否默认启用：

| Family  | Title           | Prioritized keywords             | Prioritized content          | 默认策略                           |
| ------- | --------------- | -------------------------------- | ---------------------------- | ---------------------------------- |
| doc     | `title`         | tags、sectionPath                | content、经验证的 summary    | 默认实验开启                       |
| code    | `symbolFqn`     | path、language、signature        | codeSummary、content         | 默认关闭，先走 exact/symbol/hybrid |
| bdd     | `scenarioTitle` | featureTitle、tags、stableTestId | content                      | 默认实验开启                       |
| failure | `errorType`     | fingerprint、testAssetId、tags   | symptom、resolution、content | 默认实验开启                       |

Semantic ranker 只处理可检索文本，并重排已有候选，不替代初始召回。若启用，候选字段按优先级排列；代码、短 ID、fingerprint query 可以按 query class 绕过。

## 8. Security Filter

前端不传 tenant、group 或 classification。BFF 从 Entra 身份构造内部 Policy Context，Retrieval Service 编译不可放宽的 OData filter；下面的值均来自服务端并经过 OData escaping：

```text
tenantId eq '<trustedTenant>'
and projectId eq '<trustedProject>'
and allowedGroupIds/any(g: search.in(g, '<trustedGroupIds>'))
and search.in(classification, '<trustedClassificationSet>')
and search.in(environment, 'global,<authorizedRequestedEnvironment>')
and corpusVersion eq '<activeCorpus>'
```

- vector query 显式使用 `vectorFilterMode: preFilter`。
- 高选择性 filter 必须压测；必要时只对已授权候选使用 exhaustive KNN。
- Phase 1 禁止客户端/模型设置 vector-level `filterOverride`，因为它可能覆盖顶层 security filter。
- Scoring profile、semantic ranker、facet 与 result trimming 都不能替代授权。
- Parent、依赖边、summary、trace、citation resolver 和历史聊天打开时再次鉴权。

## 9. 查询模板

单索引 natural-language baseline：

```json
{
  "search": "<normalized query>",
  "searchFields": "title,content,tags",
  "filter": "<server-compiled security filter>",
  "vectorFilterMode": "preFilter",
  "vectorQueries": [
    {
      "kind": "vector",
      "vector": ["<server generated>"],
      "fields": "contentVector",
      "k": 50,
      "weight": 1.0
    }
  ],
  "queryType": "semantic",
  "semanticConfiguration": "tap-doc-semantic-v1",
  "select": "chunkId,logicalChunkId,parentId,title,content,sourceId,sourceRevision,anchorJson,sourceContentHash,chunkContentHash",
  "top": 10
}
```

这只是 profile 化模板，不由浏览器直接发送给 AI Search。Exact ID/symbol/fingerprint query 使用 exact/filter fast path；跨索引请求分别查询 alias，再由 TAP 按 per-index rank 做 RRF。

## 10. 迁移与验收

迁移流程：新物理索引 → 全量重建 → manifest/ACL/hash 对账 → 离线评测 → 灰度双读 → 逐 family 更新 alias → 至少等待官方建议的传播窗口并主动确认 → 原子发布 TAP `activeCorpusVersion` 指针 → 回滚窗口。

四个 alias 不能一起原子切换。传播期间应用层继续使用旧 `activeCorpusVersion`，每个 turn 固化该值，所有查询仍强制 `corpusVersion` filter；已经指向新索引的 family 因版本不匹配只能返回零结果并标记 degraded，绝不能放宽 filter 混入新旧 corpus。只有四个 alias 都确认收敛后，TAP 才原子发布新 corpus pointer。

必须验证：

- 物理索引和 alias 与 trace 中记录一致。
- 不兼容字段或向量变化不会混入 active corpus。
- 四个 family 的 filterable/retrievable 属性通过自动化 Schema test。
- 普通 Search response 不包含 group IDs、vector 或未授权 source URI。
- Writer 不会误向 alias、旧索引或错误 corpus 写入。
- Preview 功能关闭后，GA baseline 仍可完成检索、引用和 Chat 回答。

## 11. 官方依据

- [Azure AI Search hybrid search](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)
- [Vector query filters](https://learn.microsoft.com/en-us/azure/search/vector-search-filters)
- [Configure semantic ranker](https://learn.microsoft.com/en-us/azure/search/semantic-how-to-configure)
- [Scoring profiles](https://learn.microsoft.com/en-us/azure/search/index-add-scoring-profiles)
- [Index aliases](https://learn.microsoft.com/en-us/azure/search/search-how-to-alias)
- [Security trimming pattern](https://learn.microsoft.com/en-us/azure/search/search-security-trimming-for-azure-search)
- [Azure AI Search naming rules](https://learn.microsoft.com/en-us/rest/api/searchservice/naming-rules)
