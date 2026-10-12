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
3. worker 按阶段执行解析（`tap-parser`）、切片、embedding，每阶段产物内容寻址写入对象存储。PDF 解析先去掉页眉页脚（在过半页面的首尾行重复出现的行）、分隔线与页码，再把页边换行的行合并成段落（中文不加空格、英文加空格），并以条款编号（`第X条`、`一、`、`1.2` 等）建立标题路径；同一页内同一条款的段落组成一个块，块不跨页，锚点页码因此准确。切片不超过 512 token：PDF 块超限时只在段落或句末断开且不重叠，其他格式超限时仍按 token 窗口重叠切分。
4. 切片投影写入 Milvus；投影可从 MySQL 与中间产物重建。
5. 修订身份由 `PARSER_VERSION` 决定，解析行为变化记录在 `parser_config_digest`。解析器升级后，`make knowledge-reparse ARGS="knowledge reparse --project tapper-demo"` 为旧解析器生成的就绪 PDF 文档新建修订并重新入队（复用已存原件，旧修订及其产物保留，已有引用仍可解析；有手工切片的文档跳过）；文档在新修订就绪前不可检索，就绪后图谱按新修订重新抽取。运行前需用 `make parser-build` 重建解析器镜像。

**问答**

1. 用户在 Conversation 中提交问题，生成不可变 Turn。
2. 检索 → 关系分析子图（种子/扩展/路径/排序，R1..R20）→ 检索增强 ≤ 5 条 → 生成 → R/S 校验：worker 在授权范围内经 Milvus 混合检索（RRF 融合）取回切片后，关系分析 Agent 子图（`modules/ai/application/agents/relation_analysis.py`）在固定图版本内做种子（问题 + 证据切片；canonical key 以 `document:`/`chunk:` 开头的结构性节点不作种子）、扩展（1–2 跳）、路径（种子两两之间 ≤3 跳）、排序（路径优先、按置信度 × 种子邻接度，截断为 R1..R20）；检索增强对候选集外的邻居节点证据切片最多补 5 条（同样的 ACL 与发布授权，`TAPPER_GRAPH_RETRIEVAL_AUGMENT` 控制）；生成后对 claim 的 R/S 标签做位置与端点标签校验，非法标签剥离或整条丢弃（`TAPPER_GRAPH_REASONING` 为 0 时整段跳过，回退为纯切片回答）。子图结果经 `graph.context_ready` 事件与回答的 `graphContext` 下发：`seedCount` 为全部种子数，`querySeedCount` 为问题本身识别到的实体数；状态为 EMPTY 且 `querySeedCount` ≥ 2 时，前端在回答上方提示未找到直接关系证据。
3. 生成前若同一条款在不同来源修订中内容不一致，则以 `conflicting_sources` 弃答：条款由逻辑切片 ID，或同一文档谱系（同一来源或同名文件）内有意义的末级标题识别；分隔线/标点、少于 2 个字母数字字符、在比较的两个修订的证据中都标注 ≥ 3 个切片的页眉式标题不参与这两者之间的匹配，不同文档的同名通用标题（如“释义”）不视为同一条款。该判定在关系分析之后执行，冲突弃答的 `graphContext` 仍报告项目图状态（如 NOT_READY、EMPTY），而非表示图推理未启用的 UNAVAILABLE。经 LiteLLM 生成回答并校验引用，结果写回 MySQL；可选模型目录由 LiteLLM `GET /v1/model/info` 动态提供（60 秒缓存），新增模型只改 `deploy/local/litellm/config.yaml` 并重启 LiteLLM，后端无需改代码或重启。
4. 前端经 SSE 获取 Turn 事件（服务端轮询数据库，支持 `Last-Event-ID` 续传）。

**图谱**

1. graph worker 按文档修订分批（以字符预算为主，默认约 3000 字一批、超长的单个切片单独成批；块数上限为辅，默认 100 块，见 `TAPPER_GRAPH_BATCH_CHARS`/`TAPPER_GRAPH_BATCH_SIZE`）用模型抽取实体与关系（schema 与提示词限定每批至多 40 个节点、60 条边，只输出本批原文中出现的实体与关系，同一对节点同一关系类型只输出一条边；解析时丢弃重复节点（同 id 或同 canonical key）与重复边（同两端、关系类型与来源）并计入 span 属性；模型输出写满预算被截断、JSON 不完整或顶层 nodes/edges/provenance 超出上限时（别名超过 5 个只截取前 5 个，不拆批），网关报 `ModelOutputTruncated`，worker 对该批做一次二分重试（两半各自抽取、各自续租），某一半仍被截断则保留另一半并把作业记为 PARTIAL，两半都截断或单切片批次截断才记该批失败，且不再原样重试；拆半后因其他瞬时错误重试时直接重抽两半，不再先调用整批），关系限定为受控词表并带原文标签，批次结果持久化到 `graph_fragment_batch`，可重试与恢复；全部批次成功为 READY，部分失败为 PARTIAL（片段抽取）。抽取调用有独立的截止时间与输出预算（`TAPPER_GRAPH_EXTRACTION_TIMEOUT_SECONDS` 默认 240 秒、上限 300 秒；`TAPPER_GRAPH_EXTRACTION_MAX_TOKENS` 默认 8192；按每秒 40 token 估算，截止时间短于输出预算所需时间的组合在启动时被拒绝，以免可检测的截断变成超时再重试），问答等其他模型调用仍用 `TAPPER_MODEL_TIMEOUT_SECONDS` 与结构化输出默认 2048 token；graph worker 的作业租约随之放宽到抽取截止时间加 60 秒（默认 300 秒），避免单次调用期间租约过期。模型输入里每个切片只带批内短序号（`c1`..`cN`）与正文（JSON 不转义中文），输出的节点与边只用这些序号引用证据，修订 ID、切片 ID、anchor 与内容摘要由服务端按序号从本批授权切片回填，引用本批以外的序号整批失败。graph worker 同时处理至多 `TAPPER_GRAPH_WORKER_CONCURRENCY`（默认 4）个作业（每个作业对应一个文档修订），只在有空闲槽位时才领取作业；领取沿用 `FOR UPDATE SKIP LOCKED` 租约，同一作业不会被两个槽位或两个进程同时持有，作业内各批次仍按顺序执行（后续批次复用前面批次的实体：只把本批原文字面提到的已知实体发给模型，至多 50 个；模型只在边里引用其 id、不在 nodes 中重抄，重抄的副本由服务端丢弃并计数），因此同一批不会被重复抽取；模型网关的并发槽位随之放宽到不少于该值。
2. 片段发布（READY/PARTIAL）、知识发布/撤销发布、来源删除、单文档删除均把项目加入 `graph_project_merge_job` 合并队列；graph worker 的 `pending_work` 钩子串行认领、租约执行合并（复用 `MysqlGraphJobStore` 的 `FOR UPDATE` 租约与幂等模式），对项目内全部满足档案摘要的 READY/PARTIAL 片段做全量重放（merge 输入上限 100 个已就绪来源，见第 5 节已知差距）：三级实体对齐（规范化 canonical key 精确匹配 → 别名表匹配 → embedding 相似度，默认关闭；三级都要求被对齐的节点类型相同；指代词、结构词与编号（如“本合同”“第十九条”）由 `graph/domain/naming.py` 统一拒绝，抽取时不作别名或实体名，对齐时不作别名键；模型别名只在同一来源修订内匹配，跨来源修订只按名称相同合并，避免不同产品因各自的别名被合成一个节点）、边合并（按两端节点与关系类型合并，置信度取最大值、证据取并集）、标签传播社区划分，`fragment_digest` 不变则跳过，版本就绪后原子切换为 READY 并只保留一个旧版本（项目合并）。embedding 对齐在片段节点数（各片段 draft 节点数之和）超过 `TAPPER_GRAPH_ALIGN_EMBEDDING_MAX_NODES`（默认 2000）时跳过，退化为仅用一、二级对齐，以限定单次合并的 CPU 时间：第三级按节点类型把已放置成员的单位向量组成矩阵，每个节点一次矩阵向量积预筛、只对接近最高分的候选按原余弦公式复核（结果与逐簇全量扫描一致），整个合并在线程池中执行，不阻塞事件循环。
3. 项目图按（project_id、version）在进程内缓存邻接表，版本切换时失效；查询 API（总览、搜索、路径、节点详情、邻居、高亮）与回答侧关系分析 Agent 子图都读这份缓存；响应带 `graphVersion`，请求携带的 `graphVersion` 与当前不符时返回 409；关系分析子图在种子阶段与排序阶段之间检测到版本切换时返回 STALE，回答退化为纯切片且不带任何关系边引用。旧的按来源快照查询路由（`POST /query`（搜索）、`POST /nodes/{node_id}/neighbors`、`POST /path`、`GET /nodes/{node_id}`、`GET /evidence/{evidence_id}` 等，携带 `snapshotId` 时命中片段分支）仍留在后端作为兼容表面，但当前前端（`features/graph/api/client.ts`）从不发送 `snapshotId`，这些调用一律走项目图分支；前端只读项目图的总览、`POST /query`（搜索）、邻居、路径、节点详情与高亮接口。列出当前活跃快照的 `GET /snapshots` 已退役。graph worker 每次启动先做一次回填：已发布但没有抽取作业的修订补建作业，抽取档案摘要已过期（提示词或 schema 变更）的片段按当前档案重新排队（租约仍有效的运行中作业跳过），当前档案下 FAILED 的作业不自动重试；若没有任何需要抽取的修订、但已有当前档案的就绪片段，则请求一次项目合并，避免升级后项目图一直为空；各步均幂等（重新排队在行锁内复查摘要，已是当前摘要的作业原样返回），失败只记日志、不阻止 worker 进入主循环；摘要变化后重新抽取期间，项目图暂时只包含已完成重抽的片段子集，随后续片段完成逐步恢复（与 `graph rebuild` 的退化窗口相同）。因此 Library 空态"发布后自动构建"的提示在 graph worker 运行时成立。`graph rebuild` CLI 可手动触发重建：它只把已发布修订重置为待重新抽取，不会自己请求合并（合并由片段重新发布时各自请求），因此存在一个退化窗口——重建期间，一旦第一个重新抽取的片段完成重新发布，合并即会发布一个只包含当时已 READY 片段的新当前版本，图谱随之收缩到重新抽取的子集，再随后续片段逐个完成而逐步恢复；旧版本会保留一个周期，但查询路由只对外提供当前版本。拆半后丢失一半的批次记为 READY 并带 `failure_code=graph-batch-half-truncated`（作业为 PARTIAL），片段重试只重置 FAILED 批次，不会自动重抽这类批次；需要补抽时运行 `graph rebuild`。

本地 CI/E2E 用规则式假抽取（`TAPPER_GRAPH_EXTRACTION_MODE=fake`），演示与真实环境用 `model`。两种模式各有自己的抽取档案摘要（规则式抽取为独立的确定性摘要），同一进程配置在 READY 投影建作业、启动回填、合并输入过滤与 `graph rebuild` 中统一使用当前模式的摘要；切换模式后，另一模式的片段视为过期，不进入合并，并在下次 graph worker 启动时被重新排队抽取。

验收门禁：golden set 评测（`make quality-graph-relations`，CI 常驻，假抽取 + 确定性模型，只验证链路接线、R 边引用形状与评测脚本本身的正确性，不代表真实关系抽取准确率）、抽样导出（`make graph-sample-export`，opt-in，`TAP_RUN_GRAPH_SAMPLE_EXPORT=1`，人工核对边与跨来源合并的准确率）、`graph-bench`（合成 1 万节点/5 万边基准，opt-in，`TAP_RUN_GRAPH_BENCH=1`，不进默认 CI）；真实语料上的关系 golden set 评测（`make quality-graph-relations-real`，opt-in）需要人工标注且题目覆盖须包含若干 `expectedSources` 跨 2+ 份文档的问题，否则无法暴露"命中本题期望边的同时误引用另一份无关文档邻域边"这类离题引用；该目标还要求两个前置产物已就绪——`.local/graph-real/corpus.json`（`scripts/load-graph-real-corpus.py upload` 上传语料后产出）与基线观测（关闭 `TAPPER_GRAPH_REASONING`，即 `TAPPER_GRAPH_REASONING=0` 下运行一次 `run-graph-relations-candidate.py --mode real` 得到的观测文件，用于回归对比有依据率）。

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
| [可靠问答](superpowers/plans/2026-09-29-v1-roadmap.md#1-可靠问答) | 仓库内质量用例为空；SSE 为数据库轮询，无 token 级流式；摄取任务租约回收不递增 `attempt`（Turn 回收递增但无上限）、摄取瞬时失败直接永久失败、worker loop 无异常保护；来源冲突只在同一文档谱系（同一来源或同名文件）内判定，文件名不同的两份文档即使内容相互矛盾也不会以 `conflicting_sources` 弃答，原型中“两份不同文档相互矛盾”的场景在 V1 不触发（2026-10-11 决定接受，以保证多来源问答可用） |
| [知识图谱展示](superpowers/plans/2026-09-29-v1-roadmap.md#2-知识图谱展示) | 抽取分批（PR 1）与项目级图合并、按（project_id、version）缓存邻接表（PR 2）已实现；项目合并每次只读取最多 100 个已就绪来源（`MysqlReadySources` 的 `limit(100)`），超过此数的项目无法在一次合并中纳入全部来源；剩余缺口为真实资料业务验证，详见[知识图谱脉络分析设计](superpowers/specs/2026-10-06-knowledge-graph-reasoning-design.md) |
| [图谱脉络分析](superpowers/plans/2026-09-29-v1-roadmap.md#3-图谱脉络分析) | 项目级图合并（PR 2）、关系分析 Agent 子图（多跳扩展、路径推理、关系边引用，PR 3）与前端总览/边引用渲染/高亮（PR 4）已实现；项目级图替代按来源集合即时合并快照、Agent 子图契约见 [ADR-031](decisions/2026-10-09-adr-031-project-graph-agent-subgraph-contract.md)；关系 golden set 门禁工具（`scripts/evaluate-graph-relations.py`、`run-graph-relations-candidate.py`、`load-graph-real-corpus.py`、`graph-bench.py`，`make quality-graph-relations*`/`graph-bench`）已实现；真实门禁尚未运行，关系 golden set 待人工标注，详见[知识图谱脉络分析设计](superpowers/specs/2026-10-06-knowledge-graph-reasoning-design.md)。已知局限：(a) 合并节点的 label/别名取全部片段成员中的多数值，可能反映未发布或未经审阅来源的措辞；(b) 种子选取与关系排序是确定性启发式，不是通用推理，规划器的关系意图判定本身也是启发式，复杂问题仍走模型规划器；(c) 当前运行时 `tapper_runtime.py` 中 `publication_authority` 为 `None`，这是 ADR-030（知识切片保存/索引/启用直接生效）既定设计，不是缺口——`RelationAnalysisAgent.authorize_evidence` 与检索增强的 `authorize_selection` 复检在该设计下本就不执行；流程图发布闸门另行生效：检索增强切片因为要经过一次正常的 `_retrieve` 全 ACL 检索才会被采纳，始终过流程图发布闸门，R 证据片段也由同一个 `FlowchartPublicationGate` 实例按锚点 `bbox` 过滤未审阅的流程图切片；(d) 某来源的抽取全部失败（无任何成功片段）时，该来源既不在 `extractingRevisionIds` 也不在 `partialRevisionIds` 中，`GraphFragmentStatus`（文档详情页）因而将其与「空闲」状态混同，显示「无进行中的抽取 / No extraction in progress」而非失败提示——有待后端补充按来源的 `failedRevisionIds`（而非仅 `extractingRevisionIds`/`partialRevisionIds`）后，前端才能区分两者；(e) `GraphFragmentStatus`（PR 4 新增）已有中英两套文案；`DocumentDetail` 接受可选 `locale` 并转给它，但唯一调用方 `KnowledgeLibrary` 尚无 locale 来源（且当前未挂载于 Tapper 工作区），实际仍显示中文；`DocumentDetail` 自身其余文案仅中文（早于本工作）；(f) 当图谱规模超过总览画布单页可绘制的节点数时，一条被引用路径的节点/边可能落在当前总览分页之外——此时只有「回答引用的关系」文字列表能完整展示该路径，画布本身不会跨页高亮；后续应将高亮响应的节点/边直接并入总览子图而非仅叠加 `data-highlighted` 标记。 |
| [Skills/Agents](superpowers/plans/2026-09-29-v1-roadmap.md#4-skillsagents) | 工具白名单硬编码为 `knowledge.search` / `knowledge.answer`；无导入能力 |
| [可观测性](superpowers/plans/2026-09-29-v1-roadmap.md#5-可观测性) | 追踪数据无保留期与清理任务，`model_call_content` 原文永久保留会持续增长；DashScope 部分模型算不出成本，需要手动在 `deploy/local/litellm/config.yaml` 配置 `input_cost_per_token`/`output_cost_per_token` |
