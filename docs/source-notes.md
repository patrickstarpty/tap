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
- 本仓库是结构化架构基线，不是聊天记录逐字导出；无法验证的细节保留在 [待确认项](decisions.md#待确认项)。

## 从会话确认的最终主线

项目正式名称为 **TAP（Test Automation Platform）**。

2026-08-20 的后续决策确认：**第一交付阶段优先构建 RAG Foundation**，Agent 编排、Test IR 编译和测试执行闭环顺延到后续阶段。

2026-08-21 的进一步要求确认：Phase 1 基于 **Azure AI Search**，必须写清分型数据切片、端到端溯源和可重复检索调优，并交付一个参考 Codex/Claude Code 交互模式的 Knowledge Chat。这里参考的是 Project/Conversation、流式状态、中断、排队追问、资源引用和证据侧栏，不复制品牌或隐藏推理展示。

2026-08-21 的最新问题要求补充本阶段完整架构，并评估在后台嵌入 Codex CLI 作为 Agent Runtime。当前工程结论是：Phase 1 保持确定性 RAG/Chat 主链；Phase 1.5 可增加服务端 Codex SDK Adapter 作为可选、隔离的 Research/Knowledge Enrichment Specialist。Test IR/代码生成等到 Phase 2 基础契约就绪后接入。Codex 不掌握 ACL、索引发布或生产 Git，只通过 TAP Retrieval/Tool Gateway 产生候选 Artifact。

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

采用的事实：Codex SDK 可在服务端启动、继续和恢复本地 coding thread，并用于 CI/CD、内部工具、工作流和应用；`codex exec` 面向脚本与 CI；App Server 协议面向认证、会话历史、审批和流式 Agent 事件等深度集成，但其 command/WebSocket transport 当前为 experimental/unsupported for production。Codex 支持 API key 程序化认证，并明确不应把执行能力暴露在不可信或公开环境；managed ChatGPT Workspace 自动化可评估 access token 或当前为 Beta 且需 Workspace 开通/映射的 workload identity。自定义 model provider 的公开 wire protocol 是 Responses API。官方也明确 command network proxy 不覆盖 web search、connectors/plugins、MCP、Browser/Computer Use、cloud task 或 model/auth 流量，必须分别治理。

TAP 的一 Attempt 一 Pod、干净 runtime home、Tool/Artifact/Credential sidecar、Codex 不直连 AI Search、Test IR 优先、候选 patch、Git 审批、多租户隔离、LiteLLM 兼容性门禁和 Phase 1.5 分期均为本次工程设计，不是 OpenAI 产品内部架构。详细设计见 [受控 Codex Agent Runtime](codex-agent-runtime.md)。

### LiteLLM

- [LiteLLM repository](https://github.com/BerriAI/litellm)
- [LiteLLM documentation](https://docs.litellm.ai/docs/)
- [LiteLLM Prisma data model](https://github.com/BerriAI/litellm/blob/main/schema.prisma)

采用的事实：LiteLLM 提供统一模型接口、Gateway、路由、预算、限流与多类模型端点；当前内建 key/team/budget/spend 持久化数据模型使用 PostgreSQL。TAP 在已确认的 MySQL/Redis 栈中先采用无状态协议/路由能力，并由 TAP MySQL 保存预算账本和审计；LiteLLM 内建持久化能力需单独 POC，不能静默引入 PostgreSQL。

## 事实、决策与补充的标识

| 类型 | 示例 | 文档处理 |
| --- | --- | --- |
| 会话确认事实 | Azure 最终栈、四索引名、DAG + Loop、十大能力 | 写入架构主线与“已确认” ADR |
| 官方产品事实 | AI Search hybrid/RRF；Harness Developer Preview | 附官方链接，避免扩大解释 |
| 本次工程补充 | MySQL Outbox、Redis 至少一次分发、Evidence Manifest 细化 | 标为“拟议”或“建议” |
| 尚缺业务输入 | Test IR v1 范围、设备数量、SLO、数据区域 | 进入待确认项，不凭空定值 |
