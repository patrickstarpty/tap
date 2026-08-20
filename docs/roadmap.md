# TAP 交付路线图

路线沿用 `engprod` 讨论中的演进逻辑：先做可独立运行的 Agentic Test Lab，再迁移到团队 MVP，最后落到 Azure 企业栈。每阶段用验收证据退出，不以日期代替完成标准。

## Phase 0：契约与样例冻结

### 交付

- 选定一个 Web 流程、一个移动流程和一个 API Contract 作为贯穿样例。
- 定义 Test IR v1 最小 Schema、stable Test ID、Git layout 和语义 diff。
- 定义 Run/Task/Attempt、Evidence Manifest、Finding、Approval 与幂等键。
- 把自然语言“新建”与“更新已有资产”拆成两个固定 DAG。
- 建立安全边界：模型可见数据、工具白名单、SecretRef、网络出口和审批动作。

### 出口标准

- 同一 Test IR 能编译并执行至少一个 Web target。
- 每次执行能从 Git commit 追溯到结构化结果和 Blob/local artifact hash。
- 更新路径只生成最小 patch，不无条件重写整个测试。

## Phase 1：Agentic Test Lab

### 技术基线

- LangGraph + FastAPI：主 Agent、检索/执行/审查子 Agent 与 DAG/Loop 实验。
- Ollama：本地模型；通过 LiteLLM 保持未来模型切换契约。
- Selenium Grid 4 + Docker：首个 Browser Grid。
- Appium + Appium Device Farm：在 Web 闭环稳定后加入。
- Allure + OpenTelemetry + Jaeger：报告和跨组件追踪。
- Healenium + 自研候选定位算法：只生成自愈候选。
- MCP Python SDK：受控工具接入。
- Git + 本地对象存储；MySQL/Redis 可使用本地兼容部署。
- 本地 typed ingestion/RAG：文档章节、代码 symbol、BDD Scenario、失败 Incident 分型切片；先 BM25，保留向 hybrid retrieval 演进的契约。

### 出口标准

- 自然语言/BDD → Draft Test IR → Git diff → Browser 执行 → 统一证据 → RCA/自愈建议 → 人工确认完整跑通。
- 断开所有外部 SaaS 后仍可运行内网核心闭环。
- Agent 关闭后，确定性执行和断言仍能独立工作。

## Phase 2：团队 MVP

### 交付

- GitHub App/Webhook、PR/Check 回写、分支与审批流程。
- Platform API/BFF、Session/Intent、Test Authoring、Execution Orchestrator、RCA 基础模块。
- MySQL operational SoR、transactional Outbox、Redis 任务流/租约/Reconciler。
- Blob 证据与生命周期策略；Key Vault SecretRef。
- 自建 Browser Grid；API/Contract ephemeral worker。
- 新建、复用、更新、执行、诊断五类 Intent Router。
- 基础 Console：Chat、结构化低代码/Test IR editor、IR/代码 diff、Run 时间线、证据、审批和重跑。

### 出口标准

- 重复 GitHub Webhook、Queue 消息和 Worker 重启不会产生重复外部副作用。
- 已有资产可被检索并生成最小语义 patch，再经过隔离验证与 Git review。
- Run 固定 Git commit；Test asset projection 可由 Git 重建，MySQL run/evidence metadata 与 Blob 按 hash 对账。
- 所有 Agent Finding 带模型/提示/工具版本、置信度和 evidence refs。

## Phase 3：Azure 企业平台

### 交付

- AKS namespace/node pool、workload identity、private endpoint、NetworkPolicy 与 KEDA。
- 企业 PaaS MySQL、PaaS Redis、Blob Storage、Key Vault。
- Azure AI Search 四索引：`kb-doc-v1`、`kb-code-v1`、`kb-bdd-v1`、`kb-failure-v1`。
- 文档/代码/BDD/失败专用 ingestion、Parent/Child、多粒度索引、BM25/Vector/AST/Symbol/RRF/Reranker。
- 检索前强制 `tenantId/projectId/allowedGroupIds/classification/environment` 过滤。
- LiteLLM 无状态多模型路由与 fallback；TAP MySQL 持有预算账本/审计，TAP Policy 执行限流与项目策略；高并发本地推理从 Ollama 演进到 vLLM。
- Appium Device Farm gateway、设备占用与健康检查。
- 允许时接入 BrowserStack Adapter，用于外部矩阵或弹性补充。

### 出口标准

- 多租户权限测试证明 MySQL、Search、Redis、Blob 与 Agent context 无跨租户数据泄漏。
- 四索引可从 Git/Blob/MySQL 重建，删除与权限变更能在规定窗口内生效。
- Provider 故障、模型故障、Worker 丢失和队列积压都有降级、对账和 Runbook。

## Phase 4：智能增强

- 失败指纹、聚类、历史 RCA 与修复效果反馈。
- 代码—测试资产轻量依赖图和可解释风险选测。
- Locator/Step 自愈候选、Vision 辅助和稳定性重复验证。
- Agentic Loop 的离线评测集：任务完成率、误操作率、安全违规率、成本和人工采纳率。
- 由候选 patch 自动创建 PR；仍不自动合并或制造“假绿”。

## 首批工程工作包

| 工作包 | 交付物 |
| --- | --- |
| Test IR | JSON Schema、stable ID、compiler SPI、migration、semantic diff |
| Git Assets | repository layout、branch/PR、revision sync、reconciler |
| Agent | Intent Router、DAG/Loop、subagent registry、context/permission/budget |
| Execution | Browser/Device/API provider ports、scheduler、lease、cancel |
| Evidence | manifest、collector、Blob layout、redaction、retention |
| Data | MySQL schema、Run state machine、Outbox、Redis stream/cache rules |
| Knowledge | 四索引 Schema、typed chunkers、hybrid retrieval、RRF/rerank、ACL filter |
| Models | LiteLLM routing、model registry、budget、fallback、evaluation |
| Security | Key Vault/SecretRef、sandbox、egress、approval、audit |
| Observability | OTel conventions、Allure projection、dashboard、Runbook |
