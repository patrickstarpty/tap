# 知识图谱脉络分析设计

日期：2026-10-06。归属 [V1 总纲](../plans/2026-09-29-v1-roadmap.md) 能力 2"知识图谱展示"与能力 3"图谱脉络分析"，合并为一个子项目实施。背景评审见 [V1 交付物评审](../../reviews/2026-10-06-v1-delivery-review.md) 第 5 节。

## 背景

现有图谱只能展示，不参与回答：

- 本地演示默认 `TAPPER_GRAPH_EXTRACTION_MODE=fake`，假抽取只产出"Section N"节点与 CONTAINS 边，没有实体与关系。
- 模型抽取把一个文档最多 500 个切片打成一次结构化调用，超时沿用 `model_timeout_seconds`，真实文档要么超时要么输出被截断。
- 快照按单文档建，回答与前端却按"所选来源集合的精确摘要"查快照，选两个以上来源时查不到图。
- 回答侧 `GraphAnswerEnricher` 用整句问题做节点 label 子串匹配，自然语言问题几乎不命中；命中时也只把最多 20 个节点的诱导子图作为 JSON 塞进上下文，边不可引用。
- 没有跨文档实体对齐、没有受控关系词表、节点表没有索引、每次查询整快照载入内存。

可保留的资产：节点/边/证据/推理来源的数据模型与 MySQL 表、graph job 的租约与幂等、`neighbors` 与 `bounded_path` 算法、claims 带 `evidenceLabels` 的回答结构与 grounding 校验、图谱 HTTP 路由、前端画布与 forceAtlas worker。

## 目标

2026-10-06 决定：本设计是 V1 的验收门禁，优先级为 **A 关系问答 > B 检索增强 > C 知识总览**，三者共用一张项目级图。

- A：用户问"A 和 B 什么关系""哪些规则影响 C""流程中 X 之后是什么"，回答逐句引用关系边与原文切片，点击引用在图谱中高亮路径；找不到关系证据时明确说明。
- B：普通问题借图谱邻居补充候选切片，有依据率不低于加入图谱前。
- C：Library 默认显示项目知识总览：真实社区、主要实体、关系；可从总览追问。
- 关系分析实现为第一个**契约式 Agent 子图**（类型化输入输出、独立 span、不与其他 Agent 对话），后续测试设计等 Agent 沿用同一协议。

## 非目标

- 不引入 LangGraph 工具循环与 orchestrator 路由改造（Skills/Agents 子项目负责），本设计的回答侧是确定性流水线。
- 不做 GraphRAG 式社区摘要回答（摘要不可逐句引用），可作后续增量。
- 不做模型抽取之外的 OCR 或图片实体识别；流程图图片知识沿用现有流程图路径。
- 不改认证、部署与多项目切换。

## 1. 数据模型与建图流水线

### 1.1 抽取（按文档修订）

- graph job 仍由 `MysqlGraphReadyProjection` 在文档 READY 事务内创建，每个修订一个 job。
- worker 把修订的切片按顺序切成批次，每批 `TAPPER_GRAPH_BATCH_SIZE`（默认 10）个切片，每批一次 `generate_structured` 调用。调用上下文包含：文档标题、本批切片、**本修订此前批次已抽出的实体清单**（id、label、type、canonicalKey），要求模型复用已有实体 id，保证同一文档内 canonical key 稳定。
- 批次幂等键为（修订 id、批次号、抽取档案摘要）。批次失败按退避重试，`TAPPER_GRAPH_BATCH_RETRIES`（默认 3）次后标记该批失败并继续后续批次。全部批次成功时片段状态 READY；有失败批次时 PARTIAL，仍可合并；全部失败时 FAILED。
- 批次结果合入同一修订的片段图（现有单文档快照表继续承载，称为**片段图**）；片段图的 `snapshot_id` 不变，批次表记录每批的状态、失败码、重试次数。
- 节点类型：现有 ACTOR、SYSTEM、REQUIREMENT、CONCEPT、ENTITY 之外增加 PROCESS（流程、步骤、环节）。
- 关系类型受控词表：REQUIRES、APPLIES_TO、PART_OF、EXCEPTION_OF、SUPERSEDES、TRIGGERS、PRECEDES、VALIDATED_BY、RESPONSIBLE_FOR、USES、DEFINES、CONFLICTS_WITH，兜底 RELATED_TO。模型同时输出自由文本 `relationLabel`（原文表述，≤64 字）供展示；`relation_type` 不在词表内的输出整条边作废并计入批次诊断。
- 抽取 schema 在现有基础上增加 `relationLabel`、节点 `aliases`（模型从本批文本中识别的别名，≤5 个）。推理边（INFERRED）与推理来源机制保留，不扩展。
- 抽取提示词与 schema 摘要更新后，`GRAPH_EXTRACTION_PROFILE_DIGEST` 随之变化，旧片段视为过期（见第 5 节重建）。

### 1.2 项目级图与合并

新增表（迁移 `0028_project_graph`，列出主要列）：

| 表 | 主要列 | 说明 |
| --- | --- | --- |
| `graph_project_version` | project_id、version、status（MERGING/READY/FAILED）、fragment_digest、node_count、edge_count、merged_at | 每次合并产生一个版本；`fragment_digest` 为参与合并的片段集合摘要 |
| `graph_project_node` | project_id、version、node_id、label、node_type、canonical_key、degree、community_id | 主键（project_id、version、node_id）；索引（project_id、version、canonical_key）、（project_id、version、community_id） |
| `graph_project_edge` | project_id、version、edge_id、source_node_id、target_node_id、relation_type、relation_label、origin、confidence | 索引（project_id、version、source_node_id）、（project_id、version、target_node_id） |
| `graph_project_node_source` | project_id、version、node_id、source_revision_id、document_revision_id、chunk_id、anchor_json、fragment_node_id | 节点在每个来源中的证据，用于授权过滤与"打开原件" |
| `graph_project_edge_evidence` | project_id、version、edge_id、source_revision_id、document_revision_id、chunk_id、anchor_json、content_digest、fragment_edge_id | 边证据 |
| `graph_project_alias` | project_id、version、alias_norm、node_id、origin（LABEL/MODEL/MERGE） | 别名索引，`alias_norm` 为规范化串 |
| `graph_project_community` | project_id、version、community_id、label、size | 社区；`label` 取社区内度数最高节点的 label |
| `graph_fragment_batch` | revision_id、batch_index、status、attempt、failure_code、updated_at | 批次状态 |
| `graph_merge_log` | project_id、version、node_id、merged_from（JSON：片段 id 与片段节点 id 列表）、rule（EXACT/ALIAS/EMBEDDING） | 合并审计 |

合并规则：

- 触发：片段 READY/PARTIAL、来源删除或撤销发布、`graph rebuild` 命令。合并 job 按项目租约串行，复用 `MysqlGraphJobStore` 的租约与幂等模式（新增 job 种类 `project_merge`）。
- 合并是**全量重放**：取项目内当前已发布修订的全部 READY/PARTIAL 且档案摘要为当前值的片段，重新计算一个新版本；删除来源即重放时排除。片段集合摘要与上一版本相同则跳过。版本就绪后原子切换 `graph_project_version.status=READY`，旧版本保留一个用于查询过渡，更早版本清理。
- 实体对齐三级，依次尝试：
  1. 规范化 canonical key 精确匹配。规范化：casefold、全角转半角、去标点与空白、去尾部修饰词（"规则""条款""流程""policy""rule" 等有限列表）。
  2. 别名表匹配：任一规范化别名（label、模型给出的 aliases、历史合并产生的别名）相同且节点类型相同。
  3. embedding 相似度 ≥ `TAPPER_GRAPH_ALIGN_THRESHOLD`（默认 0.92）且节点类型相同；由 `TAPPER_GRAPH_ALIGN_EMBEDDING` 控制，第一版默认关闭。
  合并后的节点 label 取出现次数最多的片段 label，其余进别名表；每次合并写 `graph_merge_log`。
- 边合并：两端节点合并后、`relation_type` 相同的边合并为一条，置信度取最大值，证据取并集，`relation_label` 取出现次数最多者。
- 社区：标签传播算法，迭代上限 20 轮，社区数上限 50，小于 3 个节点的社区并入"其他"；节点度数同时写入。

### 1.3 查询与缓存

- `ProjectGraphStore` 按（project_id、version）在进程内缓存邻接表、节点字典与别名索引；版本切换时失效。节点详情、邻居、路径、按切片反查节点、别名匹配都走缓存。
- 缓存装载走表而非整快照对象图；1 万节点、5 万边装载一次应在 1 秒内完成，之后查询 p95 < 300ms（见第 4 节）。
- 旧的 `active_snapshot(source_ids)` 语义废弃；片段快照路由仅保留给文档详情查看片段状态。

### 1.4 规则式假抽取

`DeterministicGraphExtraction` 改为规则式：从切片文本按显式句式抽取实体与关系（中文"A 需要 B""A 适用于 B""A 之后是 B"，英文"A requires B""A applies to B""A precedes B" 等，与词表一一对应），实体类型按关键词启发式判定。接口不变，CI、E2E 与 fixture 用它跑完整流水线。

## 2. 回答链路与引用模型

### 2.1 路由

- `AnswerPlanner` 意图集合增加 `relation`，路由值增加 `graph`。规划器仍是一次结构化调用。
- 有依据问答且项目图 READY 时，关系分析子图总是运行；`relation` 意图下关系证据排在切片证据之前，且无关系证据时回答前置说明"未在已选资料中找到 A 与 B 的直接关系"。

### 2.2 Agent 子图契约

```python
class AgentSubgraph(Protocol[InputT, OutputT]):
    name: str                      # 例如 "relation-analysis"
    input_schema: type[InputT]     # 冻结 dataclass
    output_schema: type[OutputT]
    async def run(self, context: AgentContext, value: InputT) -> OutputT: ...
```

- `AgentContext` 携带 scope、授权的来源修订集合、项目图版本、span 根；子图在自己的 span 内运行，失败时返回带状态的输出而不是抛出。
- 子图之间不直接通信，只经调用方传递类型化对象；证据对象原样传递，不经模型转述。
- 子图在 `modules/ai/application/agents/` 下注册，调用方现阶段是 `knowledge_service` 的确定性流水线，Skills/Agents 子项目的 orchestrator 复用同一注册表。

### 2.3 关系分析子图

输入 `RelationAnalysisInput`：standalone query、授权来源修订集合、检索证据（S 标签、chunk id、来源修订）、项目图版本。
输出 `RelationContext`：状态（APPLIED/NOT_READY/STALE/FAILED/EMPTY）、种子节点、边列表（已编号 R1..Rn）、路径列表、诊断计数。全程确定性，不调用模型。

1. **证据种子**：检索命中 chunk id 经 `graph_project_edge_evidence` 与 `graph_project_node_source` 反查节点。
2. **问题种子**：对规范化后的问题做别名最长匹配（别名索引在内存，按长度降序，匹配后遮蔽已匹配区间），不依赖分词器。
3. **扩展**：全部种子 1 跳；种子少于 3 个时 2 跳。节点与边必须至少有一条证据落在授权来源修订内，且通过现有发布授权校验（`PublishedKnowledgeAuthority`）。节点上限 60、边上限 200。
4. **路径**：问题种子两两之间、问题种子与证据种子之间求不超过 3 跳的有界路径，路径数上限 10。
5. **排序与截断**：路径上的边优先，其次按 `confidence × 种子邻接度`，截断为最多 20 条，编号 R1..R20。
6. **组装**：每条关系证据 `{label, subject, relationType, relationLabel, object, origin, confidence, evidence}`；`evidence` 指向支撑切片，已在 S 列表中的用 S 标签引用，否则附切片片段（≤300 字）与锚点。

### 2.4 模型回答

- `_ANSWER_PROMPT` 增加关系证据段落，沿用 `_FLOWCHART_ANSWER_PROMPT` 对结构化记录的处理方式：关系证据是记录而非可引用的句子；描述两个实体之间关系的 claim 必须引用对应 R 标签，可同时引用 S；没有 R 支撑不得陈述关系。`relation` 意图且 `RelationContext` 为 EMPTY 时不生成额外 claim（每个 claim 仍须有证据标签），"未找到直接关系"由 `graph.context_ready` 事件的状态驱动前端提示（见 3.3）。
- 回答仍由 claims 重建，输出 schema 不变，`evidenceLabels` 允许 R 标签。

### 2.5 引用模型与校验

- `Citation` 增加 `kind`：`chunk`（现状）或 `edge`。边引用字段：`edge_id`、`graph_version`、`subject`（node_id、label）、`object`（node_id、label）、`relation_type`、`relation_label`、`evidence`（支撑切片的 chunk id、来源修订、内容摘要、锚点）。边引用最终仍锚定到原文。
- `ports/answers.py` 校验：S 标签保持 `S1..S20` 位置规则；R 标签 `R1..R20`，必须对应本次 `RelationContext` 中的边；含 R 引用的 claim 文本须包含该边两端节点的 label 或任一别名；边引用的 `graph_version` 必须等于本次使用的版本。
- 校验失败处理：某个 claim 的 R 引用不合法时，去掉该 claim 的 R 标签后按 S 标签重新校验；仍有 S 标签则保留该 claim 并计入诊断 `invalid-relation-citation`，没有任何合法标签则丢弃该 claim；全部 claim 被丢弃才弃答。
- 持久化：`citation_snapshot` 增加可空列 `citation_kind`、`graph_version`、`edge_id`、`subject_node_id`、`object_node_id`、`relation_type`、`relation_label`；历史行 `citation_kind` 默认 `chunk`。
- 推荐问题的依据校验复用对话回答链路，自动支持 R 引用；刷新输入在"主要实体"之外加入"主要关系"（按度数与置信度取前 20 条）。

### 2.6 检索增强（B）

- 扩展得到的邻居节点若有证据切片不在候选集中，最多补 5 条进入候选池，走同样的 ACL 与发布授权，参与融合排序并获得 S 标签。由 `TAPPER_GRAPH_RETRIEVAL_AUGMENT` 控制，默认开。

### 2.7 事件、可观测性与失败处理

- 新增流事件 `graph.context_ready`，payload：状态、种子数、路径摘要（最多 3 条，每条为 label 序列）、R 数量；`citation.resolved` 的 payload 带 `kind`。
- span：`graph.seed`、`graph.expand`、`graph.path`、`relation.rank`，挂在 `turn.execute` 下，属性记录数量与耗时；替换现有 `graph.enrich`。
- 项目图未就绪、合并中、查询异常分别为 NOT_READY / STALE / FAILED，回答退化为纯切片路径，`graph_context_status` 照常写入 Turn。图谱永不阻塞回答。`TAPPER_GRAPH_REASONING=0` 时整段跳过，作为回滚手段。

## 3. API 与前端

### 3.1 API（前缀 `/api/v1/projects/{project_id}/knowledge/graph`）

| 方法与路径 | 作用 |
| --- | --- |
| `GET /project` | 项目图元信息：版本、状态、节点与边数、社区列表、最近合并时间、抽取中与部分失败的来源 |
| `GET /overview` | 总览子图。参数：`sourceRevisionId[]`、`communityId[]`、`nodeLimit`（默认 150）。按社区取度数最高节点及其间的边 |
| `POST /query` | 按 label 与别名搜索（前缀与包含），可按来源过滤，返回命中节点及其间边 |
| `POST /neighbors`、`POST /path` | 保留，改为项目图语义 |
| `GET /nodes/{node_id}` | 节点详情：类型、别名、社区、度数、证据按来源分组并带锚点、按关系类型分组的邻接边 |
| `POST /highlight` | 输入边 id 列表，返回这些边、端点及 1 跳有界上下文 |
| `POST /fragments/{revision_id}/retry` | 重试失败批次 |

- 所有响应带 `graphVersion`；请求可带 `graphVersion`，与当前不符返回 409 让前端刷新。
- `GET /snapshots` 退役（第 4 个 PR）。`make contracts` 再生成 OpenAPI 与 TypeScript。

### 3.2 Library · Knowledge Graph

- 默认即总览。左栏为真实社区列表（数量、勾选），底部显示"N 个来源仍在抽取 / M 个部分失败"；替换 `publishedGraphData.sourceCommunity` 的文件名正则分组。
- 画布：节点按度数定大小、按社区着色，边标签在悬停或放大到阈值后显示；复用 forceAtlas worker，初始位置按社区分区。默认 150 节点，可加载更多。
- 搜索走别名索引，结果点击居中并高亮，清空恢复总览。来源过滤复用 Library 顶部筛选；去掉"Published source graph"切换。
- 节点详情面板：类型、别名、来源、按关系类型分组的关系、证据片段与"打开原件"；"就此提问"把节点 label 填入输入框并把其来源加为 chip。
- 文档详情显示片段图状态（抽取中 / 就绪 / 部分失败），部分失败可重试。

### 3.3 对话中的关系引用

- 边引用句内渲染为 `[R2]` 小标，与切片引用 `[1]` 并列；悬停卡片显示"A —关系→ B"与支撑切片片段。
- 点击打开右侧证据面板：上半部分是本回答全部边引用构成的迷你路径图，下半部分是切片原文；"在 Library 中查看"跳转图谱并进入高亮态（边 id 经路由状态传递，画布高亮路径、淡化其余、自动适配视口）。
- 回答摘要行改为"搜索 N 个来源 · M 段原文 · K 条关系"，展开可见种子实体与路径（来自 `graph.context_ready`）。
- `relation` 意图但无关系证据：回答上方提示"未找到直接关系证据，以下为资料原文依据"。
- 高亮不只靠颜色：证据面板以文字列出路径。

### 3.4 回归

- `ui:capture` 新增：总览、节点详情、带边引用的回答、高亮态。
- E2E：上传两份有交叉实体的 fixture 文档 → 总览出现跨文档节点 → 关系问题得到 R 引用 → 点击后 Library 高亮路径。

## 4. 验收门禁与测试

### 4.1 真实语料

- `tests/fixtures/quality/graph-real/manifest.json` 记录 8 份公开友邦保险条款的下载地址与校验和，脚本拉取到 `.local/graph-real/`，不提交 PDF 本身；另补 2–3 份流程类文档（核保、理赔），形成有跨文档实体的语料。真实项目资料到位后换用并复验。

### 4.2 关系类 golden set（门禁主体）

- 20–30 条，JSON 字段：问题、期望实体、期望关系边（主语、关系类型、宾语，允许等价替代列表）、期望来源。人工标注，记录标注人与日期。
- 评测脚本 `scripts/evaluate-graph-relations.py` 比对回答 claims 的 R 引用与期望边，输出每题的正确边、遗漏边、错误边。**通过标准：≥80% 的题目至少命中一条期望边且无错误边；出现错误边的题目不通过。**
- 同时运行现有 17 条推荐问题与一组普通问题，有依据率不低于加入图谱前。

### 4.3 抽取与对齐质量（人工抽样）

- 脚本随机导出 50 条边及证据片段为 CSV，人工核对，准确率 ≥85%。
- 导出 30 个跨来源合并实体及其别名与来源，误合并 ≤5%。
- 结果记入门禁报告（`docs/reviews/` 下的后续记录）。

### 4.4 性能

- 合成生成器写入 1 万节点、5 万边；邻居、路径、总览查询 p95 < 300ms；make 目标 `graph-bench`，不进默认 CI。

### 4.5 自动化层次

- 单元：规范化与别名匹配、扩展与路径、排序截断、R 标签校验（含反例：引用不存在的边、claim 不含端点实体、版本不符）、规则式假抽取、合并对齐三级规则。
- contract：引用 `kind`、新图谱路由、`graph.context_ready` 事件的 schema 与 OpenAPI 一致。
- 集成（MySQL）：合并全量重放、删除来源后重放、版本切换与缓存失效、授权过滤排除未选来源的边、批次失败与重试。
- E2E：3.4 节旅程。
- 真实模型 smoke：`TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1` 下用模型抽取跑 2 份 fixture 文档并回答一条关系问题。

## 5. 迁移、配置与发布顺序

### 5.1 迁移

- `0028_project_graph`：第 1.2 节新表；`citation_snapshot` 新增可空图谱列。旧快照表保留为片段存储。
- 升级后已有片段均为旧档案摘要，不参与合并。操作命令 `graph rebuild --project <id>`（挂在现有 `knowledge-recover` 风格 CLI 下）按项目重新排队全部已发布修订的抽取，限速执行。

### 5.2 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TAPPER_GRAPH_EXTRACTION_MODE` | `.env.example` 改为 `model`；CI 与 E2E 为 `fake` | 抽取方式 |
| `TAPPER_GRAPH_BATCH_SIZE` | 10 | 每批切片数 |
| `TAPPER_GRAPH_BATCH_RETRIES` | 3 | 批次重试次数 |
| `TAPPER_GRAPH_ALIGN_EMBEDDING` | 0 | 第三级 embedding 对齐开关 |
| `TAPPER_GRAPH_ALIGN_THRESHOLD` | 0.92 | 对齐相似度阈值 |
| `TAPPER_GRAPH_OVERVIEW_LIMIT` | 150 | 总览节点上限 |
| `TAPPER_GRAPH_RETRIEVAL_AUGMENT` | 1 | 检索增强开关 |
| `TAPPER_GRAPH_REASONING` | 1 | 关系分析总开关（回滚手段） |

### 5.3 发布顺序

五个 PR，每个通过 `make check` 与 `make test`，第 2、4 个另跑 `make demo-e2e`：

1. 抽取分批与批次表、受控词表、PROCESS 类型、规则式假抽取。
2. 项目图表与合并 job、别名与社区、邻接表缓存、新读接口；`GraphAnswerEnricher` 改读项目图（修复整句匹配与多来源问题）。
3. 关系分析子图与 Agent 契约、R 引用与校验、引用持久化、流事件、推荐问题输入加主要关系。
4. 前端总览、节点详情、边引用与高亮；退役 `GET /snapshots`；`ui:capture` 新增截图。
5. golden set、评测脚本、抽样导出脚本、性能基准、门禁记录。

### 5.4 文档

- `docs/architecture.md`：图谱数据流改为片段抽取 → 项目合并 → 回答种子/扩展/路径，更新已知差距表。
- V1 总纲：能力 2、3 验收改为本设计第 4 节；产品路线图子项目 4、5 合并为"知识图谱脉络分析"并标进行中。
- ADR-031：项目级图替代按来源集合的快照，Agent 子图契约（跨 knowledge、graph、chat 模块）。

## 风险

- 模型抽取的词表遵从度不稳定：边级别作废而非整批作废，批次诊断计数进 span，门禁的边准确率抽样会暴露问题。
- 实体对齐过度合并：第三级默认关闭；合并日志可审计；误合并 ≤5% 是门禁项。
- 全量重放成本随项目增长：目标规模（数十份文档、万级节点）下单次合并应在分钟内完成；超出后再考虑增量合并。
- R 引用校验过严导致弃答增多：校验失败回退为仅 S 引用重建，而非整段弃答；门禁同时监控有依据率。
