# TAP 现状架构

本页只记录代码中可验证的现状，随代码更新。目标能力与验收见 [V1 总纲](superpowers/plans/2026-09-29-v1-roadmap.md)；历史设计见[归档](archive/index.md)。

## 1. 应用边界

- `apps/tap-ai-backend`：TAP AI 后端（Python 3.13 / FastAPI），Tapper 问答、知识文档、图谱、Agent/Skill 资产与测试方案生成。
- `apps/tap-ai-frontend`：TAP AI 前端（React / TypeScript / Vite）；只呈现 API 驱动的产品界面，交互以 `apps/web` `/prototype` 为设计来源。
- `apps/backend`：TAP 非 AI 后端，当前主要承载 Test Insights（`tap_platform/insights`，ClickHouse 适配器）。
- `apps/web`：TAP 非 AI 前端；`/prototype` 路由是完整产品设计基准（见[产品原型基准规范](guides/2026-09-22-product-prototype-baseline.md)），不代表后端已实现。

两个后端之间、两个前端之间由边界脚本禁止互相 import，当前没有共享包。

## 2. TAP AI 后端分层

模块位于 `apps/tap-ai-backend/src/tap/modules/<name>/`，按 `domain`、`application`、`ports`、`adapters` 分层，domain 层不依赖框架。

| 模块 | 职责 |
| --- | --- |
| `access` | Project 范围、授权策略与上下文 |
| `knowledge` | Source/Document 上传、分阶段摄取、切片维护、审阅、检索与带引用回答 |
| `graph` | 从文档抽取实体与关系、图谱任务与查询 |
| `chat` | Conversation、不可变 Turn、回答规划与 Turn 处理 |
| `ai` | Agent/Skill 资产 Revision、模型目录、LangGraph 交互图 |
| `governance` | 审计 |
| `test_management` | 测试方案生成、校验与发布（V1 冻结） |

HTTP 路由位于 `interfaces/http/routes/`；公共基础设施位于 `platform/{db,messaging,storage}`。进程入口位于 `entrypoints/`：`tapper_api`（API）、`relay_reconciler`（Outbox → Redis Stream 中继），以及 ingestion、parser、graph、generation、test design、prompt suggestion 六个 worker。`legacy_litellm.py` 与 `legacy_loopback_answer_runtime.py` 为遗留路径。

## 3. 基础设施

`compose.yaml` 中的服务：

| 服务 | 用途 |
| --- | --- |
| `mysql` | 唯一权威业务状态、Outbox 与 Knowledge Graph |
| `redis` | 可重建的唤醒信号（Redis Stream） |
| `milvus`（含 `milvus-etcd`、`milvus-minio`） | 可重建的文档检索投影，BM25 + dense 混合检索 |
| `litellm` | 所有模型调用的统一网关 |
| `tap-parser` | 隔离的文档解析 |
| `tap-minio` | 唯一对象存储：原件与中间产物（S3 API） |
| `clickhouse` | Test Insights 指标（`apps/backend`，不在 V1 交付路径） |
| `phoenix`（可选，profile `observability`） | OTLP 追踪查看器，仅在设置 `OTEL_EXPORTER_OTLP_ENDPOINT` 时接收导出的 span |

## 4. 主要数据流

**摄取**

1. API 接收上传，原件写入对象存储，文档与任务状态写入 MySQL，并写 Outbox。
2. Relay 把 Outbox 事件转发到 Redis Stream 唤醒 worker；worker 同时按固定间隔轮询 MySQL。
3. worker 按阶段执行解析（`tap-parser`）、切片、embedding，每阶段产物内容寻址写入对象存储。
4. 切片投影写入 Milvus；投影可从 MySQL 与中间产物重建。

**问答**

1. 用户在 Conversation 中提交问题，生成不可变 Turn。
2. 检索 → 关系分析子图（种子/扩展/路径/排序，R1..R20）→ 检索增强 ≤ 5 条 → 生成 → R/S 校验：worker 在授权范围内经 Milvus 混合检索（RRF 融合）取回切片后，关系分析 Agent 子图（`modules/ai/application/agents/relation_analysis.py`）在固定图版本内做种子（问题 + 证据切片）、扩展（1–2 跳）、路径（种子两两之间 ≤3 跳）、排序（路径优先、按置信度 × 种子邻接度，截断为 R1..R20）；检索增强对候选集外的邻居节点证据切片最多补 5 条（同样的 ACL 与发布授权，`TAPPER_GRAPH_RETRIEVAL_AUGMENT` 控制）；生成后对 claim 的 R/S 标签做位置与端点标签校验，非法标签剥离或整条丢弃（`TAPPER_GRAPH_REASONING` 为 0 时整段跳过，回退为纯切片回答）。
3. 经 LiteLLM 生成回答并校验引用，结果写回 MySQL；可选模型目录由 LiteLLM `GET /v1/model/info` 动态提供（60 秒缓存），新增模型只改 `deploy/local/litellm/config.yaml` 并重启 LiteLLM，后端无需改代码或重启。
4. 前端经 SSE 获取 Turn 事件（服务端轮询数据库，支持 `Last-Event-ID` 续传）。

**图谱**

1. graph worker 按文档修订分批（默认 10 个切片一批）用模型抽取实体与关系，关系限定为受控词表并带原文标签，批次结果持久化到 `graph_fragment_batch`，可重试与恢复；全部批次成功为 READY，部分失败为 PARTIAL（片段抽取）。
2. 片段发布（READY/PARTIAL）、知识发布/撤销发布、来源删除、单文档删除均把项目加入 `graph_project_merge_job` 合并队列；graph worker 的 `pending_work` 钩子串行认领、租约执行合并（复用 `MysqlGraphJobStore` 的 `FOR UPDATE` 租约与幂等模式），对项目内全部满足档案摘要的 READY/PARTIAL 片段做全量重放（merge 输入上限 100 个已就绪来源，见第 5 节已知差距）：三级实体对齐（规范化 canonical key 精确匹配 → 别名表匹配 → embedding 相似度，默认关闭；三级都要求被对齐的节点类型相同）、边合并（按两端节点与关系类型合并，置信度取最大值、证据取并集）、标签传播社区划分，`fragment_digest` 不变则跳过，版本就绪后原子切换为 READY 并只保留一个旧版本（项目合并）。embedding 对齐在片段节点数（各片段 draft 节点数之和）超过 `TAPPER_GRAPH_ALIGN_EMBEDDING_MAX_NODES`（默认 2000）时跳过，退化为仅用一、二级对齐，避免这一步 O(N²) 纯 Python 计算阻塞事件循环。
3. 项目图按（project_id、version）在进程内缓存邻接表，版本切换时失效；查询 API（总览、搜索、路径、节点详情、邻居、高亮）与回答侧关系分析 Agent 子图都读这份缓存；响应带 `graphVersion`，请求携带的 `graphVersion` 与当前不符时返回 409；关系分析子图在种子阶段与排序阶段之间检测到版本切换时返回 STALE，回答退化为纯切片且不带任何关系边引用。旧的按来源快照查询路由（`GET /snapshots` 等）原样保留给文档详情页的片段视图。`graph rebuild` CLI 可手动触发重建：它只把已发布修订重置为待重新抽取，不会自己请求合并（合并由片段重新发布时各自请求），因此存在一个退化窗口——重建期间，一旦第一个重新抽取的片段完成重新发布，合并即会发布一个只包含当时已 READY 片段的新当前版本，图谱随之收缩到重新抽取的子集，再随后续片段逐个完成而逐步恢复；旧版本会保留一个周期，但查询路由只对外提供当前版本。

本地 CI/E2E 用规则式假抽取（`TAPPER_GRAPH_EXTRACTION_MODE=fake`），演示与真实环境用 `model`。

验收门禁：golden set 评测（`make quality-graph-relations`，CI 常驻，假抽取 + 确定性模型，只验证链路接线、R 边引用形状与评测脚本本身的正确性，不代表真实关系抽取准确率）、抽样导出（`make graph-sample-export`，人工核对边与跨来源合并的准确率）、`graph-bench`（合成 1 万节点/5 万边基准，不进默认 CI）；真实语料上的关系 golden set 评测（`make quality-graph-relations-real`，opt-in）需要人工标注且题目覆盖须包含若干 `expectedSources` 跨 2+ 份文档的问题，否则无法暴露"命中本题期望边的同时误引用另一份无关文档邻域边"这类离题引用。

LangGraph 交互图（`modules/ai/application/interaction_graph.py`）当前为固定的 `classify → admit → execute` 三步。

**推荐问题**

1. 四类触发写入 MySQL 刷新队列表，以（项目、用户、语言）为单位：知识发布（Ready 投影同事务内请求整项目刷新，立即到期）、对话完成（节流 10 分钟）、读取时命中过滤或首次读取（无历史缓存立即到期，其余过滤节流 10 分钟），以及每日兜底扫描（24 小时）。
2. 独立的 prompt suggestion worker 轮询队列认领到期行，调用 `PromptSuggestionService.refresh`：拼装知识库主题与图谱主要实体、本人历史提问来源、其他用户来源使用次数（仅次数，不含提问原文）作为模型输入，经模型网关 `generate_structured` 生成候选（每次最多 8 条，每条 1–3 个来源，来源须属于本次输入的知识库来源，语言与请求 locale 一致）。
3. 每条候选以当前用户授权、按对话回合的同一路径（冻结来源 → 回答规划 → `answer_conversation`，来源以 `ResourceMode.SCOPE` 限定，不保存对话）验证依据，仅未弃答且带引用的候选写入 MySQL 缓存；刷新失败保留旧缓存而非清空，知识库为空时缓存写为空列表。
4. `GET /prompt-suggestions` 只读 MySQL 缓存并与知识库当前来源状态比对，过滤掉来源已删除、未发布、当前用户无权限或版本已更新的条目；接口本身从不调用模型，过滤后为空返回空列表，且任一条目被过滤会重新排入一次刷新。

**可观测性**

1. API 处理请求时开启 `turn.request` span 并生成 traceparent，写入 `chat_turn.traceparent` 随 Outbox/Redis Stream 传给 worker。
2. worker 用同一 traceparent 开启 `turn.execute` span，检索、图谱扩展、模型调用、工具调用各自开子 span；span 结束经 `MysqlSpanExporter` 写入 `trace_span`，每次模型调用同时由 `MysqlModelCallRecorder` 写入 `model_call` 与 `model_call_content`。
3. 若配置了 `OTEL_EXPORTER_OTLP_ENDPOINT`，`BatchSpanProcessor` 同时把 span 导出到该 OTLP 端点（本地为 Phoenix）；未配置时仅写 MySQL。

## 5. 与 V1 目标的已知差距

| V1 能力 | 现状缺口 |
| --- | --- |
| [可靠问答](superpowers/plans/2026-09-29-v1-roadmap.md#1-可靠问答) | 仓库内质量用例为空；SSE 为数据库轮询，无 token 级流式；摄取任务租约回收不递增 `attempt`（Turn 回收递增但无上限）、摄取瞬时失败直接永久失败、worker loop 无异常保护 |
| [知识图谱展示](superpowers/plans/2026-09-29-v1-roadmap.md#2-知识图谱展示) | 抽取分批（PR 1）与项目级图合并、按（project_id、version）缓存邻接表（PR 2）已实现，"每次查询把整个快照载入内存"已解决；项目合并每次只读取最多 100 个已就绪来源（`MysqlReadySources` 的 `limit(100)`），超过此数的项目无法在一次合并中纳入全部来源；剩余缺口为真实资料业务验证，详见[知识图谱脉络分析设计](superpowers/specs/2026-10-06-knowledge-graph-reasoning-design.md) |
| [图谱脉络分析](superpowers/plans/2026-09-29-v1-roadmap.md#3-图谱脉络分析) | 项目级图合并（PR 2）与关系分析 Agent 子图（多跳扩展、路径推理、关系边引用，PR 3）已实现；项目级图替代按来源集合即时合并快照、Agent 子图契约见 [ADR-031](decisions/2026-10-09-adr-031-project-graph-agent-subgraph-contract.md)；剩余缺口为前端边引用渲染与高亮待 PR 4，详见[知识图谱脉络分析设计](superpowers/specs/2026-10-06-knowledge-graph-reasoning-design.md)。已知局限：(a) 合并节点的 label/别名取全部片段成员中的多数值，可能反映未发布或未经审阅来源的措辞；(b) 种子选取与关系排序是确定性启发式，不是通用推理，规划器的关系意图判定本身也是启发式，复杂问题仍走模型规划器；(c) 当前运行时 `tapper_runtime.py` 中 `publication_authority` 为 `None`，这是 ADR-030（知识切片保存/索引/启用直接生效）既定设计，不是缺口——`RelationAnalysisAgent.authorize_evidence` 与检索增强的 `authorize_selection` 复检在该设计下本就不执行；流程图发布闸门另行生效：检索增强切片因为要经过一次正常的 `_retrieve` 全 ACL 检索才会被采纳，始终过流程图发布闸门，R 证据片段也由同一个 `FlowchartPublicationGate` 实例按锚点 `bbox` 过滤未审阅的流程图切片。 |
| [Skills/Agents](superpowers/plans/2026-09-29-v1-roadmap.md#4-skillsagents) | 工具白名单硬编码为 `knowledge.search` / `knowledge.answer`；无导入能力 |
| [可观测性](superpowers/plans/2026-09-29-v1-roadmap.md#5-可观测性) | 追踪数据无保留期与清理任务，`model_call_content` 原文永久保留会持续增长；DashScope 部分模型算不出成本，需要手动在 `deploy/local/litellm/config.yaml` 配置 `input_cost_per_token`/`output_cost_per_token` |
