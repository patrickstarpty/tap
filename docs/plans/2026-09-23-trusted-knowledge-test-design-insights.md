---
status: planned
date: 2026-09-23
---

# 可信知识、测试设计与基础 Insights 实施计划

> **执行方式：** 按任务使用 `executing-plans` 技能执行；各任务用复选框记录进度。只有另行明确选择并行实施时才使用 `subagent-driven-development`。本次产出是计划，不启动功能实施。

**Goal:** 在完整产品原型基础上交付“资料核对发布 → 有据问答 → 测试方案生成与人工评审”及“外部真实报告 → 基础 Insights → 有据解释”两条可独立验收的业务路径。

**Architecture:** TAP AI 负责知识、会话、AI 编排和测试设计；TAP 负责报告接收、分析事实和 Insights API，两者通过版本化 API 和服务身份协作，不互写业务表。MySQL 保存权威状态、审批、任务与接收账本，对象存储保存原件与证据，Milvus 保存可重建知识投影；基础 Insights 从第一批真实数据开始使用 ClickHouse。

**Tech Stack:** 沿用 Python 3.13/FastAPI、React/TypeScript/Vite/Ant Design、MySQL、Redis、对象存储、Milvus、ModelGateway/LiteLLM；按任务增加 LangGraph、ClickHouse 及 Insights 所需 ECharts。具体依赖版本在实现任务中验证兼容并锁定，不因本计划改动依赖。

**Spec:** [RFC-011](../proposals/2026-09-17-rfc-011-rag-test-design-cross-platform-automation.md)、[AI 与知识专题](../reference/2026-09-22-rfc-011-ai-knowledge-design.md)、[测试管理专题](../reference/2026-09-22-rfc-011-testing-execution-design.md)、[Insights 与恢复专题](../reference/2026-09-22-rfc-011-insights-contracts-design.md)、[完整产品原型基准](../reference/2026-09-22-product-prototype-baseline.md)。

## 1. 范围与事实基线

### 本次范围决定

2026-09-23 用户要求关注“可信知识与测试设计、Web 与基础 Insights”，并进一步明确 **Web 自动化放到后面**。因此本计划保留测试设计，优先建设可信知识和外部报告 Insights；自建 Web 录制、调试、脚本生成、Jenkins 正式执行均不作为本轮依赖或验收条件。“Web”在本轮只包含这些能力的浏览器产品界面。

RFC-011 已于 2026-09-23 经用户确认审批通过，状态为 `accepted`。9 月 22 日收窄后的近期范围原本不含测试设计扩展，本计划依据本次用户指示增加既有测试设计闭环的收口；Web 自动化继续延后。方案审批不等于实施完成；下列首批来源、文件范围、接口与性能预算仍由 Task 0 用真实输入冻结，本计划保持 `planned`，直至实际开始实施。

| 本轮必须交付 | 本轮边界 | 后续保留 |
| --- | --- | --- |
| 可信知识 | PDF/DOCX/MD/TXT 文字资料的完整性清点、核对、独立复核、发布/撤回、引用与多轮问答；混合文件缺失必须可见 | OCR、PNG/JPEG、Excel/CSV、图像索引/重排及完整表格计算按真实样本另启扩展任务 |
| 测试设计 | 已有 Test Plan/BDD 草稿生成、需求范围确认、编辑、冲突恢复、业务评审、人工发布、知识引用追溯；修复 V2/V3 门禁缺口 | 独立用例库、三模板迁移、手工执行周期、完整数据组合、Jira 写入/Forge |
| 基础 Insights | 一个已有 CI 来源的真实 JUnit XML＋接收清单；原件/账本、身份去重、更正、ClickHouse、运行/失败/趋势与 AI 解读 | Allure 独立适配、更多 CI/缺陷/代码质量连接器、完整评分/发布门禁、自动 RCA 跟进 |
| 产品与运行 | 复用现有界面、统一图的必要剖面、项目隔离、恢复、备份重建；团队入口前完成真实身份与授权 | 主动 Agent 订阅/收件箱、完整 Skills/Agents 资源包、Android/iOS、Load Testing |

先保持现有文字文件支持范围，是一个可逆交付切分，不是取消 RFC 的复杂文件目标。Task 0 如确认关键业务资料必须依赖扫描图或 Excel，须把对应解析扩展的输入、接口、质量集与资源单独补齐并纳入知识出口；不能在缺失关键资料时宣布业务流程完成。

### 已有实现与缺口

核对基准：完整原型恢复提交 `3519ec8`、RFC 近期收窄提交 `3e61444`，以及 2026-09-23 工作区源码。此处为静态核对，未重跑历史 Gate。

| 领域 | 可复用的实际基础 | 本轮必须补齐 |
| --- | --- | --- |
| 知识 | `apps/tap-ai-backend` 已有来源/文档版本、文字解析、Milvus、引用、会话/SSE 与 ModelGateway | 逐页/对象完整性、批准范围、独立复核、发布原子切换、撤回后的读取与交付复验 |
| 图谱 | MySQL Graph 与抽取、查询、前端探索已存在 | 多 Revision 选择集与 Snapshot 一致性；原 V2 PASS 已撤销 |
| 测试设计 | Test Plan 修订、内嵌 BDD 用例、生成 Job、发布校验及真实 API | Job 成败/深链接、可编辑评审、冲突 reload、需求分母和真实输出逐例人审；原 V3 PASS 已撤销 |
| 原型 | `/prototype` 的 `DocumentReview.tsx`、`TestAnalyticsWorkspace.tsx` 已表达核对/发布、报告接入和分析交互 | 当前核对状态在浏览器，分析数据来自 fixture；不能直接认定为审批系统或真实历史 |
| TAP 后端 | `apps/backend/src/tap_platform/app.py` 当前仅健康检查 | 自己的持久化/迁移、授权、接收 Worker、Outbox、ClickHouse 与 API；不得导入 TAP AI 内部实现 |
| AI 编排 | ADR-029 已接受统一 LangGraph 目标 | 当前尚无目标图和持久检查点；不得把现有 60 秒生成 Job 领取等同于长任务恢复 |

现行证据以 [V2/V3 更正评审](../reviews/2026-09-14-v2-v3-gate-correction.md) 为准。V0/V1 历史结论保留；新知识审核能力另行验收。旧 [RFC-009 实施计划](2026-09-04-tapper-knowledge-web-automation-platform.md) 和[路线图](2026-08-20-roadmap.md)保留原生命周期；本计划是本轮增量排期入口，不取消其历史任务或提前放行 V4/V5。

## 2. 全局约束与产品映射

- TAP AI 修改落在 `apps/tap-ai-backend` / `apps/tap-ai-frontend`；TAP Insights 落在 `apps/backend` / `apps/web`。前端访问各自后端，TAP AI 查询 TAP 必须经服务 API 双重授权。
- 唯一体验基准为 `apps/web/src/widgets/tap/TapProductPrototype.tsx` 的 `/prototype`。增量完善已有模块；保留 Tapper、Agents、Skills、Library、Knowledge Graph、Test Management、Insights、Low Code Automation 和悬浮助手，不另建产品壳。
- 原型固定数据只服务交互与截图，隔离测试数据不能进入真实分析。截图或 fake 模型不代替真实能力证据。产品屏幕不放演示开关、模拟场景和实现说明。
- 已发布资产不可原地改写；批准绑定版本、指纹和来源；撤回/改权在检索、引用、缓存、图扩展和回答交付时重新检查。
- Task/GraphRun、checkpoint、领域事件和 Outbox 同事务；Redis 只唤醒。不得以循环重试掩盖外部响应未知。
- ClickHouse 从基础 Insights 开始使用；不能先做 MySQL 权威指标再迁移。模型不能直查业务库、ClickHouse 或 Milvus，也不能执行任意 SQL。
- 不增加 PostgreSQL、图数据库、Temporal、第二工作流平台或通用 Agent 平台。可选 chDB 不在本轮主链。
- 当前 loopback/无认证边界不扩大。真实多人审批或团队试点必须先验收身份、项目授权、模型外发、凭证和恢复。固定 Validation Actor 不能冒充两个真实审核人。
- 只做追加迁移和可回放投影，保留旧原件、会话与计划修订；不得 `demo-reset` 清数据。未接受的新架构决策用新 ADR 处理，不改写旧 accepted ADR。

| 用户旅程 | 设计参照 | 实际实现落点与验收 |
| --- | --- | --- |
| 上传 → 原件对照 → 核对/复核 → 发布 | `prototype/LibraryWorkspace.tsx`、`prototype/DocumentReview.tsx` | TAP AI Knowledge Library / DocumentDetail；刷新仍有状态，不能三项复选框直接伪造批准 |
| Tapper 问答 → 点击来源 → 原文 | `prototype/TapperChat.tsx`、`KnowledgeSourcesPanel.tsx` | 真实 Conversation、SearchPort、CitationViewer；支持无依据、冲突、过期、撤回 |
| Tapper/测试管理发起 → 草稿 → 评审发布 | `prototype/testManagement/TestManagementWorkspace.tsx` | 两个入口进入同一生成服务；同一个 Plan/Revision 可深链、编辑和恢复 |
| 接收报告 → 看板 → 运行/尝试 → 失败证据 | `legacy/ReportIntakePrototype.tsx`、`legacy/TestAnalyticsWorkspace.tsx` | TAP 接收与 Insights API；汇总和明细同筛选，展示完整性和截至时间 |
| 失败 → Ask Tapper → 知识解释 | 既有悬浮助手/会话交接 | 只传受授权查询/资源引用；AI 再查询同一指标 API，分别展示事实、假设与缺失 |

原型可选严格 Test Plan ↔ Automation `1:1`、共享 Run 与双向跳转继续保留；本轮不借测试设计收口更改该关系，也不将其扩展成已落地跨产品运行模型。

## 3. 批次、依赖与角色

| 批次 | 任务 | 可独立评审的出口 | 主责 |
| --- | --- | --- | --- |
| M0 输入与边界 | 0 | 来源样本、字段映射、需求清单、授权/契约及质量口径冻结 | 产品/QA、架构、数据源负责人 |
| M1 可信知识 | 1–6 | 核对发布后可问答，撤回与恢复可靠；知识质量实测 | TAP AI 后端/前端、QA、业务复核人 |
| M2 测试设计 | 7–8，依赖 3–6 | 可编辑且有人审的测试计划；V2/V3 用新证据重新判定 | TAP AI 团队、QA、业务负责人 |
| M3 基础 Insights | 9–12，可与 M1 并行 | 外部真实报告在 ClickHouse 形成可追溯指标和页面 | TAP 后端/前端、数据源负责人 |
| M4 联合解释与交付 | 13–14，依赖 M1/M3；完整范围还需 M2 | 有据解释、恢复/隔离、整体旅程与试点结论 | 两产品团队、QA、运维/安全 |

依赖链：`0 → 2 → 3 → 5 → 6 → 7 → 8`；`0 → 4 → 6/7/13`；`0 → 9 → 10 → 11 → 12`；`6 + 11 + 12 → 13`。Task 1 在真实多人评审、跨产品服务调用和团队开放前完成；单人隔离验证可以先进行结构测试。Task 14 汇集所有适用出口。

这表示工作依赖可并行，不要求使用多个执行 Agent。没有团队人数、真实样本和环境承诺前不虚构日历工期；M0 后由各主责对任务估算，再按依赖确定日期。任一批次未通过，仅阻断依赖它的任务；外部报告分析不等待 V2/V3 或自建 Web 执行。

## 4. 先冻结的跨任务契约

以下名称为拟新增接口，不表示已有实现；Task 0 冻结后，各任务不得分别改名或另造口径。既有公开路径和 DTO 保持兼容，新字段由服务端模型生成。

### 知识与任务

| 对象 | 核心字段/规则 |
| --- | --- |
| `ParseInventoryItem` | `source_revision_id, item_id, locator, status, reason, artifact_digest`；status 为 `parsed/failed/needs_review/excluded`，排除必须留人和原因 |
| `KnowledgeReviewRevision` | 项目、原件版本、切片/标注/依赖指纹、编校参与者、复核者、有效期、乐观版本；状态 `draft → checking → reviewing → approved → published`，退回 `checking`，变更 `needs_review`，另有 `expired/withdrawn` |
| `KnowledgePublication` | 冻结批准清单、索引 generation、有效范围和当前可见指针；先验证投影，后事务切可见版本；批准不等于发布 |
| `RequirementScopeSnapshot` | 用户确认的稳定需求 ID、版本、来源和范围；覆盖分母来自该清单，不能来自检索命中数 |
| `GraphRun` | `graph_version, state_schema_version, execution_mode, reasoning_mode, checkpoint, lease_token, lease_until, budget, waiting_reason`；Task 状态和 SSE 进度可恢复 |

复核、发布、撤回和生成写请求携 `Idempotency-Key`，修改与审批携 `If-Match`；重复同请求回原结果，相同键不同内容拒绝。继承既有 `revision-conflict` / Problem Details，新增错误进入 registry；前端不能把权限失败当无资料。

### 报告与指标

首个路径拟定为“一个已有 CI 作业导出原始 JUnit XML，由授权上传/CI 推送 API 接收”，无需建设 Jenkins 调度器。接收清单提供 JUnit 本身不能可靠表达的身份和完整性；若真实来源缺失这些信息，页面及指标必须表示未知，不从到达顺序或文件名猜测。

| 对象 | 核心字段/规则 |
| --- | --- |
| `ReportReceipt` | `receipt_id, project_id, source_id, external_run_id, batch_id, shard_id, checksum, parser_version, correction_no, raw_object_ref, state` |
| `ReportManifest` | 来源项目/作业、构建、分支、应用提交与脚本提交、环境/配置、数据行、时区、开始/结束、预期分片、是否含完整尝试、外部测试 ID 映射；可选业务周期关联 |
| `TestAttemptFact` | 项目/来源/Run/稳定测试身份/配置/数据行/attempt 组成事实身份；保存结果、时间、错误指纹、原件 locator、来源版本/更正号和删除标记 |
| `MetricQuery` | `metric_ids, filters, from, to, timezone, as_of`；项目由已授权上下文确定；不接受自由 SQL |
| `MetricResult` | `query_id, metric_version, filters, window, as_of, numerator, denominator, value, completeness, missing_reasons, evidence_refs`；查询 ID 能回到所用事实版本 |

拟议 TAP 路由统一在 `/api/v1/projects/{project_id}/insights` 下：`POST /reports`、`GET /reports/{receipt_id}`、`GET /metrics`、`POST /queries`、`GET /queries/{query_id}`、`GET /runs`、`GET /runs/{run_id}/attempts`、`GET /failures`、`GET /evidence/{evidence_id}`。项目映射由服务端确认，客户端 path 中的项目仍须授权，不能自行声明归属。

事实身份不能用测试标题替代。跨来源只有显式映射证明同 Run/测试/尝试时才合并，否则保留来源身份并标待映射。新更正追加版本；同身份同更正号不同内容进入冲突处理，不能让到达时间决定胜者。

### 首版指标口径

固定实例集 `D`：首次有效终态为通过、失败或执行错误的实例。跳过、阻塞、取消、未执行、未知另列；缺首次历史时不能伪造 D。

| 指标 | 公式/条件 |
| --- | --- |
| 首次通过率 | D 内首次通过实例数 / D |
| 最终通过率 | 同一 D、固定执行清单/配置，每实例最后有效尝试通过数 / D |
| 重试恢复率 | 首次失败/执行错误且最终通过数 / 首次失败/执行错误数；不是 Flaky 真值 |
| 恢复贡献占比 | 恢复实例数 / D；若沿用原型 `Retry recovery share`，必须与上一行使用不同名称和指标 ID |
| 失败分布 | 最终失败/执行错误实例按错误指纹、配置或环境分组；钻取保留全部原始尝试 |
| 耗时/趋势 | 尝试耗时与实例含重试累计耗时分别命名；按同一时区/窗口/过滤条件统计，样本量可见 |

分母为 0 返回 `value=null`。最终结果型 JUnit 缺首次/重试历史时，可显示“已收到结果分布”，首次/最终配对指标与重试恢复率标不可计算；不能把该结果假称第一次尝试。报告不完整时允许展示已收到部分及其明确分母，但不显示完整通过结论。基础版不计算综合质量总分，不输出自动发布批准。

## 5. 任务清单

所有功能任务按“写失败测试 → 运行并确认预期失败 → 最小实现 → 同命令转绿 → 必要集成/契约检查 → 独立评审”执行。每个任务以小提交收口，建议主题 `feat: ...` / `fix: ...`；只提交该任务文件。下文标“新增”的路径为计划产物，尚不存在。迁移文件编号在执行时从当时真实 Alembic head 分配，不能复用旧计划预留编号。

### Task 0：冻结首批资料、报告、需求范围与接口

**文件：** 更新三个 RFC-011 配套专题中的本轮契约与范围说明；更新本计划的输入记录；样本仅以脱敏 fixture 进入 `apps/tap-ai-backend/tests/fixtures/quality/` 和新增 `apps/backend/tests/fixtures/insights/`，真实原件保持在授权存储。

**输入/输出：** 消费业务流程和现有 CI 原始报告；产出 `RequirementScopeSnapshot` 样本、报告字段映射、指标 oracle、审批角色矩阵和合同版本。Task 1–13 使用同一冻结集合。

- [ ] 选择一个真实业务流程，登记资料版本、适用时间、完整需求清单和业务复核者；样本包含正常、边界、异常、冲突、无答案及不允许访问的来源。
- [ ] 获取同来源至少一个成功、失败、重试、参数化和缺附件/缺分片报告，确认哪些字段真实可得；用实际样本决定 JUnit 接收清单，缺重试身份不能补造。
- [ ] 固定数据量、查询并发、模型预算和验收集；本计划建议基础 Insights 以 100 万尝试、90 天窗口、10 并发、列表/汇总 P95 ≤2 秒、钻取 P95 ≤3 秒为起始预算，须在签字的硬件配置上验证，属于建议目标而非已有性能。
- [ ] 在专题中记录本轮与后续边界、接口 schema、状态转换/错误码、项目映射、真实样本 digest、责任角色和评审结论；必要架构差异提交新 ADR。RFC 已为 `accepted`，本任务冻结实施细节，不重复请求方案审批，也不提前标为 `implemented`。

**验收：** 每个字段有真实样本位置或明确缺失语义；指标有可手算 oracle；没有来源/审核人/模型外发授权的路径登记为输入阻塞，而不是使用 fixture 判真实通过。

### Task 1：闭合身份、项目与跨产品授权

**文件：** 修改 `apps/tap-ai-backend/src/tap/modules/access/domain/authorization.py`、`application/authorize.py`、`interfaces/http/scope.py`；新增 `apps/backend/src/tap_platform/access.py`、两产品授权契约测试。身份接入按 Task 0 冻结的设计另增 adapter，不搬运另一产品 Python 包。

**接口：** 两产品分别验证 `principal + action + project + resource`；TAP Insights 服务调用同时验证 TAP AI 服务身份与实际用户/项目授权范围。OIDC 为 RFC-011 团队目标，须明确与旧 P0 内建身份路线的关系后接入，不由本计划静默替换。

- [ ] 写负向矩阵：错误 audience、错误项目、撤权、过期身份、服务身份越权、两个角色实为同一人、原件/引用/证据下载绕过鉴权。
- [ ] 接入确定的真实身份/角色映射及服务凭证，落实最小只读 Insights 权限与编校/复核/发布 action；原件和缓存也使用同一授权。
- [ ] 运行新增 `apps/tap-ai-backend/tests/contract/test_review_authorization.py` 与 `apps/backend/tests/contract/test_insights_authorization.py`，加真实两人核对与撤权验证。登录会话还须覆盖退出、失效及写操作防伪造请求。

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_review_authorization.py -q
uv run --project apps/backend pytest apps/backend/tests/contract/test_insights_authorization.py -q
```

**出口：** 同人自审和跨项目查询稳定拒绝；真实团队验收完成前仍只允许 loopback/隔离测试，不能用 Validation Actor 结果代替多人身份验收。

### Task 2：资料完整性清单与保留式迁移

**文件：** 修改 `knowledge/adapters/document_parsers.py`、`document_chunker.py`、`knowledge/application/ingestion.py`（均在 `apps/tap-ai-backend/src/tap/modules/` 下）；新增 `knowledge/domain/parse_inventory.py`、`apps/tap-ai-backend/tests/unit/knowledge/test_parse_inventory.py`、对应迁移与 metadata 注册。

**接口：** 解析结果增加 `ParseInventoryItem[]` 和解析配置/产物指纹；所有页、表、图形存在性有记录，旧文档无审核记录只能标“历史未复核”，不能回填为人审通过。

- [ ] 用混合 PDF 的无文字页、含表格/图片的 DOCX、空文本、编码异常和解析超时写失败测试；每个对象必须进入四态清单之一。
- [ ] 现有隔离 Parser 生成清单和原件定位；保留原始文件和重试版本，缺 OCR 的扫描区域标明确失败/待确认，不静默跳页。关键缺失阻止对应范围发布。
- [ ] 验证升级前后原文/引用仍可读，Worker 中断重试不重复生成版本，重新解析不覆盖旧产物。

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge/test_parse_inventory.py apps/tap-ai-backend/tests/integration/test_ingestion_recovery.py -q
```

**出口：** 已知页/关键区域清点覆盖 100%；未支持格式/区域不会变成完整支持。新增格式必须另带解析器、原件预览、locator 和独立质量集。

### Task 3：核对、独立复核、发布与撤回

**文件：** 新增 `apps/tap-ai-backend/src/tap/modules/knowledge/domain/review.py`、`application/review.py`、`adapters/mysql_review.py`、`interfaces/http/routes/knowledge_reviews.py`；修改 `platform/db/registry.py`、`contracts/http.py` / `problems.py`、应用 composition，并增加审核迁移、单元/集成/契约测试。

**接口：** 消费清单；生产 `KnowledgeReviewRevision` / `KnowledgePublication`；`approve_review(scope, revision_id, expected_version)` 与 `publish_review(scope, revision_id, idempotency_key)` 分开。复核者可以发布，但不能参与本版编校。

- [ ] 写状态机测试：自审、未解决阻断项、缺出处、过期页面审批、依赖变化、未经批准发布、半索引失败全部拒绝；重复命令只有一条批准/发布记录。
- [ ] 实现核对修订和独立审批；范围/条款/金额/单位/例外变化使批准失效。批准清单全部索引就绪后，原子切 MySQL 可见指针；投影失败保持原发布版。
- [ ] 撤回先使权威清单不可读并失效缓存，再异步删除投影；用两事务并发测试“回答生成中撤回”和“新索引写一半进程退出”。

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge/test_knowledge_review.py apps/tap-ai-backend/tests/integration/test_knowledge_publication.py apps/tap-ai-backend/tests/contract/test_knowledge_review_http.py -q
```

**出口：** 没有批准清单的内容不能进入正式检索；普通模式的部分发布需冻结排除范围，严格模式缺关键依赖即阻断。人工解释与原文分存。

### Task 4：统一 AI 图与必要长任务恢复

**文件：** 新增 `apps/tap-ai-backend/src/tap/modules/ai/application/interaction_graph.py`、`domain/graph_runs.py`、`adapters/mysql_checkpointer.py`；修改 `chat/application/conversations.py`、`test_management/application/generation.py`、`entrypoints/tapper_generation_worker.py` 和 `contracts/chat_stream.py`，增加迁移及恢复测试。

**接口：** 既有 Chat 和生成入口进入固定 `graph_version`；执行/推理分别记录 `inline|durable` 和 `direct|workflow|agentic`。本轮先 Fast Chat 与生成 Durable Workflow，Task 13 若需多步推理再启受预算 agentic 剖面。

- [ ] 先以故障注入证明 checkpoint、业务状态和 Outbox 不能部分提交；测试超过 60 秒、重复唤醒、并发领取、旧 lease 写入、人工等待后撤权与取消。
- [ ] 在 Python 3.13 环境验证 LangGraph/MySQL checkpointer 接口，固定依赖；图内执行 Classification & Admission，图外不新增模型分类器。节点只调用 ModelGateway、SearchPort、正式领域服务。
- [ ] 落实分段续租、fencing、等待释放 Worker、重启恢复、图版本固定和兼容检查；调用响应未知先对账。生成重试最多产生一份对应草稿，不重复发布。
- [ ] SSE 可恢复进度、失败原因、待确认和结果链接；不持久化模型隐藏思维链。旧 loopback Codex Answer Adapter 保持独立。

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py apps/tap-ai-backend/tests/integration/test_chat_sse_resume.py apps/tap-ai-backend/tests/integration/test_test_plan_generation.py -q
```

**出口：** 真实 Worker 被中断后从已保存节点继续；旧图运行不被新图静默接管；不得仅有任务状态字段就宣称可恢复。

### Task 5：批准范围检索、引用与多版本 Graph 一致性

**文件：** 修改 `knowledge/application/retrieve.py`、`answers.py`、`citations.py`、`graph_enrichment.py`、`graph/application/queries.py`、`graph/adapters/mysql.py`（位于 TAP AI modules）；扩展 `tests/integration/test_graph_snapshot_publication.py` 和新增 `test_published_retrieval.py`。

**接口：** 所有检索消费当前 publication；Graph Snapshot 绑定规范化完整 Revision 选择集及其 digest，而不是只取其中一个 Revision。产出 evidence 携来源/切片/批准版/locator，可在交付时复核。

- [ ] 写至少两个 Revision 的查询与问答测试：选择集不匹配不得使用 Snapshot；跨项目、已撤回、已到期的父段/邻段/Graph 证据均拒绝。
- [ ] 在 Milvus 预过滤后，再按 MySQL 当前批准清单核验；统一父段、缓存、图扩展和引用下载授权；生成结束前重新核验，失效时要求重生成。
- [ ] 修复多 Revision Snapshot 创建/发布及重启读取，补多文档浏览器旅程；有冲突或关键条件未知时回答明确说明，不能给确定测试预期。

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_published_retrieval.py apps/tap-ai-backend/tests/integration/test_graph_snapshot_publication.py -q
```

**出口：** 越权/过期泄漏为 0；旧引用可按当前权限回看历史版本，但历史授权不授予新访问。V2 最终关闭还需 Task 8 的真实质量证据。

### Task 6：真实知识审核、问答与引用界面

**文件：** 修改 `apps/tap-ai-frontend/src/features/knowledge/components/KnowledgeLibrary.tsx`、`DocumentDetail.tsx`、`CitationViewer.tsx`、`api/client.ts`；新增 `components/KnowledgeReview.tsx` 及测试、`tests/e2e/knowledge-review.spec.ts`。设计状态增量同步现有 `apps/web/src/widgets/tap/prototype/DocumentReview.tsx`，不另造审核壳。

**接口：** UI 消费 Task 2/3/4/5 的生成类型；业务审批状态和解析/索引状态分列，所有写操作携版本。

- [ ] 写原件/提取对照、问题定位、关键字段/例外、退回、独立复核、发布、撤回、冲突 reload、刷新恢复和权限按钮测试。
- [ ] 将原型的核对意图落到真实审核清单和修订记录；处理解析失败、部分可用、待复核、发布中、发布失败，无数据不能显示成功。
- [ ] 完成 Tapper 选择已发布来源、多轮问答和点击出处；上传未发布不自动进入正式问答。验证键盘、焦点返回、窄屏和来源撤回提示。

```sh
corepack pnpm --dir apps/tap-ai-frontend test src/features/knowledge/components/KnowledgeReview.test.tsx src/features/knowledge/components/CitationViewer.test.tsx
```

**出口：** UI 操作可经真实 API 重现，刷新/重启仍保留审核与会话；按 Task 14 要求采集受影响原型与共享壳前后截图。

### Task 7：测试设计从生成到人工发布闭环

**文件：** 修改 `apps/tap-ai-backend/src/tap/modules/test_management/domain/models.py`、`validation.py`、`application/generation.py`、`publish.py`、`interfaces/http/routes/test_plans.py`；修改 `apps/tap-ai-frontend/src/features/testManagement/components/TestPlanDetail.tsx`、`TestPlanReview.tsx`、API client 及 `tests/e2e/tapper-test-plan.spec.ts`。

**接口：** 生成输入冻结 `RequirementScopeSnapshot`、已批准知识版本、Input/Answer/Evidence Snapshot、模型/Agent/Skill 已发布版本；产出仍为既有 Plan/Revision 和内嵌 BDD 用例，不在本轮另建独立用例库。

- [ ] 增加测试：完整需求有 10 条、检索只命中 6 条时分母仍是 10；未知业务条件不得生成确定 Then；引用必须来自同一条授权 Evidence，不能拼接字段。
- [ ] 两个产品入口复用同一生成命令和 Job；UI 展示排队/进度/失败/重试/结果链接，离页回来能继续。模型参数不得越过冻结快照，AI 只创建草稿。
- [ ] 完成草稿编辑、BDD 顺序与预期校验、Assumption/Unknown/Coverage Gap 处理、评审结论和理由；并发编辑 `revision-conflict` 后 reload，保留未提交修改供用户核对。
- [ ] 人工发布重新验证知识与权限、需求版本和阻断项；严格项目另需非作者业务复核。已发布计划变更生成新 Draft，记录原样采纳/修改后采纳/拒绝，待评审不进入采纳率分母。
- [ ] 来源变化时标记受影响方案需复核，不覆盖历史审批；历史引用仍可定位。Automation 链接继续保持原型交互，不能显示尚不存在的真实 Run。

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_test_plan_http.py apps/tap-ai-backend/tests/integration/test_test_plan_generation.py apps/tap-ai-backend/tests/integration/test_test_plan_publish.py -q
corepack pnpm --dir apps/tap-ai-frontend test src/features/testManagement/components/TestPlanDetail.test.tsx src/features/testManagement/components/TestPlanReview.test.tsx
```

**出口：** 从 Tapper 和测试管理均可生成同类型草稿，经浏览器编辑/评审/发布；不能以直接 HTTP 发布代替浏览器旅程。每条关键预期有来源，覆盖分母可复核。

### Task 8：知识、Graph 与测试设计质量重新验收

**文件：** 扩展 `apps/tap-ai-backend/tests/quality/test_quality_kb_01.py`、`test_quality_graph_01.py`、`test_quality_test_01.py`、相应 fixtures 及 `scripts/run-quality-*-candidate.py` / evaluator；新增执行当日日期命名的 Gate Review，并更新 reviews 索引。

**接口：** 每个候选保存真实请求 ID、完整输出、数据集/配置/模型与输出 digest、人审结果及 `reviewedOutputDigest`，缺任一项不能判通过。

- [ ] 先验证伪通过会被拒绝：预填 reviewer、复用旧输出 digest、跳过已有 case、缺原输出、重复 request ID、修改输出后保留旧批准。
- [ ] V3 对全部至少 50 个业务意图重新产生当前真实输出，逐例由真实审核者复核；Schema/BDD 100%、无来源事实 0、关键需求覆盖 ≥90%、无 Critical Correction 草稿 ≥80%，不得降低既有门槛。
- [ ] V2 以多 Revision 契约补齐 API、问答、浏览器和重启旅程，并重新验证 `QUALITY-GRAPH-01` 当前候选与人审绑定。
- [ ] 知识保留 V1 的泄漏 0、anchor 100%、Claim–Citation precision 100%、recall@10 ≥90%、abstain accuracy ≥90% 基线；再用 RFC-011 的独立验收集验证新增核对/发布约束。目标至少 100 份文件、200 问，重点已启用类型各 ≥20 问，按文件拆调参与验收集。
- [ ] 新增知识指标按专题报告：Recall@50 ≥95%、前 10 证据覆盖 ≥90%、正确且有充分依据的回答 ≥90%、引用定位 ≥98%、无答案/冲突提示 ≥95%；批准关键金额/比例/日期/单位/条件/例外不得有已知错误。旧指标与新指标分别报告，不用较宽新指标覆盖旧失败。

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/quality -q
make quality-kb-real
make quality-graph-real
make quality-test-design-real
```

上述真实命令要求 Makefile 已规定的 opt-in、模型/数据授权和环境；缺输入必须失败或保持待验证，不在计划编写阶段运行。原质量集与新增集分别登记证据，模拟输出、skip 和预造人审不得计通过。

**出口：** 新 Review 分别判定 V2、V3 和新增可信知识，满足要求后才关闭重开门禁。未启用 OCR/图像/Excel 的质量项标“不在本批”，不能宣称通过 RFC-011 全格式门槛；Web 自动化仍按用户要求延后。

### Task 9：TAP 报告原件、接收账本与 JUnit 映射

**文件：** 新增 `apps/backend/src/tap_platform/insights/domain/reports.py`、`application/intake.py`、`adapters/junit.py`、`adapters/mysql.py`、`http.py`、`worker.py`，以及 `apps/backend/alembic.ini`、`migrations/env.py`、首个接收迁移和 TAP 自有 Outbox/对象存储适配；修改 `apps/backend/pyproject.toml`、锁文件、`app.py`、`tests/test_app.py`。新增 `tests/contract/test_report_intake.py`、`tests/integration/test_report_recovery.py`。

**接口：** `ReportManifest + raw bytes → ReportReceipt`；状态 `received → validating → mapped → projecting → ready`，另外区分 `rejected/conflicted/failed`；完整性 `complete/partial/unknown` 独立于处理状态。原件成功持久化与账本可对账后再确认接收，不能先确认再丢文件。

- [ ] 写失败测试：同批次同 checksum 重传幂等、同身份不同内容冲突、缺分片、乱序、更正、同名不同用例、参数化、未知尝试序号、缺附件、非法 XML/实体展开/超限上传。
- [ ] 接收先保存原件与 checksum，再事务写账本/Outbox；孤立 staging 对象可回收，账本已成功而响应丢失时重试返回同 receipt。Worker 可恢复解析，缺字段保留来源身份和缺失原因。
- [ ] 建 TAP 独立迁移、metadata 和事务边界，不导入 `tap` 包。新增 TAP 路由逐项更新归属白名单，原 AI 路由和隐藏越界负向测试继续拒绝。
- [ ] 将新增 TAP 模块、迁移、脚本和测试纳入 `Makefile` 的 lint/format/type/architecture/test 检查；为 TAP 后端配置必要检查工具，不能仅继续运行原来的 `test_app.py` 就视为新模块检查齐全。
- [ ] 报告补传只重试接收/解析，不自动重跑测试；授权证据下载经后端代理/受限链接，不暴露桶凭证。

```sh
uv run --project apps/backend pytest apps/backend/tests/test_app.py apps/backend/tests/contract/test_report_intake.py apps/backend/tests/integration/test_report_recovery.py -q
```

**出口：** 一个真实 CI 来源完成原件→账本→规范化事实；重启可继续。没有完整尝试历史的报告不会产生虚构首次率或重试率。

### Task 10：ClickHouse 事实投影、去重、更正与重建

**文件：** 新增 `apps/backend/src/tap_platform/insights/adapters/clickhouse.py`、`application/projection.py`、`deploy/local/clickhouse/001-insights.sql`、`scripts/rebuild-insights.py`、`apps/backend/tests/integration/test_insights_projection.py`；在 `compose.yaml` 增独立受限 ClickHouse 服务及持久卷，更新脱敏配置示例与启动检查。

**接口：** Worker 将已映射 `TestAttemptFact` 追加至分析明细；MySQL 记录投影批次和可见数据版本。查询按完整身份及更正号选唯一有效版本，不能依赖 ClickHouse 后台 merge。

- [ ] 用同一批重复投影、写成功回执丢失、Worker 中断、乱序更正、删除、更正撤销、同版本冲突写失败测试；关闭后台 merge 的条件下结果仍唯一。
- [ ] 建尝试明细、证据引用及查询所需 run/config 维度；保留原件、版本与删除标记。更正号由接收账本裁定，ClickHouse 不自行以到达时间解决冲突。
- [ ] 第一版先查唯一有效明细；确有性能证据再加可重算汇总。禁止对会补报/更正的记录做不可逆累计；批次未完全落库时不推进可见水位。
- [ ] 按账本与对象存储重建新投影版本，验证数量/checksum/oracle 后切读；验证服务重启、卷保留、数据备份与干净环境恢复。

```sh
uv run --project apps/backend pytest apps/backend/tests/integration/test_insights_projection.py -q
```

**出口：** 重复/更正后统计与原始 oracle 一致，清空隔离测试投影可重建；不得操作共享/default 数据卷。真实数据进入 ClickHouse 才能算该任务交付。

### Task 11：权威指标目录、查询和证据 API

**文件：** 新增 `apps/backend/src/tap_platform/insights/domain/metrics.py`、`application/queries.py`、`contracts.py`、`scripts/export_tap_contracts.py`、`contracts/openapi/tap-api.json`；生成 `apps/web/src/shared/api/generated/schema.ts`，更新 `Makefile` 同时生成/校验两产品契约，保持既有 `contracts/openapi/api.json` 不被 TAP 覆盖。

**接口：** 消费唯一有效事实和授权范围；生产第四节的 `MetricResult`、运行/失败分页、受授权 evidence。每次查询记录指标版、过滤、时区、截至时间和事实版本水位，历史 query ID 不静默变成新口径。

- [ ] 实现独立指标 oracle，断言分母、缺失、首次/最终与更正语义；覆盖时区边界、相同标题不同身份、不可比配置、缺首次、0 分母、迟到和超时。
- [ ] 服务端允许列表指标和参数化模板；ClickHouse 只读身份/项目约束、扫描/内存/并发/输出/超时上限，超限返回明确失败而非部分总数。
- [ ] 所有卡片/趋势/明细消费相同 query scope；权限、API、ClickHouse 不可用返回明确不可用，缺数据不返回 0 或正常。查询和原件引用可追溯，撤权后历史 query 仍重新鉴权。
- [ ] `make contracts` 与 check 同时覆盖 TAP 和 TAP AI；跨产品消费生成契约，不共享后端模块。

指标 oracle 示例（Task 0 的 fixture 应包含这些等价实例）：

```text
实例 a: pass；实例 b: fail → pass；实例 c: error；实例 d: skipped
D = 3；首次通过 = 1/3；最终通过 = 2/3
重试恢复率 = 1/2；恢复贡献占比 = 1/3；跳过单列 1
重复上传 b：全部不变
新增更正 b 最后结果为 fail：最终通过 = 1/3，首次通过仍 = 1/3
缺 b 的首次历史：配对口径标数据不完整，不能把 b 当首次 pass
```

```sh
uv run --project apps/backend pytest apps/backend/tests/unit/test_insights_metrics.py apps/backend/tests/contract/test_insights_http.py -q
make contracts
```

**出口：** 数字、分母、口径、查询 ID 和原始记录一一对应；Task 0 固定规模下执行查询压测并记录实测 P95，不将本地小样本结果外推。

### Task 12：真实报告接入与基础 Insights 页面

**文件：** 修改 `apps/web/src/legacy/TestAnalyticsWorkspace.tsx`，新增 `apps/web/src/features/insights/api/client.ts`、`components/ReportIntake.tsx`、`components/RunDetails.tsx` 及相邻测试；已有 `legacy/testAnalyticsModel.ts` fixture 仅留原型/测试适配，不作为运行时 fallback。新增 TAP 浏览器集成测试配置与 `apps/web/tests/e2e/insights-report.spec.ts`，纳入隔离 E2E runner。

**接口：** 接收/处理状态来自 receipt，卡片、趋势、失败和明细来自 Task 11；现有工作台增加数据 adapter seam，保留 `/prototype` 的同一布局与确定性截图数据。

- [ ] 写测试：上传后状态刷新、重复报告、映射缺失、失败/重试入口、错误格式、完整性不足、空数据、加载/服务不可用、首次历史未知、证据过期。
- [ ] 连接 Build/时间/环境/分支筛选、指标定义、首次/最终通过率、重试恢复、失败分组和耗时趋势；卡片和明细同筛选，表格额外局部筛选明确标注。
- [ ] 运行→实例→数据行/尝试→原报告/失败证据可钻取；没有步骤/截图时明确缺失，不能用示例日志填补。导出使用同一授权查询与口径；基础版分享仅项目内深链，不启用公开链接。
- [ ] 原型已展示但本轮未实现的自定义看板等交互继续保留在原型；真实入口不得宣称已支持未接通服务。复用既有产品壳，不生成另一套 Insights 应用。

```sh
corepack pnpm --dir apps/web test src/features/insights
corepack pnpm --dir apps/web test src/widgets/tap/TapProductPrototype.test.tsx
corepack pnpm --dir apps/web run check
```

**出口：** 真报告改变卡片与明细；刷新/重新登录后仍可追溯；前端不自己重新计算权威通过率，也不拿 fixture 作为错误降级。

### Task 13：Insights 与知识的受控 AI 解读

**文件：** 新增 `apps/tap-ai-backend/src/tap/modules/ai/ports/insights.py`、`adapters/tap_insights.py`、`application/insights_explanation.py` 和 `tests/contract/test_insights_tool.py`；修改 Task 4 图、会话 client 与失败详情中的 Ask Tapper 入口。

**接口：** `query_insights(scope, MetricQuery) → MetricResult`；scope 来源于后端已授权上下文。图先取得指标，再按获准来源做知识检索，回答绑定 query ID、事实版本、来源引用与当前权限。

- [ ] 写负向测试：模型提供别的项目、服务身份过期、API/ClickHouse 失败、超预算、篡改数字、query ID 不存在、引用撤回/越权、旧查询水位与新结论混用。
- [ ] 实现结构化只读 Tool；普通解释使用固定 workflow，确需多步才启 bounded agentic。Task 0 固定每次调用数/时间/费用预算，耗尽即停止并保留已核实事实。
- [ ] 回答交付前核对指标值/分母/query ID/证据，分别标事实、原因假设、缺失信息。指标不可用时核心知识 Chat 可继续，但不能猜数或回退直查数据库。
- [ ] 失败页跳到 Tapper 时携受授权资源引用与草稿；服务端再查，不能把 URL/浏览器传入数字直接当权威事实。审计关联 Conversation/Turn/GraphRun/Tool/query ID。

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_insights_tool.py apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py -q
```

**出口：** 页面与 AI 对同一 query ID 给出相同数值、分母与截至时间；知识解释有原文依据，相关性不被写成确定因果；AI 无发布、重跑或外部工单写入权限。

### Task 14：联合验收、保留式发布与交付记录

**文件：** 修改隔离 E2E runner、`README.md` 对应实现入口与状态、相关专题；新增执行当日命名的 `docs/reviews/` 记录及索引。只有实际完成时才更新本计划状态，不能用写完计划代替实现完成。

**输入/输出：** 消费 Task 1–13 的已验证产物、真实质量报告及冻结样本；产出每批次 Gate Review、保留式发布清单、恢复记录和产品验收结论。

- [ ] 在隔离环境完成三条真实旅程：资料上传→核对/独立复核→发布→问答/引用；确认需求→生成→编辑/冲突恢复→业务评审→发布；真实报告→账本→ClickHouse→查询/钻取→AI 解读。
- [ ] 故障矩阵覆盖 API/Worker/Redis/ClickHouse 中断、重复/乱序事件、补报更正、模型失败、过期/撤回、跨项目访问、响应未知、备份恢复和投影重建；原件、审批、账本与引用不丢失。
- [ ] 对照固定 `/prototype` 基准检查全部模块导航、Tapper→Test Plan→Automation 双向关联和悬浮助手→Tapper 会话/草稿交接。UI 变更在相同 fixture、1280×720、2× 像素密度下采集前后 PNG 并人工对照；原型保留的 Automation 旅程单列为交互证据。
- [ ] 执行当前产品的窄测试后，再运行下列仓库级检查；真实模型 Gate 与浏览器/恢复验证单独留记录，不把环境 skip 计入真实完成证据。

```sh
make contracts
make check
make test
make demo-e2e
git diff --check
git diff -- README.md docs/ AGENTS.md
```

- [ ] 先备份，再追加迁移，在隔离环境验证旧会话/计划/原件和数据保留；按项目逐步启用。故障回退采用兼容旧应用或撤回可见指针，保留新账本，不自动数据库降级/清卷。ClickHouse 重建按 Task 10 校验后切读。
- [ ] 记录发布清单：代码 SHA、两产品镜像/契约、迁移前后 revision、模型/图/解析配置、质量集/报告 digest、已启用格式/报告来源、已知限制、数据量/性能、备份与恢复耗时、复核签字。真实团队开放前 Task 1、安全与运维检查全部完成。

**出口：** M1/M2/M3/M4 分别有可追溯结论；未满足某批次的真实输入就保留“待验证”，不能将整个计划标 `completed`。只需交付隔离验证时明确标注部署边界，不宣称生产就绪。

## 6. 验收矩阵与补充质量要求

| 验收项 | 任务 | 证据/失败条件 |
| --- | --- | --- |
| 原件完整性与正式使用范围 | 2/3/5/6 | 每页/关键对象有处理结果，未确认关键规则不进入正式回答与预期；混合文件不静默丢页 |
| 审核和撤回 | 1/3/5 | 自审拒绝、过期版本拒绝、依赖变化失效、半发布不切换、生成中撤回拦截 |
| 可信回答 | 4/5/6/8 | 原文定位、当前授权、多轮范围、冲突/无答案，质量集分类型实测 |
| 测试设计 | 7/8 | 完整需求分母、编辑/冲突/Job 深链、人审绑定本次输出，模型不能发布 |
| Graph 历史缺口 | 5/8 | 两个以上 Revision 的 Snapshot 一致性与真实质量复验；不能复用撤销的 PASS |
| 报告真实性与身份 | 9/10 | 真实 CI 原件、稳定测试/尝试/配置、重复不重计、同名不乱合并、补报可重放 |
| 指标可复核 | 10/11/12 | 首次/最终同实例范围、重试两分母分名、截至与更正水位、空分母 null、原件可追溯 |
| AI 解读 | 13 | 服务身份与项目再授权、数值与 API 一致、事实/假设/缺失分列、不可用不猜数 |
| 恢复与安全 | 1/4/9/10/14 | 超 60 秒任务、中断/租约/并发、原子 Outbox、撤权、备份重建、模型外发策略 |
| 产品基准 | 6/7/12/14 | 完整模块可达、跨模块旅程、同视口前后截图；原型结果不等于真实执行 |

知识性能沿专题的固定硬件、10 万文本块、10 并发评测普通检索 P95 ≤3 秒，含重排、不含生成；另报端到端首字/完成延迟、超时及每问成本。图像未启用时不声称满足 1 万图块/含图 P95 ≤8 秒目标。若后续启用复杂表格/流程，必须同时验证关键值/单位/表头 ≥98%、连线端点/方向/条件精确率与召回率均 ≥95%，不只验证文件可上传。

## 7. 后续工作及进入条件

| 延后工作 | 启动条件 | 保留的目标 |
| --- | --- | --- |
| 复杂知识文件扩展 | 真实样本、模型区域/预算、独立解析资源和分类型质量集 | 扫描 PDF/PNG/JPEG、XLSX/CSV、OOXML 图形、OCR、图像向量/重排、单元格/区域引用；不把 openpyxl 读数当图形完整 |
| 完整测试管理 | 本轮生成评审闭环与独立用例迁移契约接受 | 独立用例/三模板、共享步骤、导入、需求关联、手工周期/逐配置结果、关闭更正、Jira 基础 REST；保留旧 `TestPlanRevision.cases` 快照 |
| **Web 自动化** | 用户重新确定优先级；V2/V3 新门禁通过；Web 详细设计、真实应用/账号/数据与执行环境就绪 | Test IR、固定版本、真实 Playwright、录制/调试、Jenkins、停止/对账、证据及回写 Insights；托管/本地 Electron 连接器分别验证 |
| 完整 Insights 与协作 | 基础真实数据链稳定，多来源映射和规则有业务签字 | 五类质量域、评分/门禁、告警、Jira/SonarQube 等连接器、分享/协作，不用缺失域重分权重制造高分 |
| 主动 Agent / App / Load Testing | 各自详细设计、授权、数据/设备/容量与独立验收计划 | 订阅/建议批准闭环、Android/iOS 分平台验证、k6 独立负载池；不列入本轮完成分母 |

Web 延后只改变实施顺序，完整原型中既有 LCA/移动端入口与交互继续保留；不能以本轮收窄为由删除模块。

## 8. 输入阻塞与计划完成规则

| 必须取得的输入 | 负责角色 | 阻塞范围 |
| --- | --- | --- |
| 首个业务流程、需求清单、资料原件及有效期、独立复核者 | 产品、BA、QA | 真实知识和测试设计质量出口；可先做结构与故障测试 |
| 一个真实 CI 来源及脱敏原报告、稳定身份/重试语义、保留范围 | 数据源负责人、QA | 真实 Insights 验收；不得用 fixture 代替 |
| 身份提供方、跨产品项目映射、审核角色、服务授权 | 架构、安全、运维 | 真实多人审批、跨产品 AI 查询和团队开放 |
| 模型别名、实际供应商操作、预算、数据区域与外发授权 | 模型/安全负责人 | 真实模型质量与生成验收 |
| 硬件/数据规模、查询预算、备份保留、恢复目标与执行窗口 | 运维、交付负责人 | 性能和团队试点出口，不能预填达标值 |

- 本计划所有任务初始均未完成；实现开始后改 `active`，有实际验收证据才勾选。
- M1/M3 可先独立交付，M2 不等待 Web 自动化；整个计划 `completed` 需要全部本轮任务及三条业务旅程通过。
- 依赖输入缺失与测试失败分别记录。真实验收失败不降低门槛、不跳 case、不复用旧人审、不将 skip 算通过。
- Gate Review 必须链接实际产物、样本 digest、命令、结果、人工审核与限制；更新 README/专题/计划索引的当前状态，但不将 RFC-011 全平台目标标为 `implemented`。
- 本计划编写不修改业务代码、原型或现有未提交改动；后续执行遇到并行修改先核对归属，不覆盖他人工作。
