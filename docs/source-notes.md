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

```text
AKS + PaaS MySQL + PaaS Redis + Azure AI Search
+ Blob Storage + Key Vault + LiteLLM

Core = Test IR + Git versioning + unified execution evidence
```

确认的产品形态：

- Manus 式自然语言交互。
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

以下资料用于校正产品事实，不替代 `engprod` 中的业务约束与决策（访问日期：2026-08-20）。

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
- [Security filters for result trimming](https://learn.microsoft.com/en-us/azure/search/search-security-trimming-for-azure-search)

采用的事实：Azure AI Search 支持全文与向量混合查询、RRF 融合及查询期过滤。四索引、AST/Symbol 检索、Parent/Child 与轻量依赖图是 TAP 的应用层设计。

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
