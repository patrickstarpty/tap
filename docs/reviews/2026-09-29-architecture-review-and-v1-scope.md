# 架构与实施评审及 V1 范围收敛

评审日期：2026-09-29。结论：**核心架构方向合理，但治理和文档推进快于交付；V1 收敛为 TAP AI 内的 5 项能力。** 本评审是时间点意见与范围记录，不修改任何 ADR、RFC 或 Plan 的生命周期状态；后续须以新 Plan/ADR 落实。

## V1 范围（2026-09-29 确认）

| # | 能力 | 当前状态 | 主要缺口 |
| --- | --- | --- | --- |
| 1 | 基于已有知识的可靠问答 | 基本具备：分阶段摄取、Milvus BM25 + dense 混合检索（RRF）、引用 | 仓库内 `tests/fixtures/quality/kb/profile-v1.json` 为 `cases: []`；2026-09-09 门禁使用的 100 条 case 与合成语料位于忽略的本地工作区，不可在仓库/CI 复现，也不是真实业务资料。SSE 为数据库轮询，无 token 级流式输出 |
| 2 | 知识图谱展示 | 基本具备：`modules/graph` 抽取、MySQL 存储、查询 API 与前端图谱 | 每次查询把整个快照载入 `InMemoryGraphStore`（`graph/adapters/mysql.py`），不可扩展 |
| 3 | 问答基于图谱关系做脉络分析 | 仅骨架：`GraphAnswerEnricher` 按问题关键词取最多 20 个节点作为事实拼入上下文 | 无多跳扩展、无路径推理、回答中无“关系链 + 原文证据”引用与路径高亮 |
| 4 | 自定义与导入外部 Skills/Agents | 部分具备：不可变、带摘要的 Agent/Skill Revision 与只读选择 | 工具白名单硬编码为 `knowledge.search`/`knowledge.answer`（`modules/ai/domain/assets.py`）；无导入能力与标准格式 |
| 5 | 可观测性：每次对话的调用链、推理链与 token 消耗面板 | 几乎没有：`modules/ai/adapters/litellm.py` 解析 `usage` 但未持久化，无追踪系统 | 需新建 |

**移出 V1：** 知识问答生成的测试计划和测试脚本导入对应功能模块。随之移出 Test IR、Jenkins、Web 执行、Low Code Automation 后端、主动 Agent；`test_management` 代码冻结保留，不作为 V1 验收项。Test Insights（`apps/backend` + ClickHouse）不在 V1 交付路径上。

**保留：** `AGENTS.md` 中 `/prototype` 始终保留全部模块的规则不变；原型继续展示 Test Management、Test Insights、Low Code Automation 等，但不构成 V1 交付物。

## V1 实施建议

1. **可靠问答**：先建立可提交（或可在受控环境复现）的 30–50 条真实标注问答集，作为每次检索/提示词改动的回归基线；SSE 改为 worker 事件通知 + LiteLLM 流式输出，替换 `interfaces/http/routes/conversations.py` 中 100ms 全量重载的轮询。
2. **图谱展示**：图谱查询改为按快照缓存邻接表或 MySQL 递归 CTE，V1 规模无需引入图数据库。
3. **脉络分析**：检索片段 → 实体 → 图上 1–2 跳路径扩展 → 模型基于路径作答并同时引用关系边与原文证据；前端复用图谱组件高亮路径。这是 LangGraph 在 V1 真正需要的地方（模型决定“继续扩展或作答”的有界工具循环），而非当前 `classify → admit → execute` 的固定三步包装。
4. **Skills/Agents**：Skill 采用 `SKILL.md` 格式导入，外部工具经 MCP 接入，Agent 定义为“系统提示词 + 引用的 Skills + 工具白名单 + 模型”；保留现有 Revision/摘要机制，把工具白名单改为可注册；导入 Skill 默认只作为提示词，不执行其内含代码。
5. **可观测性**：优先自托管 Langfuse（或 Arize Phoenix），不自研；在 ModelGateway 与主要应用服务埋点（OpenTelemetry GenAI 语义约定），LiteLLM 可直接开启 Langfuse 回调；每个 Turn 关联 trace ID，前端从回答跳转到调用链。多数模型不返回原始推理过程，面板稳定可见的是步骤链路（检索、图谱扩展、工具调用）加模型返回的推理摘要。

## 架构评审要点

### 值得保留

- MySQL 为唯一权威状态，Milvus/Redis 为可重建投影或唤醒信号，模型统一经 LiteLLM。
- 后端 `modules/*/{domain,application,ports,adapters}` 分层，domain 层无框架依赖。
- 摄取分阶段写入内容寻址中间产物，索引可重建；混合检索与引用链完整。
- OpenAPI 生成 TS 类型并有漂移检查，Problem Details 统一错误，SSE 支持 `Last-Event-ID` 续传。

### 需调整（按严重程度）

1. **前端原型分叉。** `apps/web` 与 `apps/tap-ai-frontend` 各有一份 `TapProductPrototype.tsx`（1,454 / 2,394 行），`widgets/tap` 有 41 处文件差异；边界脚本禁止互相 import 且无共享包。tap-ai 中名为 prototype 的组件即正式产品（27 个 `useState`，`"api" | "fixture"` 双模式，fixture 数据进入生产路径）；两端都无 router，跨应用跳转不可用。**建议 V1 第一步：** 抽出 `packages/` 共享展示组件（侧栏、主题、文案、图谱、对话），两端边界检查放行；`/prototype` 改为共享组件上的薄组装，非 V1 模块继续用 fixture；tap-ai-frontend 拆为 App Shell + 模块容器并引入 router，fixture 移入测试工具。
2. **架构叙事与代码不一致。** Test IR 仅有 `testIrDigest` 字符串；Jenkins 在源码中零引用；LangGraph 已引入（`langgraph==1.2.12`）但只是固定三步图，`agentic` 模式未使用；`AGENTS.md` 写 Azurite、README 写 MinIO，Compose 中共三个对象存储；ClickHouse 已进入部署却无 ADR；ADR-021/025 仍为 accepted 但交付已无限期推迟。
3. **过程开销。** 32 天 30 个 ADR（13 个已被取代）、约 3 万行文档、33 份评审；两份 active Plan 加一份 roadmap；业务 Gate 依赖项目无法控制的输入而长期 `PENDING`；V2/V3 Gate 曾因预填指标被撤销。建议合并为一份 V1 Plan，并用“工程完成 / 业务验证”两个显式状态替代散文式免责说明。
4. **后端可靠性缺口。**
   - 租约过期回收任务时 `attempt` 不递增、无上限（`knowledge/adapters/mysql_documents.py` 的 `claim_jobs`），毒消息无限重试；删除失败无退避。
   - Embedding/索引暂时不可用时直接 `fail_job` 永久失败。
   - `run_worker_loop` 无异常保护，一次异常即退出。
   - LiteLLM 遇 429/5xx 立即重试无退避，整段缓冲无流式。
   - `MysqlDocumentRepository` 所在文件 4,781 行，业务状态机位于 SQL 适配器。
   - 无自带 MySQL 时 485 个集成测试有 252 个静默跳过，`make test` 绿灯不覆盖持久化。
   - 架构测试只检查 knowledge/access/test_management 三个模块的框架无关性，未覆盖 chat/ai/graph/governance，无循环检测。
5. **复杂度与收益不匹配。** Outbox → Relay → Redis Stream → 唤醒链路涉及 6 个模块与 Lua 去重，但 worker 本就每秒轮询，最多省约 1 秒；已退役 Codex 路径在 PATH 有 `codex` 时仍会注册；遗留代码约 5k 行，三个 LiteLLM 适配器；`.env.example` 96 个变量；两个后端共用一个 MySQL schema。

## V1 前的收敛动作

- 以上 5 项能力各自写明验收标准，合并为唯一 V1 Plan；现有两份 active Plan 与 roadmap 按治理规范处理状态。
- ADR-021/025 及 Test IR、Jenkins 相关方向以新 ADR 标记推迟；ClickHouse 补 ADR 或明确不在 V1。
- 清理 Codex、Azure/Azurite 遗留路径与多余 LiteLLM 适配器；基础设施收敛为 MySQL、Redis、MinIO、Milvus、LiteLLM、Langfuse。
- 修正 `AGENTS.md`/README 与代码不一致之处（对象存储、LangGraph 实际状态）。
