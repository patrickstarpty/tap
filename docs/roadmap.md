# TAP 交付路线图

TAP 采用 **RAG 先行** 的交付顺序：第一阶段只构建可独立验收的知识检索底座；Test IR、Agent 编排、测试执行和智能增强在 Retrieval Contract 稳定后逐步接入。每阶段用可重复的验收证据退出，不以日期代替完成标准。

Phase 1 的完整范围、数据契约和评测方法见 [Phase 1：RAG Foundation](rag-phase-1.md)。

## 准备阶段：RAG 输入与评测契约

### 交付

- 选定文档、代码、BDD、失败记录四类代表性语料，明确 Owner、规模、语言、更新频率和删除策略。
- 明确 tenant/project/group/classification/environment 的权威来源和权限映射。
- 固化四索引 v1 Schema、稳定 `logicalChunkId`、不可变 snapshot `chunkId`、Ingestion Manifest、Retrieval Trace 和引用格式。
- 建立覆盖精确、语义、跨章节、代码—测试关联、无答案和权限攻击的 Golden Dataset。
- 建立 BM25-only baseline，确认 Azure AI Search、Embedding/Reranker、网络和 Key Vault 接入可行。

### 出口标准

- 每类语料均有可合法使用的样例和标注负责人。
- ACL、删除、数据分级和模型可见范围均有书面规则。
- Golden Dataset、基线结果和评测脚本可版本化、可重复运行。

## Phase 1：RAG Foundation（当前重点）

### 技术基线

- Azure AI Search 四索引：`kb-doc-v1`、`kb-code-v1`、`kb-bdd-v1`、`kb-failure-v1`。
- TAP AKS 服务负责变更发现、读取、权限元数据、脱敏、typed parsing/chunking、enrichment、Embedding、索引写入和删除传播。
- MySQL 保存 catalog、ACL、ingestion ledger/checkpoint 和审计；Redis 只用于锁、限流和短期缓存；Blob 保存原始文档与证据。
- LiteLLM 采用无状态路由；Embedding/Reranker 模型与输入、版本和策略均可追溯，密钥由 Key Vault 管理。
- Retrieval API 与 Inspector 作为独立交付面，不依赖 Agentic Loop 或测试执行网格。

### 交付

- 结构化切片优先：文档使用 Document/Section/Leaf，代码使用 AST/Symbol，BDD 使用 Feature/Scenario/Step，失败记录使用 Incident；固定 token 仅兜底。
- 可幂等、可恢复、可删除、可全量重建的增量 ingestion pipeline，并支持索引蓝绿升级和回滚。
- BM25 + Vector hybrid retrieval、跨索引融合、RRF/rerank、Parent/Child 上下文恢复、轻量依赖扩展和有界多跳检索。
- 检索前强制 `tenantId/projectId/allowedGroupIds/classification/environment` security trimming。
- Search API、带 claim-level citation 的 Answer 薄层、Retrieval Trace 和 Inspector。
- 四类语料分别报告 Recall/MRR/nDCG、引用正确性、权限、新鲜度、可靠性、时延和成本。

### 出口标准

- 权限对抗测试中的 unauthorized retrieval/answer 为 **0**。
- 100% 最终 context 都能定位到 `sourceUri + sourceRevision + anchor + chunkId`。
- 相同 revision 重放不产生重复 chunk；任务中断可从 checkpoint 恢复。
- 四索引均可从 Git/Blob/MySQL 权威源重建，并与 Ingestion Manifest 对账。
- 删除、权限收紧、秘密脱敏和索引版本回滚演练通过。
- Hybrid + rerank 稳定优于 BM25 baseline；质量目标经真实 Golden Dataset 校准并批准。
- Retrieval API 在没有 Agent、Test IR 和 Grid 的情况下可独立使用，契约冻结后供 Phase 2 消费。

### 明确非目标

- Agentic Loop、多 Agent 调度和自动任务规划。
- Test IR 编译器、低代码编辑器和测试代码生成。
- Browser/Device Grid、BrowserStack Adapter 和 API Runner。
- Self-Healing、RCA 自动闭环、自动代码或 Locator 修改。

## Phase 2：Test IR 与 Agentic Test Lab

### 交付

- 定义 Test IR v1、stable Test ID、Git layout、schema migration 和语义 diff。
- 固化 Run/Task/Attempt、Evidence Manifest、Finding、Approval 与幂等契约。
- LangGraph + FastAPI：主 Agent、检索/执行/审查子 Agent，支持固定 DAG 与受控 Agentic Loop。
- 通过 Phase 1 Retrieval API 检索、复用和更新已有测试资产，不在 Agent 内复制私有检索链路。
- Ollama 起步并通过 LiteLLM 保持模型契约；高并发时再评估 vLLM。
- Selenium Grid 4 + Docker、Allure + OpenTelemetry + Jaeger；Web 闭环稳定后再接 Appium Device Farm。
- Healenium/自研算法只生成自愈候选，所有修改经过隔离验证和人工审批。

### 出口标准

- 自然语言/BDD → 检索已有资产 → Draft Test IR → Git diff → Browser 执行 → 统一证据 → RCA/自愈建议 → 人工确认完整跑通。
- 新建与更新已有资产是两条明确路径；更新只生成最小语义 patch。
- 断开外部 SaaS 后仍可运行内网核心闭环。
- Agent 关闭后，确定性执行、断言和证据采集仍能独立工作。

## Phase 3：团队执行 MVP

### 交付

- GitHub App/Webhook、PR/Check 回写、分支与审批流程。
- Platform API/BFF、Session/Intent、Test Authoring、Execution Orchestrator 和 RCA 基础模块。
- MySQL operational SoR、transactional Outbox、Outbox Relay、Redis 任务流/租约/Reconciler。
- Blob 证据及生命周期策略、Key Vault SecretRef、自建 Browser Grid 和 API/Contract ephemeral worker。
- 新建、复用、更新、执行、诊断五类 Intent Router。
- 基础 Console：Chat、结构化低代码/Test IR editor、IR/代码 diff、Run 时间线、证据、审批和重跑。

### 出口标准

- 重复 GitHub Webhook、Queue 消息和 Worker 重启不会产生重复外部副作用。
- 已有资产可被检索并生成最小语义 patch，再经过隔离验证与 Git review。
- Run 固定 Git commit；Test asset projection 可由 Git 重建，MySQL run/evidence metadata 与 Blob 按 hash 对账。
- 所有 Agent Finding 带模型/提示/工具版本、置信度和 evidence refs。

## Phase 4：企业平台扩展

### 交付

- AKS namespace/node pool、workload identity、private endpoint、NetworkPolicy 与 KEDA。
- 企业 PaaS MySQL、PaaS Redis、Azure AI Search、Blob Storage 和 Key Vault 的生产化治理。
- LiteLLM 无状态多模型路由与 fallback；TAP MySQL 持有策略、用量、预算和审计事实。
- Appium Device Farm gateway、设备占用与健康检查。
- 允许时接入 BrowserStack Adapter，用于外部矩阵或弹性补充。

### 出口标准

- 多租户权限测试证明 MySQL、Search、Redis、Blob 与 Agent context 无跨租户数据泄漏。
- Provider、模型、Worker 和队列故障均有降级、对账、告警和 Runbook。
- 容量、成本、数据保留和灾难恢复达到批准的生产 SLO。

## Phase 5：智能增强与规模化

- 失败指纹、聚类、历史 RCA 与修复效果反馈。
- 代码—测试资产轻量依赖图和可解释风险选测。
- Locator/Step 自愈候选、Vision 辅助和稳定性重复验证。
- Agentic Loop 离线评测：任务完成率、误操作率、安全违规率、成本和人工采纳率。
- 由候选 patch 自动创建 PR；仍不自动合并或制造“假绿”。
- 按真实瓶颈拆分 Ingress、Indexer、Result Pipeline 或 Scheduler，不预先微服务化。

## 工作包优先级

| 优先级 | 工作包 | 交付物 |
| --- | --- | --- |
| Phase 1 | Knowledge Contracts | 四索引 Schema、chunk ID、manifest、trace、citation、ACL filter |
| Phase 1 | Ingestion | typed parsers/chunkers、增量/删除、checkpoint、rebuild、index migration |
| Phase 1 | Retrieval | BM25/vector、RRF/rerank、Parent/Child、multi-hop、API、Inspector |
| Phase 1 | Evaluation | Golden Dataset、baseline、质量/权限/新鲜度/成本回归 |
| Phase 1 | RAG Operations | OTel、dashboard、告警、Runbook、Key Vault、配额和审计 |
| Phase 2+ | Test IR & Git Assets | Schema、stable ID、compiler、migration、semantic diff、branch/PR |
| Phase 2+ | Agent & Execution | Intent Router、DAG/Loop、provider ports、scheduler、lease、cancel |
| Phase 2+ | Evidence & RCA | manifest、collector、Blob layout、redaction、failure analysis |
| Phase 3+ | Platform | MySQL Run state、Outbox、Redis stream、Console、security governance |
