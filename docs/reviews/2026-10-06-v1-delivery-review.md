# V1 交付物评审与优化建议

评审日期：2026-10-06。评审对象：`origin/main` 提交 `2da88e1`（PR #39、#40、#41 合并后）。评审目标：判断 V1 能否交付，并以"真实系统参加国际比赛、达到世界一流水平"为标准列出差距与优化顺序。

评审方法：只读代码与文档；运行 `apps/tap-ai-frontend` 的 `ui:capture`（隔离示例响应）采集真实前端界面；查看仓库内原型截图与 E2E 失败截图。未运行 `make check`、`make test`、`make demo-e2e`，未在真实服务上复现问题；凡结论来自代码阅读而非运行验证的，文中标注"代码阅读"。

关联文档：[V1 总纲](../superpowers/plans/2026-09-29-v1-roadmap.md)、[产品路线图](../superpowers/plans/2026-10-01-product-roadmap.md)、[现状架构](../architecture.md)、[产品原型基准](../guides/2026-09-22-product-prototype-baseline.md)。

## 1. 结论

- **V1 不能交付。** 按总纲退出标准（5 项能力"工程完成"全部勾选），当前 1/5；业务验证 0/5。勾选状态与代码一致，没有"做完未勾"的情况。
- **真实系统与原型差距大。** 真实系统只有 Tapper 四个页面，回答区带调试信息；原型中的 Test Management、Test Insights、Low Code Automation 在真实系统中不存在。演示真实系统时，故事线必须围绕"上传资料 → 图谱 → 带引用问答 → 推荐问题 → 调用链"重排。
- **知识图谱目前只是展示。** 本地演示使用假抽取，模型抽取对真实文档不可用，回答链路中的图谱上下文在真实问题下几乎从不生效。详见第 5 节。
- **已确认的 V1 验收门禁新增项**（2026-10-06 决定）：图谱脉络分析以"关系问答为主、检索增强顺带、知识总览为可视化结果"验收；设计路线待批准后写 spec（第 5.3 节）。

## 2. 五项能力验收

| 能力 | 工程完成 | 业务验证 | 结论 |
| --- | --- | --- | --- |
| 1 可靠问答 | 部分：推荐问题已完成（#38、#39） | 替代验收 16/17（公开友邦条款，非真实项目） | 未完成 |
| 2 知识图谱展示 | 未开始 | 无 | 未完成 |
| 3 图谱脉络分析 | 未开始 | 无 | 未完成，且现有图谱基础需重做 |
| 4 Skills/Agents | 未开始 | 无 | 未完成 |
| 5 可观测性 | 完成（#36） | 无 | 工程完成 |

### 2.1 可靠问答

已完成：推荐问题按知识库与使用情况生成、经真实回答链路校验、过滤失效条目（`modules/chat/application/suggestions.py`、`routes/prompt_suggestions.py`）。

缺口（代码阅读）：

- 仓库内没有 golden set 与回归命令；`tests/fixtures/quality/` 下的 profile 是合成文本，不是标注问答。
- 没有逐 token 流式：`entrypoints/tapper_generation_worker.py:238` 在回答完成后发一次完整 `answer.delta`。
- SSE 仍为数据库轮询：`interfaces/http/routes/conversations.py:522` 每 100ms `service.load` 整个对话，`idle < 10` 意味着约 1 秒无事件即关闭流，客户端靠 `Last-Event-ID` 重连。
- Turn 处理中任何异常直接判 failed，无退避重试（`modules/chat/application/process_turn.py:68`）。
- 租约回收递增 `processing_attempt` 但无上限（`modules/chat/adapters/mysql_conversations.py:1237`）；`max_checkpoint_attempts` 只约束 checkpoint 冲突。
- generation worker 主循环无异常保护（`tapper_generation_worker.py:654-664`），`claim_queued` 抛错即进程退出。
- 集成测试缺 MySQL 时静默跳过（约 20 处 `pytest.skip`）。
- 推荐问题仍需真实项目复验（≥90% 有依据）。

### 2.2 知识图谱展示

- `MysqlGraphStore._memory`（`modules/graph/adapters/mysql.py:478-618`）每次查询把整个快照载入 `InMemoryGraphStore`；邻居与路径遍历每跳全量扫边。
- 没有"领域总览"视图；前端 `publishedGraphData.sourceCommunity` 按文件名正则分配聚类颜色。
- 节点表只有主键，没有 label/canonical key 索引。

### 2.3 图谱脉络分析

见第 5 节。

### 2.4 Skills/Agents

- `routes/ai_assets.py` 只有 GET；资产来自 `validation_asset_seed`；`_DOMAIN_TOOLS` 硬编码为 `knowledge.search`/`knowledge.answer`（`modules/ai/domain/assets.py:12`）。
- 没有 `SKILL.md` 导入、工具注册表、MCP 注册。
- Agent 的系统提示词与 Skill 模板确实进入回答（`interfaces/http/knowledge_service.py:491-519`），选择机制可用，只是没有可选资产。
- 真实前端"Create agent draft"按钮没有后端支撑。

### 2.5 可观测性

工程完成。遗留：`trace_span`、`model_call_content` 无保留期；DashScope 部分模型算不出成本（需在 `deploy/local/litellm/config.yaml` 配 `input_cost_per_token`/`output_cost_per_token`）；调用链在真实前端以裸文本出现在回答之前（第 3 节）。

## 3. 真实系统界面与交互审查

采集方式：`corepack pnpm --dir apps/tap-ai-frontend run ui:capture`（输出在 `apps/tap-ai-frontend/test-results/ui-capture/`，示例 API 为空态）；真实回答画面来自 10 月 2 日 E2E 失败截图（fake 模型）。

### 3.1 回答区（演示核心）

1. Trace 裸文本出现在回答之前：`Trace: 1.6s · 20/19 tokens · cost unknown · text-embedding-v4, qwen-plus → fake/...`，含内部模型别名。应收进可展开面板。
2. 回答无 Markdown 渲染；Insights 解释按行平铺（"Report investigation / Why did this run fail? / Verified Insights facts · query-capture · insights-metrics-v1"），像日志。
3. 消息区左缘有游离短横线（真实系统两条，原型一条），位置固定。
4. 回答动作只有 Regenerate；没有复制、点赞点踩、追问建议、分享导出。
5. 提问到出现回答之间只有"等待开始"，然后整段弹出。
6. "Generate Test Plan draft"按钮仍在回答下方，Test Management 已冻结且真实系统无页面，是断路。
7. 聊天内可触发 Insights 解释（`insights_explanation` 仍在生成 worker 路径里），不在 V1 范围。

已做对的：句内引用 `[1]` 小标（优于原型）；来源/Agent/Skill 三类 chip；"Message context"与"Sources and settings used"可折叠。

### 3.2 其他页面

8. Library 文档卡片全部显示"No preview available for this source."，占卡片大半。
9. Knowledge Graph 没有总览，首屏只有一句"Select a ready source to view its graph."。
10. Type、Status 下拉是浏览器原生样式；输入框内"Each turn records the knowledge context you select."是实现说明。
11. 左侧图标栏无文字标签；语言切换为两个竖排按钮；没有项目切换器。

### 3.3 原型（`/prototype`）的缺陷，仅在原型保留设计职责时需修

- Low Code Automation 页左下角游离的"2 automations"文本。
- Documents 卡片"View"被截断；"部分页面仍在索引"却标 Ready；Draft 与 Ready 同用绿色对勾。
- 输入框显示 GPT-5.6 Sol，调用链显示 qwen-plus，示例数据自相矛盾。
- 图谱被三层导航与筛选栏压到下半屏，标签过小、虚线交叉。

### 3.4 原型与真实前端的关系（建议）

不做全量同步。Tapper 范围内以 `tap-ai-frontend` 为设计与实现唯一真源，用 `ui:capture` 与 E2E 截图做回归；原型冻结为完整产品愿景的展示物，只保留尚未实现模块的设计职责。一次性"收割"原型中值得要的决定（回答摘要行、来源配置折叠、Trace 收起、领域总览布局），之后不再回流。这改变 AGENTS.md 与原型基准规范的"原型先行"规则，需以 ADR 记录。

## 4. 架构与工程问题

按阻塞程度排序。

**阻塞交付**

1. **V1 定义不含交付前提。** 只有 validation 身份模式（`interfaces/http/app.py:91` 对其他模式拒绝），README 写明"本机无认证 Demo"，`deploy/` 只有本地配置。多人、企业部署排在 V4。即使 5 项全勾，交付物仍是单机演示。需决定 V1 交付形态。
2. **没有 CI。** `.github/` 只有 `CODEOWNERS`；合并靠 `--admin`；集成测试静默跳过。
3. **worker 可靠性**（2.1 所列）。
4. **SSE 约 1 秒断流叠加轮询**（2.1 所列）。

**需设计决策**

5. **流式与回答引擎冲突。** #39 后回答由校验过的 claims 重建（`modules/knowledge/adapters/litellm.py` `_ANSWER_PROMPT`），是事后校验的结构化输出。流式下发什么（草稿后替换 / 校验后逐句）须在 spec 中先定。
6. **子项目 5 与 6 的依赖。** 工具循环需要工具注册表；LangGraph 去留推迟到子项目 5，但 ADR-028/029 仍写它为统一编排器，实际回答逻辑在 `knowledge_service` 与 worker。建议合并设计。
7. **原文永久保留且无认证访问。** `model_call_content` 永久保存提示词与回答原文，trace 接口无认证。接入真实资料前需定保留期与访问控制。
8. **V1 之外的代码缠在关键路径。** 生成 worker 异常分支多处 `if insights_query_id is None: raise`；冻结的 `test_management` 仍在同一运行时。

**次要**

9. 过大文件：`mysql_documents.py` 4782 行、`mysql_review.py` 3108 行、`tapper_runtime.py` 1933 行；前端残留 `LegacyTapperWorkspace.tsx`。
10. 文档过期：产品路线图仍写"推荐问题 进行中（分支 `feat/prompt-suggestions`）"，总纲写子项目 3"未开始"，实际 #38、#39 已合并。`architecture.md` 写图谱"按问题关键词"匹配，代码是整句子串匹配。
11. 已记录未确认修复：Stop 取消上一个 Turn（`TapperChat` `generatingTurnId` 竞态）；未复现。

## 5. 知识图谱专项审查

### 5.1 结论

现有图谱不能做脉络分析。数据模型（节点五类、边带关系类型/置信度/来源、证据到切片、推理来源）与 job 机制可以保留；建图与用图两段都要重做。

### 5.2 发现（代码阅读）

1. **本地演示是假抽取。** `.env.example:104` 默认 `TAPPER_GRAPH_EXTRACTION_MODE=fake`，`modules/graph/adapters/fake_extraction.py` 生成"Section 1..N"CONCEPT 节点加 CONTAINS 边，没有实体与关系。
2. **模型抽取对真实文档不可用。** `ModelGatewayGraphExtraction.extract` 把一个文档最多 500 个切片（`modules/graph/application/worker.py:76`）打成一次结构化调用，超时沿用 `model_timeout_seconds`（默认 15 秒，上限 60 秒）。
3. **快照按单文档建，查询按来源集合查。** `MysqlGraphReadyProjection.after_ready` 每个修订建一张图；`active_snapshot` 按所选来源集合的精确摘要查找。选两个以上来源提问时查不到图，`GraphAnswerEnricher` 返回 NOT_READY；前端 Library 一次只能看一个来源。
4. **回答侧匹配失效。** `GraphAnswerEnricher.enrich` 把整句问题传给 `InMemoryGraphStore.search`，后者做 `needle in node.label` 子串匹配（`modules/graph/application/queries.py:107-118`）；自然语言问题几乎不命中。命中时也只取最多 20 个节点的诱导子图，无扩展、无路径，作为 JSON `knowledgeGraph` 塞入上下文。
5. **边不可引用。** 引用模型只接受切片证据标签 `S1..S20`（`modules/knowledge/ports/answers.py:322-323`），提示词未要求沿关系作答，前端无路径高亮。
6. 没有跨文档实体对齐（`canonicalKey` 只在单次抽取内有效）、没有受控关系词表、INFERRED 边依赖"文档中显式声明的推理规则"，真实资料中几乎不存在。
7. 质量集 QUALITY-GRAPH-01 为合成文本，不能证明真实资料上的抽取质量。

### 5.3 已选路线（待批准后写 spec）

目标（2026-10-06 已确认）：关系问答为主，检索增强顺带，知识总览为可视化结果，作为 V1 验收门禁。

路线 1：项目级图 + 增量合并 + 检索锚定的确定性推理。

- 建图：按文档分批抽取（每次 8–12 个切片，可重试，单批失败不毁整份文档）；受控关系词表；抽取结果作为文档片段图落库，再按规范化 canonical key 与别名表合并进项目级图；节点与边保留每份来源的证据以支持授权过滤；项目图带版本号，发布或删除触发增量合并；合并时计算社区与度数并存库。
- 回答：检索命中切片反查节点 + 问题实体链接（分词后按 label/别名索引）作为种子；授权范围内 1–2 跳扩展；种子之间求有界路径；边组织为关系证据 `R1..Rn` 与切片证据 `S1..Sn` 一起给模型；关系类 claim 必须引用 R 标签；grounding 同时接受 S 与 R；边引用持久化；前端点击高亮路径。
- 总览：Library 默认显示项目图总览（社区、主要实体、度数），替换假聚类。
- 否决的路线：查询时内存合并单文档快照（无法沉淀对齐、总览不可缓存）；GraphRAG 式社区摘要为核心（摘要不可逐句引用，与可追溯底线冲突，可作后续增量）。

### 5.4 门禁建议

- 真实资料：先用已有的 8 份公开友邦条款（`/tmp/aia-docs`，需纳入可复现的 fixture 路径）加流程类文档，后用真实项目资料复验。
- 关系类问题集 20–30 条，人工标注期望涉及的实体与关系；通过标准：≥80% 的回答包含正确关系边引用且无错误边，路径高亮与引用一致；普通问题集的有依据率不低于加入图谱前。
- 抽取质量：抽样 50 条边人工核对，准确率 ≥85%；跨文档对齐抽样 30 个实体，误合并 ≤5%。
- 性能：1 万节点、5 万边项目图，邻居与路径查询 p95 < 300ms。

## 6. 优化建议与顺序

每步合并后更新总纲与产品路线图对应行。

### 第一步：可靠性与工程基线

- worker 主循环异常保护；Turn 瞬时失败退避重试，`processing_attempt` 设上限；摄取同样处理。
- SSE 改为事件驱动（Redis Stream 通知 API 推送），保留续传；去掉 1 秒断流。
- 建 CI：lint、单测、带 MySQL/Redis 的集成测试、contract 检查；E2E 单独 job。
- 从生成 worker 路径拆掉 Insights 与 test_management 分支；删除遗留文件。
- 验收：CI 绿；故意杀掉模型网关时 Turn 进入重试而非立即失败；worker 单次异常后继续处理下一条。

### 第二步：回答体验

- 流式设计：建议逐 claim 校验后逐句下发；修改 worker 事件协议与前端消费。
- 前端：Markdown 渲染；句内引用悬停原文片段、点击定位原件并高亮；Trace 收进可展开面板；复制、点赞点踩、追问建议。
- 移除断路：Generate Test Plan draft、聊天内 Insights、无后端的 Create agent draft。
- 验收：真实模型下首句出现时间 < 2 秒（本机）；引用点击可定位原文；`ui:capture` 增加回答态截图。

### 第三步：知识图谱（第 5.3 节路线 1）

- 顺序：抽取分批与词表 → 项目级图与合并 → 回答侧种子/扩展/路径与 R 引用 → 前端路径高亮与总览 → 门禁评测。
- 验收：第 5.4 节门禁。

### 第四步：质量闭环

- golden set 格式与回归命令；把推荐问题验收的 17 条与图谱关系问题集纳入；CI 用 fake 模型跑结构性断言，真实模型可选。
- 点赞点踩写入 MySQL，作为 golden set 补充来源。
- 验收：`make quality-golden`（命名待定）可在 CI 运行并输出基线分数。

### 第五步：Skills/Agents 与工具循环

- 工具注册表 → 有界 LangGraph 工具循环（复用第三步的图 API）→ `SKILL.md` 导入为不可变 Revision → MCP 注册。
- 同时决定 LangGraph 去留，更新 ADR-028/029。
- 验收：导入一个真实外部 Skill 并在对话中生效；关系类多跳问题经工具循环回答且步数有上限。

### 第六步：交付前提

- 身份模式与认证；追踪与原文保留期；非回环部署 compose；多项目切换。
- 验收：两名用户在局域网内同时使用，数据按项目隔离。

### 演示故事线建议

围绕真实能力：上传真实条款与流程文档 → Library 总览显示领域实体与关系 → 提问关系类问题，回答逐句带切片与关系引用，点击引用高亮路径 → 推荐问题继续追问 → 调用链展示检索、图谱扩展、模型调用与成本。记忆点是"每一句话都能追溯到原文和关系边"。

## 7. 治理与文档待办

- 更新产品路线图：推荐问题行改为"已完成（#38、#39）"；子项目 3 行改为"进行中"。
- 更新总纲：能力 3 验收改为第 5.3 节目标；能力 2 的"领域总览"与能力 3 共用项目级图。
- `architecture.md`：修正图谱匹配描述；补项目级图数据流。
- ADR：原型与 TAP AI 前端的关系（第 3.4 节）；LangGraph 去留（第五步）。

## 8. 需要决定的事项

1. V1 交付形态：单机演示，还是多人试用（决定第六步是否前移）。
2. 是否接受第 3.4 节建议（原型冻结、实现为真源）。
3. 批准第 5.3 节路线 1，以进入 spec 编写。
4. 评委是否会亲手操作系统（决定断路清理与稳定性的优先级）。
