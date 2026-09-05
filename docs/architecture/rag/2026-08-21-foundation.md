# RAG Foundation：当前 Milvus 基线与历史 Azure 参考

| 字段       | 值                                                                                                                                                                                |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 状态       | 历史/provider-specific 设计；当前规范性基线只取本页明确标为“当前”的摘要与可复用原则                                                                                               |
| 目标       | 按 V0–VG 验证可评测、可追溯、可增量更新的可信知识问答，再进入 P0 身份/RBAC/多 Project 与 P1 生产加固                                                                              |
| 核心技术   | 当前：React/TypeScript、Python + FastAPI/ASGI、MySQL、Redis、Milvus、MinIO、LiteLLM、Docker Compose；历史 provider-specific 方案：AKS、Azure AI Search、Blob、Entra ID、Key Vault |
| 主要用户面 | TAP Knowledge Chat、Retrieval API、Citation/Trace Inspector                                                                                                                       |

> **当前范围（2026-09-04）**：[RFC-009](../../proposals/2026-09-04-rfc-009-tapper-knowledge-web-automation-platform.md) 与 [ADR-021](../../decisions/2026-09-04-adr-021-knowledge-first-web-automation-delivery.md) 已替代 ADR-019 的交付优先级。当前路线 Knowledge-first，方案验证使用 TAP 管理的来源/revision、MinIO 原件与 artifact、Milvus 单一 `doc` family、MySQL Knowledge Graph 和 LiteLLM，并在 V0–VG 后依次进入 P0、P1。本文有关 Git/Blob/MySQL 四类来源、Azure AI Search 四索引、Entra 与 AKS 的内容，是 2026-08-21 接受范围的历史/provider-specific 设计，不是当前实现要求；其中稳定 revision/hash/anchor、引用、删除传播、可重建与评测原则继续适用。

> **阅读规则**：除 [3.4 节](#34-tapper-本地-doc-切片的当前已交付事实) 外，下面第 1–10 节原文记录 2026-08-21 的 Azure Knowledge Plane 方案；标题已统一标为“历史”。当前要实施的 API、Scope、Milvus/MySQL Graph、质量门禁与交付顺序只以 [RFC-009](../../proposals/2026-09-04-rfc-009-tapper-knowledge-web-automation-platform.md)、[当前架构](../2026-09-04-tapper-knowledge-web-automation-overview.md)和[当前核心契约](../../reference/2026-09-04-tapper-platform-contracts.md)为准。历史正文中的“必须”“出口”“正式”只对当时方案成立。

## 1. 历史阶段目标（2026-08-21）

后置 Knowledge Plane 阶段不追求完整 Test Automation 闭环，而是证明后续 Agent、Test IR 生成、失败 RCA 都能共享一套可靠检索底座：

- 文档、代码、BDD、失败记录按各自结构正确解析和切片。
- 四个 Azure AI Search 索引可从 Git、Blob、MySQL 权威源重建。
- 全文、向量、AST/Symbol 和轻量依赖关系能组合检索。
- 每次检索在查询前执行 tenant/project/group/classification/environment 权限过滤。
- 每个结果和生成结论都带可定位到 source revision 的引用。
- 检索质量、权限、时延、成本和数据新鲜度都有可重复评测。
- 用户能在 Project/Conversation 中流式提问、中断或排队追问，并从逐条引用打开精确证据。

## 2. 历史非目标（2026-08-21）

该 Knowledge Plane 不以以下能力作为出口条件：

- Agentic Loop、多 Agent 调度或自动任务规划。
- Test IR 编译器、低代码测试编辑器和测试代码生成。
- Browser Grid、Device Grid、BrowserStack 或 API Runner 执行。
- Self-Healing、RCA 自动闭环和代码/Locator 自动修改。
- 通用知识图谱、模型微调或跨云检索平台。
- Shell/工具执行、代码编辑、Git 写入、测试运行和通用 Agent 产品能力。

完整 Knowledge Chat 是该后置阶段的可持续 RAG 用户面和出口条件；它聚焦知识问答，不扩展为通用 Agent 或 Test Automation 工作台。

## 3. 历史 Azure Knowledge Plane 架构（2026-08-21）

```mermaid
flowchart LR
    subgraph Sources[Authoritative Sources]
      Git[Git<br/>Code / BDD / Test Assets]
      Blob[Blob<br/>Documents / Evidence]
      MySQL[(MySQL<br/>Catalog / Failure Metadata / ACL)]
    end

    subgraph AKS[AKS - RAG Foundation]
      BFF[Python FastAPI BFF / SSE]
      Detect[Change Detection]
      Fetch[Fetch + Verify Revision]
      Classify[Classify + ACL + Redact]
      Parse[Typed Parsers]
      Chunk[Typed Chunkers]
      Enrich[Summary / Symbol / Dependency Enrichment]
      Embed[Embedding Client]
      Write[Index Writer]
      API[Retrieval API]
      ContextBuilder[Conversation Context Builder]
      Query[Versioned QueryPlan<br/>Classifier / Decomposer]
      Filter[Trusted ACL Filter Builder]
      Hybrid[Parallel Hybrid Retrieval]
      Fuse[RRF / Cross-index Fusion]
      Rerank[Reranker]
      Context[Evidence Packager<br/>Parent / Graph Expansion + Context Budget]
      Cite[Citations / Retrieval Trace]
      Answer[Grounded Answer / Abstain]
      Resolver[Citation Resolver]
      TracePolicy[Restricted Trace Policy]
      TurnAPI[Turn API / Event Projection]
      TurnWorker[Turn Worker<br/>Context / Retrieval / Answer]
      Relay[Outbox Relay / Reconciler]
    end

    Web[TAP Knowledge Chat Web] -->|REST + SSE| BFF
    BFF --> TurnAPI
    TurnAPI --> ChatDB[(MySQL<br/>Chats / Turns / Queue / Snapshot<br/>Append-only Events + Outbox)]
    IngestDB[(MySQL<br/>Ingestion Ledger / Checkpoint / Outbox)]
    ChatDB --> Relay --> Redis[(Redis<br/>Distribution / Lease / Live Fanout<br/>Locks / Short Cache)]
    Redis --> TurnWorker --> API
    ChatDB --> ContextBuilder

    Git --> Detect
    Blob --> Detect
    MySQL --> Detect
    Detect --> IngestDB --> Relay
    Redis --> Fetch --> Classify --> Parse --> Chunk --> Enrich --> Embed --> Write
    Write --> Search[(Azure AI Search<br/>4 versioned indexes)]
    Search -->|Upsert ACK| IngestDB

    Embed -->|Model call| LiteLLM[Stateless LiteLLM Gateway]
    Rerank -->|Model call| LiteLLM
    Answer -->|Model call with context| LiteLLM
    KeyVault[Key Vault] --> LiteLLM
    Redis --> API
    Identity[Entra ID / Trusted Policy] --> TurnAPI
    Identity --> TurnWorker
    Identity --> API
    Identity --> Filter

    API --> ContextBuilder --> Query --> Filter --> Hybrid
    Filter --> Search
    Search --> Hybrid --> Fuse --> Rerank --> Context --> Cite
    Cite --> Answer -->|Answer deltas + citations| TurnWorker
    TurnWorker -->|coalesced event + snapshot| ChatDB
    Redis -->|live notification| BFF
    ChatDB -->|REST snapshot read + SSE event tail read| BFF
    Cite --> Resolver --> BFF
    Cite --> TracePolicy --> BFF
    BFF -->|SSE / citation response| Web
    BFF --> Resolver
```

Indexer、解析、切片、Embedding、权限元数据和删除传播均由 TAP 在 AKS 中控制。Azure AI Search 是可重建索引，不负责替代原始内容或 ACL 权威源。

专项实现设计：

- [数据切片与端到端溯源](2026-08-21-chunking-and-provenance.md)
- [Azure AI Search 索引设计](2026-08-21-ai-search-index.md)
- [检索调优方案](2026-08-21-retrieval-tuning.md)
- [TAP Knowledge Chat](../2026-08-21-knowledge-chat-ui.md)

### 3.1 在线问答的两种 AnswerMode

| AnswerMode | 用途                                          | 约束                                                                                |
| ---------- | --------------------------------------------- | ----------------------------------------------------------------------------------- |
| `quick`    | 普通知识问答、精确 ID/symbol/fingerprint 查询 | 单个有界 QueryPlan；exact/filter fast path 后执行必要的 hybrid，不启动 Agent        |
| `deep`     | 跨文档、跨索引、跨章节比较与解释              | 有界子问题、并行索引检索、跨索引融合、parent/依赖扩展与冲突检查；仍是确定性检索流程 |

每轮先由 Context Builder 组装 `Policy Context → Project Context → recent turns → versioned conversation summary → current message/@resource`，再生成不可变、可审计的 QueryPlan。`quick/deep` 是用户 AnswerMode，服务端把它映射到版本化 RetrievalProfile。历史答案和 conversation summary 只用于指代消解与连续性，不能成为事实证据；最终 claim 只能引用本轮重新授权并检索到的 source revision。

QueryPlan 至少固定：原始问题与 raw request hash、standalone query、intent/置信度、effective source families、exact identifiers、已授权并解析到 immutable revision/hash 的 `@resource`、effective environment/corpus、server-capped candidate limit、Retrieval Profile、Policy/ACL digest、子问题上限和 planner version。Context Snapshot 绑定 retrieval operation/current Policy；Chat 路径额外绑定 chat/turn，记录每层输入的 ID/hash/token 数与摘要 lineage；每轮重新授权摘要来源，不复制秘密或未经授权原文。

### 3.2 与历史 Intelligence Layer 探索的关系

Knowledge、Chat、Policy、Citation 和 Ingestion 在不启动 Intelligence Runtime 时仍必须独立工作。RFC-007 的 Intelligence Task/Artifact/Validator 是可复用的历史探索成果，但其独立 Lab 不再是当前交付出口。当前 Tapper 生成能力只能经 TAP Knowledge/Citation 边界读取快照，不能直接查询/写入 Milvus、决定 Policy、chunk identity、删除或 active corpus；派生内容仍必须经过确定性 Validator 与人工发布。历史边界见 [RFC-007](../../proposals/2026-09-02-rfc-007-phase-1-intelligence-layer-exploration.md) 和 [ADR-014](../../decisions/2026-08-21-adr-014-codex-specialist-runtime.md)，当前顺序见 RFC-009/ADR-021。

### 3.3 历史前后端与运行角色边界

| 角色                 | 历史 Azure Knowledge Plane 职责                                                                    |
| -------------------- | -------------------------------------------------------------------------------------------------- |
| React/TypeScript Web | Project/Conversation、composer/queue、SSE 增量投影、Sources/Claims/Trace；不保存权威状态或构造 ACL |
| FastAPI `api-sse`    | Entra/Policy、公共 DTO、幂等、Turn/Queue API、REST snapshot + SSE tail、Citation/Trace 重授权      |
| `turn-worker`        | Context Snapshot、QueryPlan、Retrieval、Answer、Citation validation；与浏览器连接解耦              |
| `ingestion-worker`   | fetch、typed parser/chunker、ACL/lineage、redaction；CPU/内存池与 API 分离                         |
| `embedding-worker`   | content-hash 去重、batch/cache、受治理模型调用                                                     |
| `index-writer`       | AI Search batch/upsert/tombstone、ACK 后 checkpoint、alias/corpus 发布                             |
| `relay-reconciler`   | MySQL Outbox、Redis distribution/lease、至少一次投递、幂等和故障对账                               |

首版可以共享一个 Python package 和数据库迁移，但使用独立 entrypoint/Deployment。FastAPI 在线 handler 只做异步 I/O；parser/OCR/AST、本地模型和大对象处理不在 event loop 或进程内 Background Task 运行。完整性能、容量与 AKS 边界见 [总体技术架构](../2026-08-20-overview.md#11-可靠性性能与容量)。

### 3.4 Tapper 本地 `doc` 切片的当前已交付事实

Tapper 本地 Demo 已经交付一组可被当前 Knowledge-first 路线和历史 Intelligence 探索复用、但范围严格受限的 RAG 能力：

- provider-neutral Knowledge HTTP/API、Search/Model/Citation ports 与公共 OpenAPI/TypeScript 生成链；
- PDF/DOCX/MD/TXT 文档的有界上传、稳定 revision/chunk 身份、typed parse/chunk、可恢复 job/lease/retry/delete 和 MySQL Outbox/Redis 唤醒；
- 原文件及 normalized/chunk/embedding artifact 的 private Azurite 持久化，以及本地 Milvus 单一 `doc` family 的 alias、schema/model/dimension 绑定、发布/删除/重建投影；
- fixed demo policy 下的来源限定检索、grounded answer、whole-paragraph claim spans、citation snapshot 与 revision/hash/anchor 原文解析；
- 浏览器刷新、应用进程重启和普通 Compose `down/up` 后恢复文档目录与来源可用状态、ingestion/index 状态和 citation resolver 所需的持久事实。当前渲染回答只在页面内存中，刷新会清空；本版不提供历史回答恢复，但可基于持久的 `ready` 来源重新提问。

该切片不包含 `code`、`bdd`、`failure` family，也没有 Azure AI Search 四索引、用户认证/Project Membership、Conversation/SSE/Trace/Feedback、OCR 或生产治理。它证明同一领域与端口边界可以承载本地来源优先路径；本文件其余章节是历史 Azure 设计，不是当前规范性目标，也不能用本地 deterministic E2E 或 Milvus `doc` GREEN 替代 RFC-009 的 V1/P1 出口标准。

## 4. 历史 Azure 四索引设计（2026-08-21）

### 4.1 公共字段

每个索引至少包含：

| 字段                                                                     | 用途                                              |
| ------------------------------------------------------------------------ | ------------------------------------------------- |
| `chunkId` / `logicalChunkId`                                             | 不可变 snapshot 主键 / 跨 revision 的逻辑切片身份 |
| `tenantId` / `projectId`                                                 | 强制租户与项目过滤                                |
| `allowedGroupIds`                                                        | Entra/group security trimming                     |
| `classification` / `environment` / `aclVersion`                          | 数据分级、环境边界与权限版本                      |
| `sourceType` / `sourceUri` / `sourceId` / `anchor`                       | 权威来源和可解析引用                              |
| `sourceRevision` / `sourceContentHash` / `chunkContentHash`              | 原始 snapshot 版本、切片完整性、去重和审计        |
| `rootId` / `parentId` / `chunkLevel` / `corpusVersion`                   | Parent/Child、多粒度上下文与原子语料快照          |
| `title` / `content` / `language` / `tags`                                | 全文检索与展示                                    |
| `contentVector` / `embeddingModelVersion`                                | 向量检索与模型迁移                                |
| `parserVersion` / `chunkerVersion` / `schemaVersion` / `pipelineVersion` | 可重复重建与蓝绿升级                              |
| `indexedAt` / `deleted` / `parserStatus` / `redactionStatus`             | 新鲜度、删除、对账与安全审计                      |

逻辑身份与 snapshot 身份分开计算：

```text
hashId(value) = "h_" + lowercaseHex(SHA-256(value))
logicalChunkId = hashId(tenantId | projectId | sourceId | structuralLocator | chunkKind)
chunkId = hashId(logicalChunkId | sourceRevision | chunkContentHash | chunkerVersion)
```

`chunkId` 作为 AI Search document key 只使用允许的 URL-safe 字符；URI、冒号前缀和路径不直接进入 key。

### 4.2 类型专用字段与切片

| 索引            | 切片单元                                                           | 专用字段                                                                            |
| --------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| `kb-doc-v1`     | Document Summary → Section Summary → Leaf Chunk；固定 token 仅兜底 | docType、sectionPath、page/offset、effectiveDate                                    |
| `kb-code-v1`    | Repository/File → Class/Function/Method/Symbol；代码保持原语言     | repo、commit、path、language、symbol、kind、signature、line range、imports/calls    |
| `kb-bdd-v1`     | Feature → Scenario → Step                                          | featureId、scenarioId、stableTestId、stepKeyword、tags、automationRefs              |
| `kb-failure-v1` | 一个 Incident/失败指纹一个逻辑单元，证据大文件只保留引用           | incidentId、fingerprint、testId、runId、errorType、status、evidenceRefs、resolution |

OpenAPI/AsyncAPI 内容进入 `kb-doc-v1` 或独立逻辑 source type，但必须按 Endpoint/Operation 切片，不能按固定页长切分。

## 5. 历史 Azure Ingestion Pipeline（2026-08-21）

### 5.1 标准流程

1. Git webhook/scan、Blob event/scan、MySQL Outbox 发现 revision 变化。
2. 用 `sourceUri + sourceRevision + sourceContentHash` 幂等去重。
3. 从权威源读取内容并校验 revision 未漂移。
4. 从可信策略源计算 tenant/project/group/classification/environment；权限服务不可用时 fail closed，空 ACL 不自动解释为公开。
5. 按 source type 选择 parser/chunker；结构化边界优先，token window 只兜底。
6. 生成 parent/child、summary、symbol 和轻量依赖边，并在 Embedding 前完成秘密/PII 脱敏；不同 ACL 的 child 不合并为同一个 parent summary。
7. 经无状态 LiteLLM Gateway 调用固定版本 Embedding 模型；未变化的 `chunkContentHash` 不重复 Embedding。
8. 批量 `mergeOrUpload` 到目标版本索引并保存 Ingestion Manifest。
9. 在 MySQL 更新 checkpoint、数量、hash、失败原因与 trace ID。
10. rename、删除或权限收紧时写 tombstone，并在批准的新鲜度窗口内从索引移除/更新；部分批次失败进入可重放清单，禁止静默丢失。

### 5.2 Ingestion Manifest

每次批次保存：source revision、parser/chunker/embedding/schema version、输入/输出数量、失败记录、index target、开始/结束时间、`sourceContentHash`/`chunkContentHash` 和操作者。相同输入与版本必须生成相同 chunk IDs。

### 5.3 索引升级

- Schema、chunker 或 embedding 发生不兼容变化时创建 `*-v2`，不原地混合向量空间。
- 后台全量重建并运行同一评测集。
- 四个 reader alias 逐个更新并等待传播确认，不宣称跨 alias 原子性；传播期间 turn 继续固定旧 `corpusVersion`，新索引只会产生可见 degraded/零结果，禁止放宽 filter 混合新旧数据。全部 alias 收敛后再原子发布 TAP `activeCorpusVersion` 应用层指针，并保留快速回滚窗口。
- 旧索引只在新索引质量、ACL 和数据对账通过后清理。

## 6. 历史 Azure Retrieval Pipeline（2026-08-21）

1. API 从可信身份上下文注入 tenant、project、groups、classification ceiling 和 environment；模型不能传入或放宽这些字段。Policy 将 classification ceiling 转换为明确的允许集合，不进行字符串大小比较；environment 只允许 `global OR requested environment`。
2. Context Builder 使用 Project 配置、近期 turns、带 lineage 的 conversation summary 和结构化 `@resource` 做指代消解；服务端生成版本化 QueryPlan，记录 standalone query、intent/confidence、目标索引、exact identifiers、resource mode、profile、子问题与 planner version。
3. QueryPlan 判断 doc/code/bdd/failure 单索引或多索引检索。ID、stable Test ID、symbol 和 fingerprint 查询先走 exact/filter fast path；复杂或跨章节问题再拆成有上限的子问题，禁止无界 Agentic search loop。
4. 每个目标索引执行 BM25 + Vector hybrid query；代码额外使用 symbol/path/signature 信号，BDD/Failure 使用结构化字段和 fingerprint。
5. Azure AI Search 在单索引内做 hybrid/RRF；TAP Retrieval Service 按 per-index rank 做跨索引 RRF，不直接比较不同索引的原始 score。
6. Reranker 对候选重排；记录模型、输入格式和分数，不将其与 embedding 版本混淆。
7. 根据 Parent/Child 和轻量代码—测试依赖边补充必要上下文；parent、依赖边、facet/count 和补充 chunk 均再次应用同一 ACL。
8. 去重、控制 token budget，并生成带不可变 `source revision + structured anchor + sourceContentHash + chunkContentHash + chunkId` 的 Citation；内部 URI 经 resolver 重新授权后才可打开。
9. Retrieval Trace 保存 tenant/project/actor、ACL digest、QueryPlan/Context Snapshot ID、filters、候选、分数、丢弃原因、最终 context、版本与耗时；内容按 trace retention/redaction policy 保存。

Knowledge Plane 初始实现默认不缓存检索结果；若评测后启用，cache key 必须包含 tenant、project、ACL digest、classification、environment、corpus/index/model version，撤权与删除必须同步失效。

## 7. 历史 Retrieval 与 Knowledge Chat API（2026-08-21）

后置 Knowledge Plane 至少交付：

```text
POST /v1/retrieval/search
  -> ranked chunks + citations + retrieval trace id

POST /v1/retrieval/answer
  -> answer + claim-level citations + retrieval trace id

GET /v1/retrieval/traces/{id}
  -> query plan / filters / candidates / scores / final context
```

`answer` 是 Knowledge Chat 的正式回答接口；`search` 是后续 Agent、Test Authoring 和 RCA 依赖的稳定检索契约。两者共享同一可信 Policy Context、Retrieval Profile、Trace 和 Citation 模型。

Answer 服务只能使用返回的 context；证据不足、来源冲突或 revision 不一致时必须拒答或显式提示冲突。正式请求/响应字段见 [Retrieval Contract](../../reference/2026-08-20-contracts.md#8-retrieval-contract)。

Knowledge Chat 通过 BFF 提供 Project/Conversation、流式回答、中断、排队追问、`@resource`、逐条引用和反馈。浏览器不得直连 AI Search 或 LiteLLM；公开请求不能携带 tenant/group/classification/filter。正式 Chat/SSE 契约见 [Knowledge Chat Contract](../../reference/2026-08-20-contracts.md#9-knowledge-chat-contract)，页面行为见 [TAP Knowledge Chat](../2026-08-21-knowledge-chat-ui.md)。

Retrieval Inspector 需要展示：原始 query、分解 query、脱敏 ACL digest、各检索通道 rank/score、RRF/rerank 前后顺序、parent expansion、最终 context 和引用跳转。普通用户只看 Sources/Claims；完整 Trace 仅限诊断角色。

`traceId` 只是关联标识，不能作为访问凭证。`GET /traces/{id}` 和 Inspector 每次读取都必须从当前 Entra 身份重新校验 tenant/project/actor 或受限诊断角色，并按当前 ACL fail closed；默认脱敏 query、group/filter 细节、候选内容和秘密。撤权后不得通过旧 trace、引用或 Inspector 继续读取原文，所有读取都进入审计日志。

## 8. 历史四索引评测体系（2026-08-21）

### 8.1 Golden Dataset

按四类语料建立版本化评测集，覆盖：

- 精确术语、ID、错误码和 symbol 查询。
- 同义表达和语义查询。
- 跨章节、多跳和 code → BDD/test 关联查询。
- 新建与更新已有测试资产所需的检索场景。
- 无答案、冲突版本、过期内容和引用定位。
- 跨 tenant/project/group/classification/environment 的攻击性权限用例。
- 更新、删除、权限收紧和索引重建后的新鲜度用例。

每条样例保存 query、actor/ACL、期望 source/chunk、可接受答案要点、禁止出现内容和数据集 revision。

首版冻结集建议至少包含 120 个经人工复核的问题：每个索引至少 25 个，另有至少 20 个跨索引、无答案、冲突来源或过期 revision 问题；另运行至少 1,000 个 ACL negative probes，覆盖 parent、依赖边、facet/count、缓存、删除和撤权侧漏。

### 8.2 指标

| 维度        | 指标                                                                        |
| ----------- | --------------------------------------------------------------------------- |
| Retrieval   | Recall@K、MRR、nDCG、per-index/source coverage、zero-result rate            |
| Answer      | citation precision/recall、faithfulness、unsupported claim rate、版本正确性 |
| Security    | unauthorized hit/answer count、filter bypass、trace 中敏感信息泄漏          |
| Freshness   | change/delete/ACL update 到生效的 P50/P95                                   |
| Reliability | ingestion success、重复 chunk、checkpoint recovery、rebuild parity          |
| Performance | retrieval/rerank/answer latency、tokens、embedding/query cost               |

所有结果必须分别报告四类索引，不能只用总体平均掩盖代码或失败知识的弱项。

### 8.3 后置 Knowledge Plane 出口标准

硬门槛：

- 权限对抗测试中 unauthorized retrieval/answer 为 **0**。
- 100% 最终 context 具备不可变 `source revision + structured anchor + sourceContentHash + chunkContentHash + chunkId`。
- 相同 source revision 重跑无重复 chunk；中断后可从 checkpoint 恢复。
- 四个索引都能从 Git/Blob/MySQL 重建，并与 Ingestion Manifest 对账。
- 删除、权限收紧、秘密脱敏与索引版本回滚通过演练。
- Retrieval API、Knowledge Chat 和 Inspector 可在不依赖 Agentic Loop 的情况下独立使用。
- Chat 完成 `选择 Project → 新建/恢复会话 → 流式提问 → 打开精确引用 → 反馈` 的端到端链路；支持停止、排队追问与断线续传。
- Citation、Trace 和历史会话每次按当前 ACL 重新授权，撤权后无法从旧内容读取受限证据。

建议的首轮质量目标（需用真实 Golden Dataset 校准后批准）：

- 总体 `Recall@10 ≥ 0.85`，且任一语料类型不低于 `0.75`。
- Exact ID/symbol/fingerprint 查询 Top-3 命中率为 `100%`，总体 `nDCG@10 ≥ 0.70`。
- 有答案样例的 citation precision `≥ 0.95`。
- 无答案/证据不足识别准确率 `≥ 0.90`，unsupported claim rate `≤ 0.02`。
- Hybrid + rerank 在主要指标上稳定优于 BM25-only baseline。
- 时延、新鲜度与成本先建立基线，再由产品/平台负责人批准 SLO；不以实验数据直接承诺生产值。

## 9. 历史 Azure 四索引实施顺序（2026-08-21）

以下 `P1.0`–`P1.3` 名称仅记录 2026-08-21 的历史拆分，不对应 RFC-009 当前 V0–VG、P0、P1 路线，也不构成当前 Azure AI Search 实施承诺。

### P1.0：数据契约与评测先行

- 确认 source owner、ACL owner、代表性语料和删除策略。
- 固化公共字段、四索引 v1 Schema、chunk ID、Ingestion Manifest 和 Retrieval Trace。
- 先建立 Golden Dataset 与 BM25-only baseline，避免无评测调参。

### P1.1：Typed Ingestion

- 先打通 `kb-doc-v1` 与 `kb-bdd-v1` 的端到端增量链路。
- 再加入代码 AST/Symbol parser 和 `kb-code-v1`。
- 最后接失败 Incident、证据摘要和 `kb-failure-v1`。

### P1.2：Hybrid Retrieval

- 权限过滤、BM25 + Vector、cross-index fusion、rerank。
- Parent/Child、context budget、代码—测试依赖扩展和有界多跳。
- Search/Answer API、Citation Resolver 和受限 Retrieval Inspector。

### P1.3：Knowledge Chat 与生产化

- 实现 Project/Conversation、REST + SSE、流式回答、停止、队列追问、`@resource`、Sources/Claims drawer 和反馈。
- 按角色拆 `api-sse` 与 Turn/Ingestion/Embedding/Index Writer Worker；实现 delta 合并、REST snapshot + SSE tail、慢消费者背压、分页和 React 增量渲染。
- 离线回归、Chat E2E、ACL/删除/重建演练、恶意 Markdown/citation IDOR、容量和故障注入。
- OTel trace、dashboard、告警、Runbook、成本与配额。
- 通过出口标准后冻结 Retrieval Contract，供 Phase 2 使用。

## 10. 历史 Azure 方案待确认输入（2026-08-21）

1. 四类语料各自的真实样例、规模、语言、Owner 和更新频率。
2. Entra group 与 tenant/project/classification/environment 的权威映射来源。
3. Azure AI Search SKU、区域、网络方式和容量预算。
4. Embedding/Reranker 模型、向量维度、语言覆盖和数据区域。
5. Git/Blob/MySQL 的增量变更机制及删除/权限收紧目标窗口。
6. Golden Dataset 的标注人、评审流程和每类最低样例数。
7. 哪些字段、文件类型、日志或证据禁止进入索引和模型。
