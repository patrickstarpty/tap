# TAP — Test Automation Platform

TAP（**Test Automation Platform**）是一套 Knowledge-first 的测试智能平台：Tapper 用企业知识回答问题并生成测试设计，Test Management 保存可审查的测试资产，Low Code Automation 把 BDD 映射成可录制、可执行、可追溯的 Web 自动化。当前已接受的路线先在固定 Validation Scope 中验证知识问答、Knowledge Graph、Test Plan、Web LCA/Recorder、Playwright/Jenkins 与结果闭环；验证通过后再实现用户、RBAC、多 Project 和生产治理。

## TAP AI 独立应用

TAP AI 的前后端分别位于 `apps/tap-ai-frontend` 和 `apps/tap-ai-backend`，拥有 Tapper 问答、知识文档/图谱、模型与 Agent/Skill 资产，以及 AI 测试方案的生成、保存和评审。TAP 非 AI 应用入口保留在 `apps/web` 和 `apps/backend`；现有低代码与测试分析原型由 TAP Web 承载。公开 API 路径、数据库表、迁移链和 `TAPPER_*` 配置名在本次目录迁移中保持不变，因此已有 Tapper 数据和对象引用无需重建。

TAP AI 可在不启动 TAP 前后端的情况下运行。先执行 `make tap-ai-bootstrap` 安装 TAP AI 冻结依赖，按 `.env.example` 配置并启动本机基础服务（现有 Demo 可用 `make demo-up`，或连接自行配置的服务），执行 `make tap-ai-migrate`，再执行 `make tap-ai-dev`；这会在回环地址启动 TAP AI API、Relay、后台任务和前端。结束 `tap-ai-dev` 会清理这些应用进程；现有 Demo 的基础服务可用 `make demo-down` 停止并保留卷。`make tap-ai-api` 与 `make tap-ai-web` 可分别启动两端；`make tap-web-dev`、`make tap-backend-dev` 则分别启动 TAP 非 AI 应用。`make tap-ai-check` 和 `make tap-ai-test` 只检查 TAP AI。当前入口仍是本机无认证 Demo，不承担局域网或生产访问。

产品边界与验收见 [TAP AI 产品边界与本机独立部署](docs/architecture/2026-09-15-tap-ai-product-boundary.md)。下方客户原型演示记录的是拆分前的组合式平台页面。

当前 AI 页面截图可运行 `corepack pnpm --dir apps/tap-ai-frontend run prototype:capture`：仅使用隔离的示例 API 响应，输出 6 张截图到应用的 `test-results/prototype-capture/`，不改写下方历史截图；追加 `--list` 可查看采集范围。

旧版浏览器中的 Automation 编辑和模拟 Run 需按[浏览器原型工作区升级](docs/architecture/2026-09-15-tap-ai-product-boundary.md#浏览器原型工作区升级)迁移：同源可自动恢复；默认端口从 5173 变为 5174 时，使用 TAP 自有的 `Local workspace` 导出/导入功能转移。

## 客户原型演示（2026-09-06 历史记录）

截至 2026-09-06，当前前端交互原型以 Tapper 为统一助手入口，组合 Knowledge、AI Agent 与 Skill，生成并评审 Test Plan，再生成严格 `1:1` 关联的 Automation。BDD 步骤显式映射到 Navigate、Click、Send keys、Assert 等动作，已关联资产共享模拟 Run 历史。

- **导航与品牌**：平台与浏览器标题使用 TAP；Tapper 使用 Listening 啄木鸟标识。二级菜单为 `New chat`、`Agents`、`Skills`、`Library`，收起后保留图标导航。点击一级 Tapper 入口回到当前会话并保留草稿；新建会话使用 `New chat`。
- **知识检索**：Library 默认打开 Knowledge Graph，搜索文档、概念或实体后可点击结果定位、高亮节点并查看关系。清空搜索恢复总览；`Documents`（文档列表）提供名称、类型与状态筛选。图谱支持平移、缩放、全屏及收起辅助面板；文档节点可跳到对应来源记录，尚无原文预览。
- **跨页面助手**：Test Management 与 Low Code Automation 右下角提供 Tapper 悬浮入口，支持当前页面上下文、快捷提问、轻量对话和“在 Tapper 中继续”。Listening 为默认形象，Aha 表示收起后收到未读回复；回复是基于页面数据的确定性原型建议。

当前操作步骤、现场话术、能力边界及 2026-09-06 从当前原型采集的 44 张截图见 [TAP 客户原型演示指南](docs/reference/2026-09-04-customer-prototype-demo-guide.md)。历史交互基线见 [RFC-008](docs/proposals/2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md)，正式产品和技术范围见 [RFC-009](docs/proposals/2026-09-04-rfc-009-tapper-knowledge-web-automation-platform.md)。

从仓库根目录启动原型：

```sh
corepack pnpm --dir apps/tap-ai-frontend dev --port 4175
```

打开 `http://127.0.0.1:4175/`。下图为 2026-09-06 从当前原型重新采集的六个页面，使用 2560×1440 无损 PNG，按整行展示，可点击图片查看原尺寸细节：

**Tapper 统一对话入口**

![Tapper 新对话入口](docs/assets/prototype-demo/01-tapper-new-chat.png)

**Knowledge Graph**

![Knowledge Graph](docs/assets/prototype-demo/17-tapper-knowledge-graph.png)

**已关联的 Test Plan**

![已关联 Automation 的 Test Plan](docs/assets/prototype-demo/20-test-plan-detail-linked.png)

**BDD 与 Automation actions 映射**

![Web Automation BDD 与动作映射](docs/assets/prototype-demo/27-web-automation-bdd-mapping.png)

**Web Automation 执行历史**

![Web Automation 执行历史](docs/assets/prototype-demo/30-web-automation-run-history.png)

**Tapper 生成并关联两类资产**

![Tapper 生成关联资产](docs/assets/prototype-demo/36-tapper-linked-artifacts.png)

演示时必须明确：Tapper 中的 **AI Agent** 负责分析、生成和调整；正式路线中的 **Execution Agent** 是 Jenkins **Pipeline Agent**。截图中的 Azure DevOps 与 Mobile 是旧的模拟交互探索，不属于当前实施范围。当前 Conversation、资产和 Run 使用浏览器状态模拟，所有运行均标为 `Simulated`；这不表示已连接真实 Pipeline、浏览器、移动设备或生成真实 Execution Evidence。

## 一句话架构

TAP 以 **可信知识 + 统一测试模型（Test IR）+ TAP-managed Revision + 统一执行证据** 为核心，采用 **React + TypeScript 前端、Python + FastAPI/ASGI 后端**。MySQL 保存权威业务状态与 Outbox，Redis 只作可重建唤醒，MinIO 保存原件/Bundle/Evidence，Milvus 保存可重建 `doc` 检索投影，MySQL 同时保存 Knowledge Graph；模型经 LiteLLM，首个 Execution Provider 是外置 Jenkins。Git 是可选导出/同步 Adapter，不是发布和执行的必要事实源。

目标 AI 编排由 [ADR-029](docs/decisions/2026-09-18-adr-029-langgraph-ai-interaction-task-orchestrator.md) 固定：TAP AI 的 Chat 与 AI Task 进入同一版本化 LangGraph，分别支持低延迟 Fast Chat、可恢复 Durable Workflow 和受预算限制的 Bounded Agentic Task；知识检索经 `SearchPort → Milvus 混合检索 → 可选的有界 MySQL Graph 扩展`，历史指标经 `TAP Insights API → ClickHouse`，模型调用经 `ModelGateway → LiteLLM`。任务分类位于图内，模型选择位于 ModelGateway，不部署独立 Query Router 或 Model Router 服务。RFC-006/ADR-018 的 legacy loopback Codex 回答组合不挂载 Project API，是明确例外。这是已接受的目标架构，不表示当前 V1 已实现 LangGraph、三种执行剖面、工具循环或模型层级策略。

[RFC-011 主动 Agent](docs/proposals/2026-09-17-rfc-011-rag-test-design-cross-platform-automation.md#2-主动-agent) 将**主动测试运营 Agent** 列为平台目标能力：受信需求/知识、构建与运行事件触发项目影响分析、回归建议和失败跟进，先形成可审阅方案，再经授权调用领域接口执行并回收反馈。项目工作记忆、建议收件箱、订阅降噪与版本化批准共同支持该闭环；默认仅分析/草稿，首期不监听个人桌面。这仍是 draft 功能设计，未表示当前代码已实现。

RFC-011 当前聚焦**知识问答与基础 Test Insights**，对应原第 1、2 阶段的部分能力；基础 Insights 先接已有 CI 或外部真实报告。测试管理扩展、自建 Web/App 执行、完整 Insights/协作、负载与完整主动 Agent 闭环保留后续目标，详细设计待补。各阶段设计深度与范围见[交付范围标注](docs/proposals/2026-09-17-rfc-011-rag-test-design-cross-platform-automation.md#迁移或发布方式)，不据此改写现有阶段完成状态。

### Test IR 是什么？

`Test IR` 是 **Test Intermediate Representation** 的缩写，在 TAP 中可以直接理解为“**统一测试模型**”。它不是客户需要操作的页面，也不是 Playwright、Selenium 或 Appium 脚本，而是平台内部用于统一记录测试内容的结构化格式。

```text
Test Plan / BDD（业务上要验证什么）
                ↓
Test IR（统一记录步骤、动作、目标、预期结果和关联关系）
                ↓
Web Automation（当前生成 Playwright + TypeScript；Mobile 后置）
                ↓
Run 与执行证据（记录执行结果并追溯到原始测试步骤）
```

例如，业务人员在 Test Plan 中写下：

```text
When the applicant submits the application
```

TAP 会在统一测试模型中记录：这个步骤来自哪个 Test Plan、执行 `Click` 动作、目标是哪个提交按钮，以及预期进入 `Pending underwriting` 状态。当前 Web Automation 把它确定性生成成 Playwright + TypeScript；未来若增加其他执行框架，也必须保留同一个步骤身份和证据链。

因此，Test IR 的价值不是让客户学习一种新语言，而是让 TAP 能够做到：

- Test Plan 和 BDD 保持业务可读；
- 每个 BDD 步骤都能关联 Click、Send keys、Navigate、Assert 等自动化动作；
- 更换 Web 执行 Provider，或未来增加新的执行框架时，不必重写业务测试定义；
- Automation Run 可以追溯到对应的 Test Plan、Scenario 和 BDD Step。

面向客户演示时，可以直接使用“**统一测试模型**”这个名称；`Test IR` 只作为技术架构中的正式术语保留。

已接受的首个交付技术栈：

```text
Linux + Docker Compose + MySQL + Redis + MinIO
+ Milvus + LiteLLM + external Jenkins
+ React/TypeScript + Python/FastAPI + Playwright/TypeScript
```

## 目标

- 先让用户基于企业知识获得带引用、可核验、可恢复历史的回答和 Knowledge Graph。
- 让 TAP AI 的 Chat 与 AI Task 共用一个 LangGraph 入口、状态和审计边界：Fast Chat 保持低延迟，Durable Workflow 承载可恢复长任务，Bounded Agentic Task 承载受控复杂工具循环。
- 让用户用自然语言或 BDD 创建 Test Plan 与 Web Automation，也能基于已有资产做定向更新。
- 用稳定的统一测试模型（Test IR）连接需求、BDD、脚本、Locator、Fixture、Hook、测试数据和运行证据。
- 在同一条 Run 时间线中关联 TAP Revision、Jenkins Attempt、测试结果、证据和人工审批。
- 用 provider-neutral 接口隔离模型、对象存储、Recorder 和执行系统，首个执行适配器采用 Jenkins。
- 通过 LiteLLM 统一路由 Chat、Coder、Embedding、Reranker、Vision 模型。
- 默认隔离不可信代码，限制凭证、网络和高风险工具调用。
- 在固定 Validation Scope 中先验证知识→测试设计→Web 自动化→执行结果闭环，再决定是否投入账号与生产化。
- 所有 AI、Graph 和 Recorder 输出先形成 Draft/Proposal，经确定性验证与人工发布后才成为权威 Revision。

## 非目标

- 当前不实现 Mobile/Appium、Azure DevOps、BrowserStack、Git Sync、SSO、专用 Graph DB 或 Kubernetes HA。
- 不把 DeepSeek Harness、LangGraph 或 BrowserStack 的内部对象直接暴露为 TAP 公共契约。
- 不在 MVP 阶段构建通用低代码编排器或多云调度平台。
- 不让非确定性的 Agent 判断替代确定性的测试门禁。
- 不把 Codex CLI/SDK 变成 Knowledge、授权、摄取、Milvus/Graph 写入或测试执行的必需依赖。

## 文档导航

- [Tapper 知识与 Web 自动化平台架构](docs/architecture/2026-09-04-tapper-knowledge-web-automation-overview.md)：当前边界、组件、数据、流程、安全、可靠性与部署。
- [RFC-011：跨平台测试与 AI 编排目标设计](docs/proposals/2026-09-17-rfc-011-rag-test-design-cross-platform-automation.md)：统一 LangGraph、主动测试运营 Agent、Milvus 知识工具、Insights API/ClickHouse 指标工具与可选 chDB 文件分析。
- [ADR-029：LangGraph 统一编排 TAP AI 的 AI 交互与任务](docs/decisions/2026-09-18-adr-029-langgraph-ai-interaction-task-orchestrator.md)：记录 Fast Chat、Durable Workflow、Bounded Agentic Task 及工具、模型、指标边界。
- [RFC-009：平台设计](docs/proposals/2026-09-04-rfc-009-tapper-knowledge-web-automation-platform.md)：完整产品旅程、数据模型、API、事件、质量门禁和阶段边界。
- [Tapper 知识与 Web 自动化平台实施计划](docs/plans/2026-09-04-tapper-knowledge-web-automation-platform.md)：V0–P1 的精确文件、TDD 步骤、命令与提交边界。
- [Tapper 平台设计基线评审](docs/reviews/2026-09-05-tapper-platform-design-baseline-review.md)：记录已关闭的关键问题、最终 READY 结论和“可进入 V0、尚未实现或生产就绪”的授权边界。
- TAP AI 技术架构总览：[PNG 预览](docs/assets/rfc-011/2026-09-18-tap-ai-technical-architecture-overview.png) / [SVG](docs/assets/rfc-011/2026-09-18-tap-ai-technical-architecture-overview.svg) / [draw.io 源文件](docs/assets/rfc-011/2026-09-18-tap-ai-technical-architecture-overview.drawio)：面向技术与产品/管理联合评审，展示主动事件、统一 LangGraph、行动提案与授权反馈、领域端口及数据底座。
- TAP 平台架构简图：[draw.io 源文件](docs/architecture/2026-08-27-tap-platform-architecture.drawio) / [SVG 预览](docs/architecture/2026-08-27-tap-platform-architecture.svg)：面向管理层说明输入、统一平台、业务结果与共享底座。
- RAG 知识问答简图：[draw.io 源文件](docs/architecture/rag/2026-08-27-rag-knowledge-business-flow.drawio) / [SVG 预览](docs/architecture/rag/2026-08-27-rag-knowledge-business-flow.svg)：用知识建设与在线问答两条主线说明从数据源到可溯源回答的完整链路。
- [整体架构评审](docs/reviews/2026-08-21-architecture-review.md)：评审结论、优先级问题、整改建议与分阶段决策门禁。
- [Milvus 本地检索实验评审](docs/reviews/2026-08-27-milvus-local-search-experiment.md)：记录真实数据库、空卷重建、embedding 预算证据与严格的决策边界。
- [Tapper 本地知识 Demo RFC](docs/proposals/2026-08-27-rfc-005-tapper-local-knowledge-demo.md)：本地来源优先工作区、运行边界与验收标准。
- [Tapper 本地知识 Demo 计划](docs/plans/2026-08-27-tapper-local-knowledge-demo.md)：纵向实现步骤与确定性门禁。
- [Tapper 本地知识 Demo 验收](docs/reviews/2026-08-27-tapper-local-knowledge-demo.md)：本地中间件、浏览器、持久化和可选真实模型的证据记录。
- [Tapper 本地回答后端 RFC](docs/proposals/2026-08-31-rfc-006-tapper-local-codex-answer-backend.md)：记录 LiteLLM/Codex 独占选择、固定 Embedding 与 fail-closed 验收。
- [Tapper 单智能体、无工具 Codex 决策](docs/decisions/2026-09-01-adr-018-tapper-local-codex-tool-free-answer.md)：记录精确 CLI/model/catalog 契约及其本地边界。
- [历史 Phase 1 Intelligence Layer 探索](docs/proposals/2026-09-02-rfc-007-phase-1-intelligence-layer-exploration.md)：保留 durable task、Artifact、Validator 与 Review 设计；交付优先级已被 RFC-009/ADR-021 替代。
- [TAP 客户原型演示指南](docs/reference/2026-09-04-customer-prototype-demo-guide.md)：按客户讲解顺序汇总 Tapper、Library、Test Management、Low Code Automation 的逐页截图、演示话术和能力边界。
- [Knowledge/RAG 基础](docs/architecture/rag/2026-08-21-foundation.md)：当前 Milvus 文档路径与历史 Azure 四索引设计的范围说明。
- [数据切片与溯源](docs/architecture/rag/2026-08-21-chunking-and-provenance.md)：稳定身份、revision lineage、Citation、删除与重建。
- [Azure AI Search 索引设计（历史/provider-specific）](docs/architecture/rag/2026-08-21-ai-search-index.md)：被替代的四索引专项参考。
- [检索调优方案](docs/architecture/rag/2026-08-21-retrieval-tuning.md)：可复用评测原则与历史 Azure 实验阶梯。
- [TAP Knowledge Chat](docs/architecture/2026-08-21-knowledge-chat-ui.md)：V1 持久 Conversation/SSE/Citation 交互输入。
- [历史 Codex Agent Runtime RFC](docs/proposals/2026-08-21-rfc-001-codex-agent-runtime.md)：已拒绝的旧 Phase 1.5 设计；可复用 Runtime 隔离原则由 RFC-007/ADR-014 保留，现行产品范围由 RFC-009 管理。
- [当前核心契约](docs/reference/2026-09-04-tapper-platform-contracts.md)：Scope、Knowledge、Conversation、Test Plan、Test IR、Recorder、Jenkins Run/Evidence 和错误语义。
- [架构决策](docs/decisions/index.md)：架构决策、取舍与被覆盖的历史方案。
- [交付路线图](docs/plans/2026-08-20-roadmap.md)：从架构基线到可用 MVP 的阶段计划。
- [来源与可追溯性](docs/reference/2026-08-20-source-notes.md)：`engprod` 会话索引、官方资料和推断边界。

## 核心原则

1. **Test IR 是稳定中间层**：自然语言、BDD、低代码和脚本都映射到版本化 IR。
2. **TAP 管权威 Revision**：MySQL 管资产/版本/关系/运行事实，MinIO 管内容寻址 Bundle/Evidence，Git 仅为可选同步。
3. **执行证据统一**：Jenkins 及未来 Provider 都产出同一 Evidence 和 Step Result 模型。
4. **平台拥有控制面**：身份、策略、状态、审批、审计和归一化结果由 TAP 管理。
5. **执行端可替换**：模型、对象存储、Recorder 和 Jenkins 均通过端口接入。
6. **确定性门禁优先**：Agent 可以建议、生成和诊断，最终门禁必须落到明确规则。
7. **不可信输入默认隔离**：代码、网页内容、模型输出和第三方回调都不可信。

## 当前状态

- 架构状态：`v0.4 accepted — validation-first knowledge and web automation`
- 实现状态：`V0/V1 gate-passed; V2/V3 gate-reopened; V4 blocked`
- 当前交付重点：`关闭 V2/V3 更正门禁`
- 后续顺序：`V2/V3 re-review → V4 Web LCA/Recorder → V5 Jenkins → VG → P0 → P1`
- 默认仓库可见性：建议 `private`
- 下一决策点：见 [待确认项](docs/proposals/2026-08-20-open-questions.md)

V0 的固定 Validation Scope、Project 授权、Audit、恢复、MinIO 和隔离 Parser 已通过[完整出口](docs/reviews/2026-09-06-v0-validation-scope-reliability-gate.md)。V1 的 Source、统一 ModelGateway、AI Agent/Skill、持久 Conversation/SSE、真实 Web 接线和知识质量已通过[可信知识门禁](docs/reviews/2026-09-09-v1-trusted-knowledge-gate.md)。V2/V3 已有 Knowledge Graph、Test Plan 与对应 Web/API 主体实现，但后续独立审查重新打开了门禁：多 Revision Graph 一致性、真实 Test Design 人审绑定和完整 Web 评审旅程仍需补证，V4 暂不放行。当前状态见 [V2/V3 门禁更正评审](docs/reviews/2026-09-14-v2-v3-gate-correction.md)，开发入口、调用链和扩展位置见 [Tapper 开发者指南](docs/reference/2026-09-13-tapper-developer-guide.md)。

## Tapper 本地知识工作区

Tapper 当前产品入口在已确认的 TAP 壳层中使用真实 Project API。Library 支持 Source create/list/detail/delete/retry、真实上传与六阶段 ingestion；Conversation 支持服务端历史、不可变 Turn 快照、可恢复 SSE、取消、引用与 Artifact Link；Knowledge Answer 可使用当前授权的 Milvus 文档证据和有界 Graph Context。Library 的 Knowledge Graph 使用真实 Snapshot/Evidence API；Tapper 可请求生成 grounded Test Plan Draft，Test Management Web 支持明细 Review 和人工发布，编辑与冲突恢复仍只具备 API 能力。本地 Milvus `doc-schema-v2` 使用 canonical Enterprise/Project/Source/Document/Revision 投影。

当前仍未交付登录、产品身份/RBAC、多 Project 产品化、OCR、Web Recorder、正式 Playwright Bundle、Jenkins 结果闭环和生产加固。API、Web 和所有中间件只绑定精确 loopback；固定 Validation 身份仅适用于验证环境，不能直接开放到局域网或生产环境。V2/V3 当前为 `gate-reopened`，不能作为 V4/V5、产品身份或生产加固已经完成的依据。

支持文本可提取的 PDF、DOCX、Markdown（MD）和 TXT。PDF 不执行 OCR；扫描件返回 `ocr-required`。服务端硬上限为每文件 `25 MiB`、最多 `50` 份未删除文档、每次回答最多选择 `20` 份 ready 文档。

首次启动：

```sh
cp .env.example .env
# 在 .env 中填写 DASHSCOPE_API_KEY，并把 ws-your-workspace-id 换成自己的 Workspace ID；不要提交该文件
make bootstrap
make object-store-build PLATFORM=linux/arm64
TAPPER_PARSER_PLATFORM=linux/arm64 make parser-build
make demo-up
make demo-check
make demo-dev
```

对象存储构建固定官方 MinIO 源码、Go 与 runtime 输入，在本机生成实际 image ID 和 ignored `.tapper/object-store-build.json` receipt；启动会核对 receipt、镜像与容器身份。以上命令的 `linux/arm64` 已实测；其他平台需指定对应平台并完成本机验证。MinIO 独立于 Milvus 自用存储，不发布 registry，也不把固定输入视为逐位一致重建的证明。

文档解析镜像使用固定 Python 基础镜像和 `uv.lock` 中的四个解析依赖，在本机生成 `.tapper/parser-build.json` receipt；源码、锁或构建输入变化后需要重建。`make demo-dev` 先启动私有 Unix socket 监督进程，并以真实短解析确认可执行及可回收，再启动 API、Relay、Ingestion、Conversation Generation、Graph、Test Design Worker 与 Web。每次解析使用独立无网络容器，Parser 不开放 TCP 端口；停止应用时同时回收监督进程及其任务。`.tapper/parser-runtime/<compose-project>` 保留私有 owner/image 关联，重启先按原归属清理遗留任务，再执行当前镜像自检；不要把缺少错误文件当成清理成功。

已有旧 `.env` 的工作区应同步所需配置，保留原凭据和存储引用。未配置 `TAPPER_OBJECT_STORE_PROVIDER` 时保留 Azure；新模板明确选择 MinIO。切换已有 Azure 数据时，显式设置 `TAPPER_LEGACY_AZURE_ENABLED=1` 并保留有效的 `AZURE_STORAGE_CONNECTION_STRING`，旧 locator 才能继续读取、删除及恢复 reservation；新写入进入 MinIO，不自动搬迁旧数据。

模型配置至少同步下面三项；旧 OpenAI model route 会覆盖 Compose 默认值，不能继续保留：

```dotenv
LITELLM_MODEL=dashscope/qwen-plus
LITELLM_TAPPER_EMBEDDING_MODEL=dashscope/text-embedding-v4
DASHSCOPE_API_BASE=https://ws-your-workspace-id.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
```

`make demo-up` 启动并初始化 MySQL、Redis、Azurite、Milvus 与 LiteLLM，MinIO 模式另外启用独立的 `tap-minio` 服务；`make demo-dev` 在 `127.0.0.1:8000` 运行 FastAPI，在 `127.0.0.1:5173` 运行 Vite Web，并启动 Relay、Ingestion、Conversation Generation、Graph 与 Test Design Worker。默认本地端口如下：

| 组件           | 默认 loopback 端口 | 职责                                                                             |
| -------------- | ------------------ | -------------------------------------------------------------------------------- |
| MySQL 8.4 LTS  | `23306`            | Source/Document、Conversation/快照、Graph、Test Plan、Audit 与 Outbox 的权威状态 |
| Redis 7.4      | `26379`            | 可重建命令分发与任务唤醒                                                         |
| TAP MinIO      | `19000`            | 新模板默认的原文件、normalized/chunk/embedding artifact，独立具名卷              |
| Azurite Blob   | `21000`            | 显式 Azure 模式或已启用的旧 locator 兼容存储                                     |
| LiteLLM Proxy  | `24000`            | 固定 Chat/Embedding alias 路由                                                   |
| Milvus         | `39530` / `29091`  | 本地 `doc` 可重建检索投影与健康端口                                              |
| FastAPI / Vite | `8000` / `5173`    | Knowledge HTTP API 与 Tapper Web                                                 |

RFC-009 V1 默认、Validation 和 Product runtime 现在只装配一个 LiteLLM `ModelGateway`，并对 `TAPPER_ANSWER_BACKEND=codex` fail closed。RFC-006 的直接 Codex CLI 路径只保留在不挂载 RFC-009 Project API 的显式 legacy-loopback composition，不属于 V1 合同，也不能作为 V1/VG 验收证据。

该历史 Demo 的模型配置只来自服务端 `.env`，不在 UI 或单次请求暴露。默认及已验收的完整回答选择块为：

```dotenv
TAPPER_MODEL_BACKEND=litellm
TAPPER_ANSWER_BACKEND=litellm
TAPPER_CODEX_MODEL=gpt-5.6-sol
TAPPER_CODEX_REASONING_EFFORT=ultra
TAPPER_CODEX_TIMEOUT_SECONDS=300
TAPPER_CHAT_ALIAS=tapper-chat
TAPPER_EMBEDDING_ALIAS=tapper-embedding
TAPPER_EMBEDDING_DIMENSION=1536
```

要复验历史本机 Codex 路径，必须显式运行 `make legacy-tapper-codex-dev`；该命令只绑定 loopback，固定使用直接 Codex 能力，不会启动 V1 Project API 或切换到 LiteLLM 回答路由。默认 `make demo-dev` 不接受 Codex 直连，也不会 fallback、hedge 或重试到 legacy runtime。

V1 文档、查询 Embedding 和回答生成都经唯一 ModelGateway 的固定 alias：`tapper-embedding` 发往阿里云百炼/DashScope `text-embedding-v4`，维度固定为 `1536`；`tapper-chat` 当前路由到百炼 `qwen-plus`。公共模型目录的逻辑显示名与实际 provider/model 审计分离；`GPT-5.6 Sol` 显示名不是当前上游路由或 V1 质量证据。

Codex 回答模式精确要求原生 `codex-cli 0.149.0`、`gpt-5.6-sol`、`ultra`、有效的本机 ChatGPT 登录、单智能体、零工具、单 API 进程内并发 `1` 和 300 秒超时，不读取或要求 `OPENAI_API_KEY`/`CODEX_API_KEY`。

Codex CLI 只是本机调用入口，不是本地推理：query 与所选 Evidence 会发送给 OpenAI；文档和 query 的 Embedding 内容会发送给阿里云百炼。该数据边界只获准用于 loopback、无认证的单操作者本地 Demo，不得据此开放 LAN、共享或生产服务。

Codex 的请求自有 canonical model catalog 会消除内建 CodeModeOnly、多智能体和 apply-patch metadata，并与 24 个禁用 feature 及显式 plan/input/agent overrides 一起保持 Direct tool registry 为空。这个 catalog 的固定 entry schema 只保证精确 CLI `0.149.0`，不是跨版本兼容承诺；CLI、登录、feature、catalog/schema、模型或能力任何漂移都会使 readiness/request fail closed，返回 `503 answer-unavailable`，且绝不调用 LiteLLM answer。Web 对该错误只显示“回答模型暂时不可用，请稍后重试。”

LiteLLM 用 `LITELLM_BASE_URL`、`LITELLM_MASTER_KEY`、`LITELLM_MODEL`、`LITELLM_TAPPER_EMBEDDING_MODEL`、`DASHSCOPE_API_KEY` 与 `DASHSCOPE_API_BASE` 注入实际路由与凭据。在未跟踪的 `.env` 填写 key，并把脱敏 Workspace ID 替换为实际值；`.env.example` 同时列出的 API Host 与原生 `/api/v1` 地址仅供参考，Tapper/LiteLLM 当前只消费 OpenAI-compatible `/compatible-mode/v1` 地址。`LITELLM_EMBEDDING_*` 只供单独批准的付费 Embedding research 使用，Tapper runtime 不读取。

页面刷新会重新读取 Source/Document、Conversation/Turn、Answer/Evidence Snapshot、Citation、Graph Snapshot 和 Test Plan Revision；API/Web/Worker 进程重启与普通 Compose 停止/再次启动后也从 MySQL 权威状态和可重建投影恢复。普通停止/再次启动保留具名卷：

```sh
make demo-down
make demo-up
make demo-dev
```

只有下面的 guarded 命令会不可逆删除精确 Compose project `tap-tapper-demo` 的 MySQL、Redis、Azurite、TAP MinIO 和 Milvus 卷；命令拒绝其他 project 名称：

```sh
TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-demo \
  TAP_ALLOW_TAPPER_VOLUME_RESET=1 make demo-reset
```

### `demo-check` 故障定位

`make demo-check` 独立检查五个组件，只输出组件、结果和安全修复码：

| 组件 / 修复码               | 处理方式                                                                                                                                                                                                                                                                                                                                                                                      |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MySQL / `start-mysql`       | 运行 `make demo-up`；确认 `TAP_DATABASE_URL` 与迁移 head 使用默认 loopback project。                                                                                                                                                                                                                                                                                                          |
| Redis / `start-redis`       | 运行 `make demo-up`；确认 `TAP_REDIS_URL` 指向 `redis://127.0.0.1:26379/0`。                                                                                                                                                                                                                                                                                                                  |
| Blob / `start-blob`         | MinIO 模式先运行 `make object-store-build PLATFORM=linux/arm64`，核对 `.env.example` 的显式 `TAPPER_S3_*` 配置，再运行 `make demo-up`；Azure 模式核对 loopback connection string 与 private 容器。                                                                                                                                                                                            |
| Milvus / `start-milvus`     | 为 Docker 分配至少 2 vCPU / 8 GiB，运行 `make demo-up`，并保留固定 reader/writer/provisioner 配置。                                                                                                                                                                                                                                                                                           |
| Models / `configure-models` | 默认 V1 在 ignored `.env` 配置 `DASHSCOPE_API_KEY`、`dashscope/text-embedding-v4`、`dashscope/qwen-plus` 与完整 Workspace `/compatible-mode/v1` 地址，重启 `make demo-up` 和本地角色，并确认 `tapper-embedding` / `tapper-chat`；只在显式复验 legacy 时检查精确原生 Codex `0.149.0`、ChatGPT 登录与 tool-free catalog/feature 契约。默认 runtime 对 Codex 直连 fail closed，不会回退 legacy。 |

### 确定性 E2E 与真实模型 smoke

日常验收使用隔离 project、真实本地中间件和 deterministic fake 模型，不消耗 provider 配额：

```sh
make demo-e2e
```

真实 provider gate 是单独的显式 opt-in；未设置开关时两个 smoke 文件精确产生两次有意 skip，且不会进入 provider/Codex 请求体。阿里门禁验证 `tapper-embedding`、1536 维、有限数值及 zh→en/en→zh 相似度；Codex 门禁验证精确 `0.149.0 + gpt-5.6-sol + ultra` 的单智能体、零工具、grounded/cited/sanitized/cleanup 契约。缺凭据、provider `401`、维度或 catalog 漂移、非法 claim/citation、工具事件和清理不确定性都算失败，不会转为 skip：

```sh
set -a
. ./.env
set +a
TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1 uv run --project apps/tap-ai-backend pytest \
  apps/tap-ai-backend/tests/smoke/test_tapper_real_model.py -v -rs
TAP_RUN_TAPPER_CODEX_CONFORMANCE=1 uv run --project apps/tap-ai-backend pytest \
  apps/tap-ai-backend/tests/smoke/test_tapper_codex_smoke.py -v -rs
```

2026-09-01 的验收证据为：阿里 `tapper-embedding` 的 zh→en 与 en→zh 门禁均通过且维度为 `1536`，`elapsed_ms=669`；Codex bootstrap 和未打补丁的生产配置均通过，最新生产复验输出 `version=0.149.0 model=gpt-5.6-sol reasoning=ultra single_agent=true grounded=true cited=true sanitized=true cleanup=true elapsed_ms=21652`，pytest 为 `1 passed in 21.71s`、exit `0`。默认无授权执行为 `2 skipped in 0.63s`、exit `0`。证据不保存 query、Evidence、回答、向量、JSONL 或登录信息。

### 实验性 Milvus 检索门禁

Milvus 已被 ADR-023 接受为目标 `doc` 检索投影，但当前仓库完成的仍只是本地、可重建且可替换的实验与知识切片，不是共享或生产部署完成证据；这里的检索实现可替换性不表示回答后端存在 fallback。固定版本与脱敏预计算向量的可复现 correctness gate 为：

```sh
make milvus-preflight

# 仅首次创建全新 volume；完成 root 轮换后不再设置该开关
TAP_ALLOW_INITIAL_MILVUS_ROOT=1 make test-milvus

# 已完成 root 轮换的既有 volume
make test-milvus

TAP_ALLOW_INITIAL_MILVUS_ROOT=1 \
  TAP_ALLOW_MILVUS_VOLUME_RESET=1 \
  make test-milvus-rebuild-empty
```

真实 embedding profile 是显式授权的付费研究入口，只能在注入未跟踪 provider 配置并单独批准后运行 `TAP_RUN_PAID_EMBEDDING_RESEARCH=1 make research-embeddings`。上述命令或单次 GREEN 只证明固定实验门禁，不表示 V1、共享环境、P0 身份或 P1 生产门禁已经通过；生命周期建议以[本次实验评审](docs/reviews/2026-08-27-milvus-local-search-experiment.md)的完整证据为准。

## 开发工作区与契约

运行时和依赖图固定为 Python 3.13.12、uv 0.10.8、Node 22.22.0、pnpm 10.15.1、`uv.lock` 与 `pnpm-lock.yaml`。从仓库根目录执行：

```sh
make bootstrap
make contracts
make check
make test
```

`make contracts` 从 FastAPI 路由元数据和公共 Pydantic 模型确定性导出并检查 `contracts/openapi/api.json` 与 `contracts/events/chat-stream.schema.json`：JSON 使用排序键、两空格缩进、一个末尾换行，且不写入时间戳。HTTP DTO 与 SSE event models 是彼此独立的模型图；浏览器可见的 SSE schema 不描述 `text/event-stream` framing。

`make contracts` 同时更新并检查 `apps/tap-ai-frontend/src/shared/api/generated/` 的 TypeScript client/type。冻结安装使用 `uv sync --frozen --all-groups` 和 `corepack pnpm install --frozen-lockfile`，不依赖全局 pnpm。
