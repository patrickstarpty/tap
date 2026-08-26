# 来源与可追溯性

## `engprod` 原始会话

三条会话属于同一个 ChatGPT 项目，正文已通过只读 thread 接口恢复并用于本次整理：

| 会话 | Thread ID | 对架构的有效输入 |
| --- | --- | --- |
| 锐评 DeepSeek Harness | `6a801f03-c340-83ec-825b-f9c55beb821a` | 插件生命周期、append-only Session Log、恢复/Fork/Replay、工具 Guard；Developer Preview、安全与企业成熟度风险 |
| BrowserStack 测试平台分析 | `6a802306-6220-83ec-b351-fc9f33a81830` | 产品定位、内网约束、Test IR、Git、执行网格、RAG、Azure 最终栈、数据 SoR、自愈/RCA |
| 架构优化建议 | `6a802f65-b08c-83ec-8c9a-059362fe3f0c` | 主 Agent + 专业子 Agent、固定 DAG + Agentic Loop、十大治理能力 |

### 恢复限制

- 本机 Codex 数据库只有标题、项目归属和 Thread ID；正文来自远端只读 thread 接口。
- 部分长 assistant message 在源接口中约 2,000 字后标记为 truncated，无法继续取得同一条消息的尾部。
- 用户历次要求、约束演进、最终技术栈、两张架构图的核心结构与主要取舍均已恢复。
- 本仓库是结构化架构基线，不是聊天记录逐字导出；无法验证的细节保留在 [待确认项](../proposals/2026-08-20-open-questions.md)。

## 从会话确认的最终主线

项目正式名称为 **TAP（Test Automation Platform）**。

2026-08-20 的后续决策确认：**第一交付阶段优先构建 RAG Foundation**，Agent 编排、Test IR 编译和测试执行闭环顺延到后续阶段。

2026-08-21 的进一步要求确认：Phase 1 基于 **Azure AI Search**，必须写清分型数据切片、端到端溯源和可重复检索调优，并交付一个参考 Codex/Claude Code 交互模式的 Knowledge Chat。这里参考的是 Project/Conversation、流式状态、中断、排队追问、资源引用和证据侧栏，不复制品牌或隐藏推理展示。

2026-08-21 的最新问题要求补充本阶段完整架构，并评估在后台嵌入 Codex CLI 作为 Agent Runtime。当前工程结论是：Phase 1 保持确定性 RAG/Chat 主链；Phase 1.5 可增加服务端 Codex SDK Adapter 作为可选、隔离的 Research/Knowledge Enrichment Specialist。Test IR/代码生成等到 Phase 2 基础契约就绪后接入。Codex 不掌握 ACL、索引发布或生产 Git，只通过 TAP Retrieval/Tool Gateway 产生候选 Artifact。

2026-08-21 的应用技术栈进一步确认为：前端 React + TypeScript，后端 Python + FastAPI/ASGI。整体架构要求同时覆盖最初的 Test Automation Platform 能力与当前 RAG 前后端，并明确 RAG 是未来平台共享的 Knowledge Plane，而非独立 Demo。工程决策是保持一个代码库/共享契约，按 API/SSE、Turn、Ingestion、Embedding、Index Writer、Agent 与 Execution 角色隔离部署。

```text
AKS + PaaS MySQL + PaaS Redis + Azure AI Search
+ Blob Storage + Key Vault + LiteLLM

Core = Test IR + Git versioning + unified execution evidence
```

原始会话确认、并由 2026-08-21 交互决策继续演进的产品形态：

- 原讨论以 Manus 为自然语言交互标杆；当前 Phase 1 明确改为参考 Codex/Claude Code 的 Project/Conversation、流式状态、中断、队列和证据交互。
- BrowserStack 式测试执行/低代码资产体验。
- Git 式可审查、可编辑、可版本化测试资产。
- 内网环境不能把 BrowserStack 或 Manus 当必需运行依赖。
- 新建测试流程与检索/复用/更新已有流程必须区分。

确认的数据职责：

| 组件 | 职责 |
| --- | --- |
| MySQL | 项目、权限、Test IR 目录/投影与版本映射、运行、自愈、RCA、审批、审计的 operational SoR |
| Git | BDD、IR、生成代码、Locator、Fixture、Hook、测试数据模板版本源 |
| Azure AI Search | 文档、代码、BDD、历史失败的可重建全文/向量混合索引 |
| Redis | Session、任务流/队列、锁、限流、短期/语义/Embedding 缓存、Worker heartbeat |
| Blob | 原始文档、App 包、trace、视频、截图、HAR、日志 |
| Key Vault | 模型、Git、数据库与执行 Provider 凭证 |
| LiteLLM | Chat/Coder/Embedding/Reranker/Vision 的统一入口与任务路由 |

## 选型演进记录

| 讨论阶段 | 当时方案 | 后续结论 |
| --- | --- | --- |
| 个人实验早期 | BrowserStack/Manus 能力组合 | 内网不能依赖外部服务，转为自建 Grid/Agent；保留能力标杆 |
| 轻量知识库 | Markdown + Git + FTS | 随文档与代码规模增长升级为 hybrid retrieval；BM25 仍保留 |
| 个人数据栈 | PostgreSQL + pgvector | 被企业现有 PaaS MySQL/Redis 覆盖 |
| Azure 候选一 | Azure PostgreSQL + pgvector | 因不新增 PostgreSQL PaaS 被覆盖 |
| Azure 候选二 | Redis/RediSearch/HNSW | Azure AI Search 可用后，Redis 回归运行态与缓存 |
| 企业最终数据层 | MySQL + Redis + AI Search + Blob + Git | 与 AKS、Key Vault、LiteLLM 共同构成最终企业栈；数据组件按 SoR、缓存、索引、对象、版本拆分 |
| 生成路径早期 | Prompt 直接生成 Framework Code | Test IR 成为中间层；生成前先检索、判断新建/复用/更新 |
| Agent 架构早期 | 主 Agent + 子 Agent | 第二张图补充 DAG/Agentic Loop 双模式和十大治理能力 |

## 官方资料校正

以下资料用于校正产品事实，不替代 `engprod` 中的业务约束与决策（访问日期：2026-08-21）。

### DeepSeek Harness

- [DeepSeek Harness repository](https://github.com/deepseek-ai/deepseek-harness)
- [Architecture](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)
- [Core subsystem](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/core.md)
- [Subagent subsystem](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/subagent.md)
- [Sandbox subsystem](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/sandbox.md)

采用的事实：Harness 是基于 Cordis 的插件化 Agent Runtime，使用持久会话事件与可替换 extension seam；官方仍将其标为 Developer Preview。由此得出的 TAP 决策是“借鉴/适配，不直接成为生产核心”。

### BrowserStack

- [Test orchestration and selection](https://www.browserstack.com/docs/automate/selenium/test-orchestration)
- [Local testing introduction](https://www.browserstack.com/docs/automate/selenium/local-testing-introduction)
- [Test Management API](https://www.browserstack.com/docs/test-management/api-reference/introduction)
- [Test Reporting & Analytics](https://www.browserstack.com/docs/test-reporting-and-analytics/quick-start)
- [Quality profiles and rules](https://www.browserstack.com/docs/test-reporting-and-analytics/quality-gate/profiles-and-rules)

采用的事实：BrowserStack 提供浏览器/设备执行、编排、报告分析与质量规则；这些能力经 Provider 适配进入 TAP，供应商对象不是 TAP 的领域主记录。

### Azure AI Search

- [Azure AI Search documentation](https://learn.microsoft.com/en-us/azure/search/)
- [Hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)
- [RRF ranking and debug subscores](https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking)
- [Vector filter modes](https://learn.microsoft.com/en-us/azure/search/vector-search-filters)
- [Index projections](https://learn.microsoft.com/en-us/azure/search/search-how-to-define-index-projections)
- [Structure-aware document chunking](https://learn.microsoft.com/en-us/azure/search/search-how-to-semantic-chunking)
- [Semantic ranker configuration](https://learn.microsoft.com/en-us/azure/search/semantic-how-to-configure)
- [Scoring profiles](https://learn.microsoft.com/en-us/azure/search/index-add-scoring-profiles)
- [Naming rules and document keys](https://learn.microsoft.com/en-us/rest/api/searchservice/naming-rules)
- [Security filters for result trimming](https://learn.microsoft.com/en-us/azure/search/search-security-trimming-for-azure-search)

采用的事实：Azure AI Search 支持全文与向量混合查询、RRF 融合、semantic rerank 及查询期过滤；`preFilter` 优先保障过滤后召回。Index Projection 属于 Indexer + Skillset 管线，不是 Push API 通用能力。四索引、typed chunking、稳定身份、AST/Symbol 检索、Parent/Child、跨索引 RRF 与轻量依赖图是 TAP 的应用层设计。

### Knowledge Chat 交互参考

- [OpenAI Projects and chats](https://learn.chatgpt.com/docs/projects)
- [Anthropic Claude Code interactive mode](https://code.claude.com/docs/en/interactive-mode)

采用的事实：Project/Conversation 可组织相关来源和独立目标；交互式编码会话支持恢复、流式进度、中断与运行中排队消息。TAP 将这些模式用于知识问答，并自行定义身份、ACL、Citation、Trace、视觉与 API；不声称复刻任一产品。

### Codex 后台 Runtime

- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- [Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex authentication](https://learn.chatgpt.com/docs/auth)
- [Agent approvals and security](https://learn.chatgpt.com/docs/agent-approvals-security)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Workload identity federation](https://learn.chatgpt.com/docs/enterprise/workload-identity)

采用的事实：Codex SDK 可在服务端启动、继续和恢复本地 coding thread，并用于 CI/CD、内部工具、工作流和应用；官方稳定 Python 包为 `openai-codex`，要求 Python 3.10+、包含 pinned Codex CLI runtime，并提供 `AsyncCodex`。`codex exec` 面向脚本与 CI；App Server 协议面向认证、会话历史、审批和流式 Agent 事件等深度集成，但其 command/WebSocket transport 当前为 experimental/unsupported for production。Codex 支持 API key 程序化认证，并明确不应把执行能力暴露在不可信或公开环境；managed ChatGPT Workspace 自动化可评估 access token 或当前为 Beta 且需 Workspace 开通/映射的 workload identity。自定义 model provider 的公开 wire protocol 是 Responses API。官方也明确 command network proxy 不覆盖 web search、connectors/plugins、MCP、Browser/Computer Use、cloud task 或 model/auth 流量，必须分别治理。

TAP 的一 Attempt 一 Pod、干净 runtime home、Tool/Artifact/Credential sidecar、Codex 不直连 AI Search、Test IR 优先、候选 patch、Git 审批、多租户隔离、LiteLLM 兼容性门禁和 Phase 1.5 分期均为本次工程设计，不是 OpenAI 产品内部架构。详细设计见 [受控 Codex Agent Runtime](../proposals/2026-08-21-rfc-001-codex-agent-runtime.md)。

### React/Python 应用运行时

- [FastAPI async and concurrency](https://fastapi.tiangolo.com/async/)
- [FastAPI deployment in containers](https://fastapi.tiangolo.com/deployment/docker/)
- [FastAPI background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [Uvicorn settings](https://www.uvicorn.org/settings/)
- [Python threading and the GIL](https://docs.python.org/3/library/threading.html)
- [React `useDeferredValue`](https://react.dev/reference/react/useDeferredValue)
- [React Profiler](https://react.dev/reference/react/Profiler)
- [Azure AI Search performance tips](https://learn.microsoft.com/en-us/azure/search/search-performance-tips)
- [Azure AI Search query monitoring](https://learn.microsoft.com/en-us/azure/search/search-monitor-queries)

采用的事实：FastAPI/ASGI 适合大量 I/O 等待的在线 API，CPU 重任务需要进程/Worker 隔离；Kubernetes 中可以一个 Uvicorn process/Pod 并由集群复制。React 提供延迟非关键更新与性能测量能力，但 TAP 的 delta 合并、增量 Markdown、规范化状态和虚拟化属于应用层设计。Azure AI Search 的查询形态、容量、延迟、QPS 和 throttling 必须持续监控。由此得出的“Python/React 足以支持既定规模、热点按 profiler 局部拆分”是 TAP 工程决策，不是厂商吞吐承诺。

### LiteLLM

- [LiteLLM repository](https://github.com/BerriAI/litellm)
- [LiteLLM documentation](https://docs.litellm.ai/docs/)
- [LiteLLM Prisma data model](https://github.com/BerriAI/litellm/blob/main/schema.prisma)
- [LiteLLM v1.76.1 custom pricing](https://github.com/BerriAI/litellm/blob/v1.76.1-stable/docs/my-website/docs/proxy/custom_pricing.md)
- [LiteLLM v1.76.1 model cost map](https://github.com/BerriAI/litellm/blob/v1.76.1-stable/model_prices_and_context_window.json)

采用的事实：LiteLLM 提供统一模型接口、Gateway、路由、预算、限流与多类模型端点；当前内建 key/team/budget/spend 持久化数据模型使用 PostgreSQL。TAP 在已确认的 MySQL/Redis 栈中先采用无状态协议/路由能力，并由 TAP MySQL 保存预算账本和审计；LiteLLM 内建持久化能力需单独 POC，不能静默引入 PostgreSQL。

2026-08-26 固定版本源码/配置资料核对：`v1.76.1-stable` 的 model config schema 虽支持独立 `custom_llm_provider`，但 2026-08-27 live execution 证明本次 pinned embedding deployment 路径没有把该字段传给 `get_llm_provider`，raw config model 因而报 provider 未指定。仓库实测可行的闭合形状是在 gateway deployment 内部使用 `openai/text-embedding-v4`；pinned provider resolver 随后剥离 routing prefix，并向 OpenAI-compatible handler 传 raw `text-embedding-v4`。这是固定组合的 live observation，不泛化到其他 LiteLLM 路径或版本。custom pricing 文档支持 `model_info.base_model` 与显式 token price override，但该 tag 的 model cost map 没有 `text-embedding-v4`。仓库不把内部 routing prefix 当作百炼 model，也不写未经同币种证明的 USD token price。

### 百炼 embedding research route

以下百炼官方资料用于 Task 9 provider 选择与真实探针门禁（访问日期：2026-08-26）。

- [文本向量同步 API](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api/)
- [文本向量模型与价格](https://help.aliyun.com/zh/model-studio/embedding?disableWebsiteRedirect=true)

官方事实：百炼提供 OpenAI-compatible embedding 调用，workspace base URL 模板以 `/compatible-mode/v1` 结尾；`text-embedding-v4` 支持请求参数 `dimensions`，可选维度包括 `1536`，默认维度为 `1024`。官方价格表以人民币列示，不能直接填入仓库的 USD cost 字段。

TAP 设计：百炼 raw provider model 仍是 `text-embedding-v4`；LiteLLM deployment 内部固定 `openai/text-embedding-v4` 只用于 provider routing，上游 transform 必须得到 raw model。runner 环境继续固定 raw model 并拒绝 caller 注入 prefix。应用侧 alias 仍为 `research-embedding-v1`，canonical model/dimension/cache/schema digest 语义不变。endpoint/key 只通过未跟踪环境或 secret store 注入；每个 embedding request 显式发送 `dimensions=1536`，并对实际 vector length、usage 与 LiteLLM cost header fail closed。真实 provider probe 尚未完成，不能把 routing 修正记为质量或计费 GREEN。

### Milvus 本地检索实验

以下固定版本官方资料用于本地 Milvus 实验的兼容性门禁与后续 transport/Compose 设计（访问日期：2026-08-25）。

- [Milvus v2.6.22 release](https://github.com/milvus-io/milvus/releases/tag/v2.6.22)
- [PyMilvus 2.6.17](https://pypi.org/project/pymilvus/2.6.17/)
- [Standalone Compose](https://milvus.io/docs/v2.6.x/install_standalone-docker-compose.md)
- [Docker prerequisites](https://milvus.io/docs/v2.6.x/prerequisite-docker.md)
- [full-text search](https://milvus.io/docs/v2.6.x/full-text-search.md)
- [language identifier](https://milvus.io/docs/v2.6.x/language-identifier.md)
- [FLAT](https://milvus.io/docs/v2.6.x/flat.md)
- [INVERTED](https://milvus.io/docs/v2.6.x/inverted.md)
- [GrantPrivilegeV2](https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Authentication/grant_privilege_v2.md)

采用的事实：Milvus `v2.6.22` release 的兼容表将其 Python SDK 列为 `2.6.17`；PyPI 将 Milvus `2.6.*` 对应到 PyMilvus `2.6.X`，且该 SDK 支持 Python 3.8+。本仓库将本地实验矩阵进一步固定为 Milvus `2.6.22`、PyMilvus `2.6.17`、etcd `3.5.25` 与 MinIO `RELEASE.2024-12-18T13-15-44Z`，并以 Python `3.13.12` 的 import/API surface 合约作为 transport 开发前的硬门禁。Compose 指南提供 `v2.6.22` 的 standalone 配置下载和启动方式；前置条件列出 standalone 至少 8 GB RAM（建议 16 GB）、Docker Desktop 的 2 vCPU/8 GB 初始内存要求，以及 MinIO `RELEASE.2024-12-18T13-15-44Z`。`FLAT` 文档说明其穷举比较、100% recall 及不需附加 index/search 参数；`GrantPrivilegeV2` 文档确认 `MilvusClient.grant_privilege_v2()` 的角色、权限与 collection 参数。全文、语言识别和 INVERTED 文档为后续 schema/index 设计的限定资料，不在本次兼容性门禁中实现其功能。

仓库实测（2026-08-26，非官方文档声明）：本地 live probe 使用 Milvus `2.6.22` + PyMilvus `2.6.17` 时，BM25 `describe_index` 结果把 `bm25_b`、`bm25_k1` 与 `inverted_index_algo` 放在顶层，没有嵌套 `params`；前两个值以 numeric strings 返回。仓库同时检查了 pinned SDK 的返回组装路径，其基础结果键为 index 的 `field_name`、`index_name` 和服务端 index 参数，以及 `total_rows`、`indexed_rows`、`pending_index_rows`、`state`。这项观察只支持 RFC-004 中对该精确版本和 BM25 identity 的闭合 transport 归一化：已知 numeric BM25 strings 严格解析为有限数值并放回唯一 canonical nested `params`；未知、重复、nested+flat 冲突以及其他 index 的 coercion 继续拒绝。

同日后续 live publish/preflight 还观察到两个独立的 pinned transport/RBAC 形状：`describe_collection` 把 canonical `content` 字段的 `params.enable_analyzer` 布尔真值返回为精确小写字符串 `"true"`；grant inventory 把 reader 的 `DescribeAlias`、`DescribeCollection` 表达为 `Global`，并把 provisioner base 的既有权限精确表达为两组——`Global/*` 的 `CreateAlias`、`CreateCollection`、`DescribeAlias`、`DropAlias`、`DropCollection`、`ManageOwnership`，以及 `Collection/*` 的 `CreateIndex`、`GetLoadState`、`GetLoadingProgress`、`IndexDetail`、`Load`、`Release`。第四次 live publish 进一步观察到：上述 `ManageOwnership` 足以完成 target grant mutation，但 provisioner 调用 `describe_role` 时得到 permission denied，错误明确要求 `PrivilegeSelectOwnership`。这些 observation 只支持两个闭合仓库契约：前者仅在该精确 field/path/value 归一化回布尔 `true`，不允许其他 string-to-bool coercion；后者只在既有精确二分上增加 `Global/*` 的 `SelectOwnership`，形成七项 `Global/*`/六项 `Collection/*`，其中 `SelectOwnership` 只供 publisher 的安全 `describe_role`/grant inventory，`ManageOwnership` 保留 grant mutation 用途。不得授予 reader/writer，也不得用 root/admin 旁路 publisher reconciliation；不得推广到其他版本、API、权限或 resource level。target-scoped reader set 仍限为 `Collection` + exact database/name 的 `Search`、`Query`，以 target 名称出现的 `Global` record 不能冒充 target grant。第四次 finalizer 后为 zero collections、aliases、concrete grants 和 active marker，base counts 为旧闭合集合的 `2/5/12`；下一次 bootstrap 必须补齐 `SelectOwnership` 并收敛到 `2/5/13`。Bootstrap base reconciliation 只拥有 `Global/*` 与 wildcard namespace；保留符合 publisher 闭合契约的 concrete reader/writer grants，对异常或所有权不明的 concrete record fail closed。这些 RBAC 所有权规则是基于仓库实测形成的安全契约，不是 Milvus 官方文档事实，也不是对其他版本、字段、权限层级或通用 transport 稳定性的推断。

## 事实、决策与补充的标识

| 类型 | 示例 | 文档处理 |
| --- | --- | --- |
| 会话确认事实 | Azure 最终栈、四索引名、DAG + Loop、十大能力 | 写入架构主线与“已确认” ADR |
| 官方产品事实 | AI Search hybrid/RRF；Harness Developer Preview | 附官方链接，避免扩大解释 |
| 本次工程补充 | MySQL Outbox、Redis 至少一次分发、Evidence Manifest 细化 | 标为“拟议”或“建议” |
| 尚缺业务输入 | Test IR v1 范围、设备数量、SLO、数据区域 | 进入待确认项，不凭空定值 |
