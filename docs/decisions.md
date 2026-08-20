# TAP 架构决策

本文把 `engprod` 三条讨论中的最终选型、明确约束和本次整理补充的工程决策分开记录。除标为“已确认”的内容外，均需在实现评审中正式批准。

## 已确认的方向

### ADR-001：平台核心采用 Test IR + Git 版本化 + 统一执行证据

- **状态**：已确认。
- **决策**：自然语言、BDD 和低代码编辑首先生成或修改 Test IR，再编译为 Selenium、Playwright、Appium、Cucumber 或 API/Contract 执行资产。BDD、IR、生成代码、Locator、Fixture、Hook 和数据模板进入 Git；所有执行端输出统一 Evidence Manifest。
- **原因**：避免 `Prompt → Framework Code` 的不可控生成；同时获得结构化编辑、语义 diff、跨框架编译、稳定 Test ID 和完整审计。
- **后果**：Test IR Schema、编译器、Git layout 和 migration 成为首要基础设施。

### ADR-002：企业部署基线采用 Azure 现有技术栈

- **状态**：已确认。
- **决策**：`AKS + PaaS MySQL + PaaS Redis + Azure AI Search + Blob Storage + Key Vault + LiteLLM`。
- **原因**：企业云为 Azure，现有数据库栈是 PaaS MySQL/Redis，并且 Azure AI Search 可用；无需新增 PostgreSQL PaaS 或让 Redis 承担检索主引擎。
- **后果**：本地 Lab 可以使用轻量替代品，但企业接口和数据职责以此基线设计。

### ADR-003：主 Agent 同时支持固定 DAG 与 Agentic Loop

- **状态**：已确认。
- **决策**：确定、受监管的流程走 DAG；开放、需要观察和反思的目标走 `Plan → Act → Observe → Reflect → Adjust`。检索、执行和审查子 Agent 由同一 Orchestrator 管理并统一汇总。
- **原因**：单纯多 Agent 分工无法同时满足稳定流程与动态探索。
- **后果**：Loop 内的动态动作也必须映射为类型化 Task/Attempt，不能绕过状态、权限和预算。

### ADR-004：数据主权按存储职责拆分

- **状态**：已确认方向；同步协议需细化。
- **决策**：MySQL 是项目、权限、Test IR catalog/projection、版本映射、运行、自愈、RCA、审批和审计的 operational SoR；Git 是测试内容版本源；AI Search 是可重建索引；Redis 是运行态与缓存；Blob 是原件与证据；Key Vault 是秘密存储。
- **原因**：避免 Redis/向量库/对象存储成为隐性主记录，也避免 Git 与数据库对同一字段无规则双写。
- **后果**：所有 Run 固定 Git commit；MySQL 记录 asset/revision/hash；投影和索引失败由 Reconciler 恢复。

### ADR-005：RAG 使用四个 Azure AI Search 索引和多路检索

- **状态**：已确认。
- **决策**：使用 `kb-doc-v1`、`kb-code-v1`、`kb-bdd-v1`、`kb-failure-v1`；BM25、Vector、AST/Symbol、轻量代码—测试依赖图召回，经 RRF 与 Reranker 排序。文档用 Parent/Child 和 Document Summary/Section Summary/Leaf Chunk 多粒度索引；代码不转 Markdown。
- **原因**：文档、代码、BDD、失败的 Schema、切片、更新频率和排序信号不同；近万文档和代码库规模已超过 Markdown + FTS 的舒适范围。
- **后果**：权限字段 `tenantId/projectId/allowedGroupIds/classification/environment` 必须在检索前过滤；不建设完整通用知识图谱。

### ADR-006：内网自建执行网格，BrowserStack 作为能力标杆与可选 Provider

- **状态**：已确认约束，本次补充端口形式。
- **决策**：个人 Lab/内网使用 Selenium Grid 4、Appium Device Farm 和 API/Contract Runner；企业可在 AKS/KEDA 扩展。BrowserStack 用于外部矩阵、能力对标或明确允许的场景，通过 `ExecutionProvider` 适配。
- **原因**：内网隔离环境不能把 BrowserStack/Manus 当必需运行依赖。
- **后果**：统一证据与 Test IR 不能使用 BrowserStack 私有数据模型作为核心；Local Tunnel 必须短生命周期和最小路由。

### ADR-007：DeepSeek Harness 仅作参考或候选 Runtime

- **状态**：已确认“不直接选为核心”；适配器 POC 待定。
- **决策**：借鉴 Cordis 插件生命周期、append-only Session Log、恢复/Fork/Replay、工具 Guard 与审计；不直接 fork 或嵌入平台核心。若试用，置于 `AgentRuntime` 端口之后并固定版本、隔离进程。
- **原因**：项目仍是 Developer Preview；破坏性变更、企业成熟度、安全隔离和插件/Web 控制面风险尚不足以支持核心生产依赖。可逆 effect 也不等于安全隔离。
- **后果**：LangGraph + FastAPI 只作为 Agentic Test Lab 的实验基线，企业 Runtime 选型未定；框架选择不得改变 TAP 领域契约。

### ADR-008：确定性门禁与 Agent 建议分离

- **状态**：已确认原则。
- **决策**：模型负责理解、规划、归纳、失败分析和建议；测试执行、断言、重试、权限与发布判断保持确定性。自愈只生成候选 Test IR/代码 patch，经证据验证和审批后进入 Git。
- **原因**：防止假绿、prompt injection、模型漂移和不可复现结论污染发布流程。
- **后果**：Agent Finding 必须标记来源、置信度和 evidence refs，不能覆盖原始结果。

## 本次整理补充的工程基线

### ADR-009：MySQL Outbox + Redis 分发，按至少一次处理

- **状态**：拟议，建议采纳。
- **决策**：状态、RunEvent、Outbox 同一 MySQL 事务；Redis Streams/Queue 负责低延迟分发。Webhook、Provider callback 和 Queue 都按至少一次处理，消费者幂等。
- **原因**：符合最终 MySQL/Redis 选型，同时避免 Redis 成为唯一审计事实。

### ADR-010：控制面先保持模块化，Worker 独立扩缩

- **状态**：拟议，建议采纳。
- **决策**：API/BFF、Authoring、Orchestrator、Policy、Result/RCA 可先以模块化部署单元交付；Agent、Browser、Device、API Worker 独立。按真实瓶颈再拆服务。
- **原因**：MVP 的主要风险是 Test IR、证据和可靠闭环，不是微服务数量。

### ADR-011：第一交付阶段专注 RAG Foundation

- **状态**：已确认（2026-08-20，2026-08-21 扩充用户验收面）。
- **决策**：第一阶段交付可评测、权限安全、可追溯的 RAG 垂直切片，包括四索引、分型解析/切片、增量索引、hybrid retrieval、RRF/rerank、Parent/Child、多跳检索、引用、Retrieval API/Inspector 和正式的 Knowledge Chat。
- **非目标**：Agentic Loop、Test IR 编译器、Browser/Device Grid、自愈/RCA 和自动代码修改不作为 Phase 1 出口条件。
- **原因**：检索质量、权限和数据治理是后续 Agent、测试生成与 RCA 的共同地基，应先独立验证。
- **后果**：后续组件只能通过稳定 Retrieval/Citation Contract 使用 RAG；不得在各 Agent 内重复实现私有检索链路。Chat 只做知识问答，不提前引入工具执行或测试工作台。

### ADR-012：TAP 管切片与溯源，Azure AI Search 管索引与检索

- **状态**：已确认（2026-08-21）。
- **决策**：TAP 在 AKS 自行完成 typed parsing/chunking、稳定 `logicalChunkId`、不可变 `chunkId`、ACL/provenance、Embedding、删除传播与 Push API 写入；Azure AI Search 负责倒排/向量索引、可信 filter、单索引 hybrid/RRF 和可选 semantic ranker。Phase 1 不用 Document Indexer/Skillset/Index Projection 写 active corpus；它们只作为后续通用 Office/PDF 文档的隔离 POC。
- **原因**：代码、BDD、Failure 和精确结构 anchor 需要应用层 typed pipeline；AI Search 的 index projection 只适用于 Indexer + Skillset，自动 projected key 也不满足 TAP 跨 revision 的稳定逻辑身份。
- **后果**：每个 chunk 重复 parent、ACL 与 lineage 字段，不依赖 query-time join；Writer 写物理索引，Reader 查 alias。Schema/chunker/embedding 不兼容升级通过新物理索引、同一 Golden Dataset 和 alias 切换完成。未来 projection POC 必须引入独立 `searchDocumentKey` 与 `chunkId` 映射，不能假设服务生成的 key 是业务身份。

### ADR-013：Phase 1 交付 Codex/Claude Code 式 Knowledge Chat

- **状态**：已确认（2026-08-21）。
- **决策**：交付 `TAP Knowledge Chat`，采用 Project/Conversation、流式可观察阶段、停止、排队追问、`@resource`、逐条 Citation 和 Sources/Claims/Trace 侧栏的交互模式。参考 Codex 与 Claude Code 的交互原则，不复制品牌视觉，也不展示模型隐藏思维链。
- **原因**：API/离线指标不足以验证真实用户是否能理解范围、发现降级、追查证据并反馈检索遗漏；Chat 是 Phase 1 的正式验收面。
- **后果**：浏览器只连接 BFF，不直连 AI Search/LiteLLM；公共 DTO 不接受 ACL 字段。历史答案、Citation 和 Trace 每次按当前身份重授权。Phase 3 在这一外壳上增加 Test IR editor、Run、审批，而不是重新建设另一套 Chat。

## 被后续讨论覆盖的旧方案

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

## 待确认项

1. 产品负责人和首批使用团队；英文全称已确认为 **Test Automation Platform**。
2. 个人 Agentic Test Lab 进入团队 MVP、再进入企业 AKS 的量化退出门槛分别是什么？
3. Test IR v1 首批目标编译器：Selenium、Playwright、Appium、Cucumber、API/Contract 中哪些必须同时交付？
4. Git 仓库模式：每项目独立仓库、单一资产仓库，还是业务代码同仓？
5. Agent 首期是否只能创建候选 patch，还是允许自动创建 branch/PR？审批人和权限范围是什么？
6. BrowserStack 在企业阶段是否允许访问；若允许，数据区域、Local Tunnel、并发和预算是什么？
7. Entra ID、Key Vault、Private Endpoint、模型数据区域和日志保留的组织标准。
8. MySQL/Redis 的具体 PaaS 产品与 SLA，以及 Queue/Event Stream 是否允许引入独立服务。
9. 物理 Device Farm 的设备数量、宿主系统、USB/网络拓扑和远程控制边界。
10. 质量门禁、RPO/RTO、结果收敛延迟、单 Run 成本等目标值需基线测量后审批。
11. LiteLLM 是否只采用无状态 Gateway；若需要其 Virtual Keys/预算/Admin 持久化能力，必须先验证 MySQL/Redis 兼容性，不能静默新增 PostgreSQL。
12. Knowledge Chat 最终采用组织现有 React Design System 还是独立 Next.js shell；选择不得改变 REST/SSE、Entra/BFF 与 Citation 安全契约。
