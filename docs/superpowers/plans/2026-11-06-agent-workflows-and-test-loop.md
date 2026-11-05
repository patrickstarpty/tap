# Tapper Agent 工作流与测试闭环实施总计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可观察、可补充要求、可暂停恢复的双路径 Agent 工作流，并以版本化测试资产、真实自动化执行和证据回写完成首版验收。

**Architecture:** 非编码工作由 LangGraph Orchestrator 的模型—工具循环执行；编码工作整体交给 Copilot CLI，包括其 Orchestrator 与专业 Agent。MySQL 保存任务和事件，MinIO 保存版本化产物，Redis 只负责通知；测试业务资产通过正式跨应用合同保持单一权威。

**Tech Stack:** Python 3.13.12、FastAPI、LangGraph 1.2.12、SQLAlchemy/MySQL、Redis、MinIO、LiteLLM、Copilot CLI、React 19、TypeScript、Playwright 1.62.1。

**Spec:** [已确认的整体设计](../specs/2026-11-06-agent-workflows-and-test-loop-design.md)。本计划只授权规划；复选框表示实际执行进度，当前全部未执行。

## Global Constraints

- 岗位使用 Orchestrator、BA、SDE、QAE、SDET，显示中文；自定义 Agent 保留自身名称。
- 首版编码任务启动前生成当前项目已启用且该任务获准使用的全部 Agent 配置，文件名无 `tap-` 前缀；固定版本，不依赖热加载。配置存在不意味着实例启动。
- 简单问答由 Orchestrator 直接处理；按职责描述或用户显式指定委派；独立任务可并行，依赖任务串行。用户追加需求由原 Orchestrator 协调。
- 编码路径中的全部模型调用通过 Copilot BYOK → LiteLLM；非编码路径通过 ModelGateway → LiteLLM。不静默换 provider；保留 Claude Code、Codex 接口但不实现适配器。
- 测试计划、用例始终由 TAP 管理且分别版本化；代码首版由 TAP 管理。用户可见术语为“自动化测试步骤”。
- 发布沿用人工确认；代码执行在 TAP 管理的本机隔离容器内。外部 Git/CI、桌面 RPA、移动端、多人生产化不在本计划内。
- 所有实际 Agent 在 Agents 模块可追溯；Orchestrator 为任务父层；真实事件驱动流水线、活动时间线和节点详情。
- MySQL 为状态事实源；不可使用 Langfuse 周期指标推断实时状态；模型自然语言不能代替暂停、应用指令、发布或执行成功回执。
- 保持回环绑定、现有数据和应用 import 边界；不能对共享服务运行隔离测试或重置 Demo 卷。
- 已存在的兼容标识不因术语变更批量重命名；不重构无关目录。

## 1. 交付顺序与入口

| 阶段 | 计划 | 独立交付物 | 执行条件 |
| --- | --- | --- | --- |
| 0 | [Copilot 能力验证](2026-11-06-copilot-capability-validation.md) | 可重跑探针、真实事件样本、能力矩阵 | 可先执行；真实模型需显式 opt-in |
| 1 | [通用任务与原生工作流](2026-11-06-native-agent-workflows.md) | 原生单/多 Agent、持久状态、控制和实时界面 | 可与阶段 0 的调研并行；接口开发统一以本计划定义为准 |
| 2 | [Copilot 编码运行接入](2026-11-06-copilot-workflow-runtime.md) | 编码路径、任务级隔离、原生交接、恢复与统一视图 | 阶段 0 核心门禁全部通过、阶段 1 运行契约就绪 |
| 3 | [版本化测试执行闭环](2026-11-06-versioned-test-automation-loop.md) | 计划/用例独立版本、制品发布、容器运行和证据回写 | 领域归属 ADR 明确；最终验收依赖阶段 2 |

不得把阶段 0 的 stub/replay 成功当作 Copilot 实测。能力验证失败时，阶段 1 仍可独立推进，但阶段 2 和最终编码闭环不能宣布可交付。每阶段首个任务把本阶段接口及实测结论固化到独立设计文件；这里只安排该工作，不提前声称阶段设计与实测完成。

## 2. 已检查的代码事实

| 事实 | 现有接入点 | 计划处理 |
| --- | --- | --- |
| 已有聊天模型—工具循环 | `apps/tap-ai-backend/src/tap/modules/ai/application/chat_graph.py` | 保持 `tapper-chat-v2` 兼容；新运行图独立版本化 |
| 已有 checkpoint/lease/fencing | `apps/tap-ai-backend/src/tap/modules/ai/adapters/mysql_checkpointer.py`、`application/interaction_graph.py` | 复用持久化机制，不能把当前 Turn 状态直接充当任务状态 |
| 用户 Agent 已有修订 | `apps/tap-ai-backend/src/tap/modules/ai/domain/assets.py`、`application/agent_authoring.py` | 直接冻结 `AiAgentRevision`，不另建第二份配置库 |
| 子图注册表不是目录服务 | `apps/tap-ai-backend/src/tap/modules/ai/application/agents/registry.py` | 新任务域通过目录端口读取资产；保留旧子图协议 |
| Agents 页面只有档案/报表 | `apps/tap-ai-frontend/src/widgets/tap/workspace/agents/team/AgentsPage.tsx` | 新增运行视图，不改变历史统计口径 |
| 现有聊天只发送一个 agentRevisionId | `apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.tsx` | 新字段表达指定执行者，保持 Orchestrator 为入口 |
| 计划真实存在，用例只是内嵌项 | `apps/tap-ai-backend/src/tap/modules/test_management/` | 阶段 3 实现独立用例版本与兼容迁移 |
| TAP 自动化页仍模拟执行 | `apps/web/src/legacy/automation/AutomationWorkspace.tsx` | 新真实界面不能继续调用模拟运行 |
| TAP Insights 有真实报告接收 | `apps/backend/src/tap_platform/insights/` | 不拿其读模型代替执行权威，也不伪造 Git commit 适配报告 |

## 3. 目录边界与共同约定

新运行域为 `apps/tap-ai-backend/src/tap/modules/workflows/`，内部按 `domain/application/ports/adapters` 分层；Agent 配置仍属 `ai`，统计仍属 `agent_team`。前端新增 `apps/tap-ai-frontend/src/features/workflows/`，页面在现有 Agents 与 Tapper 入口组合。

阶段 3 目标归属为 TAP 非 AI 后端的 `test_management/automation/execution` 域；迁移正式编码前以该阶段 ADR 固化合同。旧 AI 测试生成器继续产生候选，通过 HTTP 交付，不允许两个后端同时主写同一资产。跨应用数据交换使用版本化 HTTP/事件，禁止直接 import 对方代码或直写对方表。

全部路径相对仓库根。分计划约定：`B=apps/tap-ai-backend/src/tap`，`BT=apps/tap-ai-backend/tests`，`F=apps/tap-ai-frontend/src`，`P=apps/backend/src/tap_platform`，`PT=apps/backend/tests`，`W=apps/web/src`。新增 Python 包需在同一任务添加 `__init__.py`。计划中的新增类型/函数是拟议接口，不代表已有实现。

迁移以当前 head 为基线：AI `0038_skill_hub`，TAP `0005_insights_semantics`。阶段 1 预留 AI `0039_agent_workflows`，阶段 2 `0040_coding_sessions`；阶段 3 预留 TAP `0006_test_assets`、`0007_automation_bundles`、`0008_execution_runs`。执行前检查实际 head；若号码已占用，在实现前更新计划和 down_revision，禁止改历史迁移或制造双 head。

## 4. 验证与合入

每个代码任务遵循红—绿—验证—提交，分计划给出测试文件、代表性断言和命令。集成测试使用 `owned_project_mysql` 或阶段内独立容器 fixture；默认测试不调用真实模型。新 opt-in 名称必须与 `.env.example`、帮助和测试 skip 条件同时实现。

每阶段合入前执行：

```bash
make contracts
make check
make test
git diff --check
git diff -- README.md docs/ AGENTS.md
```

有数据库改动时额外运行计划列出的迁移/恢复测试；有运行 UI 改动时执行隔离浏览器验收并检查窄屏、键盘、reduced-motion、SSE 重连。`make demo-e2e` 只证明既有 Demo，不代替阶段 3 专属执行闭环。真实模型结果与确定性结果分开记录，未执行项写明未执行。

功能实现按阶段小 PR 合入 `develop`；新长任务入口在功能开关关闭时不影响旧聊天。数据迁移必须验证单写切换和回退路径后再启用；新版本业务记录产生后，不通过删表回滚。接受的版本不能只留在临时分支，稳定发布再按仓库规则从 `develop` 合入 `main`。

## 5. Spec 验收追踪

| Spec 要求 | 对应任务 |
| --- | --- |
| 岗位、按需调用、全量配置快照、简单问答 | 0.1、1.3、1.4、2.1 |
| 两条模型路径、provider 适配、交接 | 0.2–0.4、2.1–2.4 |
| 任务、并行、累计预算、暂停恢复、幂等 | 1.1–1.4、2.2–2.4、3.5 |
| 定向要求、计划修订、真实回执 | 1.2、1.4–1.6、2.3 |
| Agents 全覆盖、单/多节点、时间线 | 1.5–1.7、2.4 |
| 独立计划/用例版本、TAP 管理代码 | 3.1–3.3 |
| 人工发布、真实隔离执行、结果回写 | 3.4–3.7 |
| 失败修复保留证据、完整验收 | 3.6–3.7 |
| 单一业务权威、兼容与文档更新 | 1.1、3.1、每阶段收尾 |

## 6. 交付进度

- [ ] 阶段 0：实测能力矩阵通过，固定 CLI/模型组合。
- [ ] 阶段 1：原生任务、控制与 Agents 实时视图通过验收。
- [ ] 阶段 2：Copilot 全链路 BYOK、事件与恢复通过验收。
- [ ] 阶段 3：版本化测试闭环及跨应用切换通过验收。

当前只完成计划编写，不代表上述能力已经实现、测试通过或具备生产运行条件。
