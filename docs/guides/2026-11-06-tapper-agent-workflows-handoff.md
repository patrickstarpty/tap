# Tapper Agent 工作流与测试闭环实施交接

交接日期：2026-11-06。供新会话实施使用。本会话完成整体设计、四阶段实施计划及用户反馈修订，尚未开始工作流功能开发。本文汇总上下文，不替代 Spec 或分阶段任务清单。

## 1. 接手位置与工作区

- 仓库：`/Users/patrick/Desktop/Workspace/ai-native/tap`。
- provider 修订时分支为 `develop`，HEAD 为 `62cbc105`；接手时重新核对实际 HEAD。
- `34f86106` 已包含模块归属重构的集成记录。后端应用层、前端会话运行封装和 AI 资产维护归属均已落地；后续计划已按这些边界修订。
- OpenCode 阶段 0 和阶段 1–3 未执行。原 Copilot 确定性探针仅保留历史；当前 PATH 未找到 OpenCode，无真实能力矩阵。
- 本文及下列最新文档修订尚未提交。只从 HEAD 创建 worktree 会遗漏本次产品决策，必须先保存并带入这些文档。

交接时已有修改的文件：

| 文件 | 最新内容 |
| --- | --- |
| [整体设计](../superpowers/specs/2026-11-06-agent-workflows-and-test-loop-design.md) | 默认助手身份、双路径、实时工作流、测试闭环及全部产品决策 |
| [实施总计划](../superpowers/plans/2026-11-06-agent-workflows-and-test-loop.md) | 四阶段顺序、最新代码接入点与验证要求 |
| [阶段 1：原生工作流](../superpowers/plans/2026-11-06-native-agent-workflows.md) | 应用层接入、会话 Hook 复用、右侧栏、系统根身份与专业 Agent 目录分离 |
| [阶段 2：OpenCode 运行](../superpowers/plans/2026-11-06-opencode-workflow-runtime.md) | 内部根 profile、适配边界、ADR 编号顺延 |
| [阶段 3：测试闭环](../superpowers/plans/2026-11-06-versioned-test-automation-loop.md) | 节点详情组件及 ADR 编号修订 |
| [产品路线图](../superpowers/plans/2026-10-01-product-roadmap.md) | 与上述确认范围同步，仍为未实施 |

用户最终选择 OpenCode + LiteLLM；新的[阶段 0：OpenCode 能力验证](../superpowers/plans/2026-11-06-opencode-capability-validation.md)从未验证状态开始。旧 Copilot 记录不转记为 OpenCode 验收。

工作区另有与本任务无关的未跟踪项 `.env.bak-20261015`、`apps/tap-ai-backend/.quality/`。不要读取、删除、覆盖或打包提交。禁止直接使用 `git add .`。创建隔离工作区前，先核对 `git status` 与文档 diff，按明确文件清单保存规划文档，再以包含它们的基线开始实施；不要丢弃原工作区修改。不要因为交接记录写了某个 SHA 就覆盖接手时更新的代码。

## 2. 用户最后确认的产品规则

### 2.1 对外只有一个默认助手：Tapper

- Tapper 就承担 Orchestrator 职责。Orchestrator 是内部技术名称，不是 Tapper 之外的另一个助手，也没有“Tapper 再调用 Orchestrator”的额外层级。
- 对话、进度、通知、错误提示及用户帮助统一称为 Tapper。运行路径变化不改变这个身份。
- 用户直接输入需求即可；不要求选择默认助手，不提供 `@Tapper` 或 `@Orchestrator`。
- Agents 档案、搜索和选择 / @ 候选提供可委派的专业 Agent 与自定义 Agent，不单列系统 Tapper/Orchestrator。系统根身份不可通过请求参数变成委派目标，不能仅靠前端隐藏。
- 专业岗位采用亚马逊风格名称：BA、SDE、QAE、SDET，配中文标签；既有自定义 Agent 保留原名。
- 系统根配置及运行仍需版本冻结和审计，其记录归任务详情。不可把“所有运行可观察”误解为“所有内部角色必须出现在可调用目录”。

### 2.2 按需调用与两条执行路径

- 简单问答由 Tapper 直接完成；普通工具调用不强制启动专业 Agent。只有任务需要时才委派，独立工作可并行，依赖工作顺序运行。
- 主循环与子 Agent 各有自己的上下文和执行循环；不能把每个工具步骤都标成一个 Agent，也不能为了界面动画制造模型调用。
- 意图识别使用现有 ModelGateway，可融入入口响应；不以启动 OpenCode 为分类前提。
- 非编码任务：LangGraph 主循环及专业 Agent 通过 ModelGateway → LiteLLM 调用模型。
- 编码任务：整项编码工作的内部 Orchestrator 与专业 Agent 由 OpenCode CLI 承载，通过 BYOK → LiteLLM 调用模型。外层 LangGraph 只负责持久生命周期、交接、等待与产物接收，不同时调度 OpenCode 内部同一批子任务。
- OpenCode CLI 是用户最新指定的首版 coding provider；这项选择不代表已核实公司组织政策。保留 Claude Code、Codex 适配接口，首版不实现这些 provider。
- 同一任务后续问答由当前运行中的 Tapper 处理；发生编码交接时保留任务身份、已确认产物与证据，转移调度所有权，不能同时运行两个统筹者。

### 2.3 Agent 配置文件

- 编码任务启动前，一次生成内部 `orchestrator.md` 及当前项目已启用、任务获准使用的专业 Agent 文件，如 `ba.md`、`qae.md`、`sde.md`、`sdet.md`；不加 `tap-` 前缀。
- 文件放在任务工作区 `.opencode/agents/`，内部根使用 `mode: primary`，专业 Agent 使用 `mode: subagent`；权限使用 OpenCode permission 规则并验证执行端拒绝。
- 系统根 profile 是内部启动配置，不加入用户选择器或子 Agent 委派名册。保留文件不等于对外新增助手。
- 配置存在不代表实例启动；仍按需委派。非编码路径读取结构化配置，不生成这些文件。
- 每个任务冻结配置版本。首版不依赖当前 OpenCode 会话热加载；新配置默认用于新任务，必要的任务中变更在安全边界保存进度后进入后续执行阶段。
- 不宣称 OpenCode 无法动态加载配置，也不假定无人值守模式中的动态加载与恢复已验证；以阶段 0 实测为准。

### 2.4 GUI 与实时执行详情

- GUI 参考 Claude App 的 Chat/Cowork 与 Codex App；Claude Code 只参考 agent loop 和委派机制，不以其 TUI 作为 GUI 依据。
- 中间对话只显示消息、简短进度与“查看执行详情”入口。执行详情在右侧栏，默认收起，用户点击才打开。
- 右侧栏顶部使用“任务进度”等文案，展示 Tapper 当前活动、等待原因和统筹记录；下面展示实际专业 Agent 节点、依赖、并行关系和活动时间线。
- 简单问答只有任务活动，不额外生成可调用的 Tapper 卡片。点击专业节点在同一侧栏查看详情、日志与产物，不叠第二层抽屉。
- 关闭面板不停止任务；新事件或失败不强制重新打开。窄屏使用用户主动打开的覆盖面板。
- 用户可以指定正在运行的专业 Agent 补充要求，但由 Tapper 统一协调、记录影响并展示真实处理回执。
- 暂停请求不等于已暂停，接收要求不等于已应用。恢复、暂停、取消、重试均以持久状态和执行端确认为准。
- Azure Pipelines 仅参考依赖、节点、步骤日志与产物展示；不是首版执行器。不能声称 Claude App 已有完全相同的多 Agent 节点图。

### 2.5 测试资产、执行和首版边界

- 用户可见术语是“自动化测试步骤 / automated test steps”。旧 Test IR 只在历史说明或既有兼容标识中保留，不批量改代码名。
- 测试计划与测试用例始终由 TAP 管理，并且各自独立版本化；计划固定引用用例版本。
- 代码首版由 TAP 管理；Azure DevOps Repos、GitLab 等外部代码仓库后续作为用户可选方式。
- 首个执行器是 TAP 管理的本机隔离容器，真实执行 Playwright + TypeScript；Jenkins、Azure Pipelines 后续适配。
- 发布保留人工确认，绑定明确版本与摘要；修复产生新版本、新发布和新运行，保留原失败证据。
- 通用底座面向软件开发、测试和业务自动化，首版只以测试闭环验收；外部 Git/CI、桌面 RPA、移动端及多人生产化不在本轮范围。

## 3. 阅读顺序与开工位置

1. 读取仓库当前 `AGENTS.md`，检查分支、工作区和相关 skills。
2. 读[整体设计](../superpowers/specs/2026-11-06-agent-workflows-and-test-loop-design.md)及[实施总计划](../superpowers/plans/2026-11-06-agent-workflows-and-test-loop.md)。不要根据旧对话中的设想恢复已被纠正的 UI 或目录设计。
3. 从[阶段 0](../superpowers/plans/2026-11-06-opencode-capability-validation.md) Task 0.1 开始：先安装固定版本到隔离工具目录，再创建阶段设计、受控探针与确定性测试，核验 CLI 和 LiteLLM 组合。
4. [阶段 1](../superpowers/plans/2026-11-06-native-agent-workflows.md)可独立推进：任务/实例/事件/控制 → 目录与路由 → 原生循环和 worker → API/SSE → 右侧栏 → 全覆盖验收。阶段 0 外部条件受阻时，可继续阶段 1，不静默改 coding provider。
5. [阶段 2](../superpowers/plans/2026-11-06-opencode-workflow-runtime.md)必须等阶段 0 核心证据通过、阶段 1 合同就绪；按真实字段实现适配器，不能用 stub 成功代替能力验证。
6. [阶段 3](../superpowers/plans/2026-11-06-versioned-test-automation-loop.md)先固化测试资产的跨应用归属与单写迁移，再建设版本、制品、发布、隔离执行及结果回写；最终验收依赖阶段 2。

每阶段首个任务会创建该阶段的详细 Spec；这些文件是计划交付项，当前不应假装已存在。接口、测试示例、文件清单与复选框以分计划为准。执行新会话使用 `executing-plans`；需要任务级并行实施时遵守计划指定的 `subagent-driven-development` 流程，避免多个 Agent 修改同一文件。不要重问本节已经确认的产品选择，遇到证据推翻设计或影响范围的实质缺口再提出。

## 4. 代码基线与不能倒退的模块边界

| 当前实现 | 后续接入要求 |
| --- | --- |
| `apps/tap-ai-backend/src/tap/modules/chat/application/turn_execution.py`、`draft_batcher.py` | 回合节点与草稿编排归 Chat 应用层，不重新创建旧 `entrypoints/tapper_chat_turn.py` |
| `apps/tap-ai-backend/src/tap/modules/knowledge/application/conversation_execution.py`、`conversation_phases.py`、`modules/knowledge/api.py` | 通过 Knowledge 公开接口使用能力，保留执行上下文、授权复检与 checkpoint v3，不依赖 HTTP service 执行业务 |
| `apps/tap-ai-frontend/src/features/conversations/model/useConversationRuntime.ts` | 复用发送、幂等、恢复、取消、轮询及旧响应隔离；widget 只组合会话与 workflow feature，不能再写一套会话生命周期 |
| `apps/tap-ai-backend/src/tap/modules/ai/{domain,adapters}/maintenance.py` | 资产维护与修订同事务写入归 AI；`agent_team`/`skill_hub` 消费，不恢复反向写依赖 |
| `tapper-chat-v2` 与 MySQL checkpoint/lease/fencing | 保留旧聊天恢复能力，新工作流使用计划中的独立图版本 |
| AI 现有 `modules/test_management` | 阶段 3 明确迁移后再切换资产主写，禁止两个后端同时拥有写权 |

模块归属以 [ADR-034](../decisions/2026-11-06-adr-034-tapper-application-module-ownership.md)为依据。当前预留 ADR-035 为运行模型边界、ADR-036 为测试资产与本机执行；创建前重查索引，已有号码不能覆盖。

迁移基线为 AI `0038_skill_hub`、TAP `0005_insights_semantics`。计划预留的后续编号需在实际编码前重查 head；不改历史迁移或制造双 head。

## 5. 验证与尚未验证的事项

- 本会话只做文档检查：相对链接、Markdown 围栏、计划 Python 示例语法及 `git diff --check`；未运行应用测试、真实模型或 OpenCode 探针。
- 模块重构已完成的全量与浏览器验证记录见[模块优化实施记录](../superpowers/plans/2026-11-06-module-depth.md)。这是旧能力的证据，不能算作新工作流、暂停恢复或测试闭环通过。
- `524c202d` 后 `make test` 是核心回归，`make test-full` 才是全量应用测试。每任务先窄测；阶段 1–3 合入按总计划完成跨模块、持久化、合同及隔离 E2E 门禁。仅文档变更不跑应用全量测试。
- OpenCode 必须实测：执行端预算、BYOK 全调用覆盖、子 Agent 身份/生命周期、并行、权限、控制回执、会话与工作区恢复、失联核对。当前这些能力均不能宣称已通过。
- 真实探针使用阶段 0 计划中的 `TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1` 与 `TAP_RUN_OPENCODE_PROBE=1` 显式启用条件；后者为待实现开关。不得把未经实际验证的模型组合写成通过，不输出密钥。
- 隔离测试只使用自己创建的资源；不得重置共享 Demo 卷、修改已有租户数据或扩大回环绑定。确定性 E2E 与真实模型验证分别记录。
- 每个任务只在实际通过后勾选；合入 `develop` 时同步产品路线图和已实现架构。权威版本不能只留在临时 worktree，未经实现的设计也不能写成现状架构。

## 6. 可直接发送给新会话的开工指令

```text
请读取 docs/guides/2026-11-06-tapper-agent-workflows-handoff.md、其中链接的整体 Spec、实施总计划及阶段 0 计划，然后按计划开始实施。

先核对 develop 和工作区。最新 Spec/计划/路线图及交接文档尚有未提交内容，隔离工作区必须包含这些最新决策；保留无关文件，不读写或提交 .env.bak-20261015 与 apps/tap-ai-backend/.quality/。

从阶段 0 Task 0.1 开始；真实 OpenCode 验证受阻时可以继续阶段 1。阶段 2 必须等真实能力门禁通过，不用模拟结果替代。遵守仓库技能流程、TDD 与分阶段验收，完成的改动按计划评审并集成到 develop。

特别遵守：对外默认助手统一叫 Tapper，Orchestrator 只是内部调度角色，不在 Agents 档案或 @ 候选中单列。专业 Agent 按需调用。执行详情只在用户主动打开的右侧栏，中间保持对话。GUI 参考 Claude App/Cowork 与 Codex App；编码运行以 OpenCode CLI 为主。其他已确认规则以交接和 Spec 为准，不重新发散设计。
```
