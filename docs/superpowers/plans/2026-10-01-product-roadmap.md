# TAP 产品路线图

本文件是 TAP 全局产品路线：做什么、为什么、按什么顺序、做到哪了。实施步骤见各版本链接的 spec 与 plan；V1 的验收标准与子项目以 [V1 总纲](2026-09-29-v1-roadmap.md) 为准。

## 状态约定

| 状态 | 含义 |
| --- | --- |
| 已完成 | 已合入 `develop` 并通过验收，附 PR 编号或合并提交；进入 `main` 的时间以发布为准 |
| 进行中 | 有 plan 或已在实施，附分支或 PR |
| 已设计 | spec 已写，未进入实施 |
| 未设计 | 仅有方向，没有 spec |

每次 PR 合并、spec 定稿或范围调整时，同步更新本文件对应行。

## 总览

```mermaid
flowchart LR
  B[基础<br/>已完成] --> V1[V1 可信知识问答<br/>进行中]
  V1 --> V2[V2 主动 Tapper<br/>已设计]
  V2 --> V3[V3 测试闭环<br/>设计草案待评审]
  V3 --> V4[V4 产品化<br/>未设计]
```

| 版本 | 主题 | 用户价值 | 状态 |
| --- | --- | --- | --- |
| 基础 | 知识问答原型与完整产品原型 | 可演示的 Tapper 与全模块设计基准 | 已完成 |
| V1 | 可信知识问答 | 基于项目知识得到有依据、可追溯的回答 | 进行中 |
| V2 | 主动 Tapper | 不用提问，Tapper 主动发现问题并沉淀团队知识 | 已设计 |
| V3 | 测试闭环 | 通用 Agent 任务底座，首先打通知识 → 版本化测试计划/用例 → 自动化执行 → 结果回写 | 已设计（2026-11-06 整体草案待评审，尚未实施） |
| V4 | 产品化 | 多人、多项目、可在企业环境安全部署 | 未设计 |

## 基础（已完成）

- 知识摄取与问答：PDF/DOCX/MD/TXT 与有界 XLSX 摄取、Milvus 混合检索、引用回答、Knowledge Graph。
- 流程图图片知识与路径回答（#27）；pytest/Allure 结果接入与知识切片生命周期（#28）；对话历史与输入控件（#29）；Tapper 猫头鹰品牌（#26、#35）。
- `/prototype` 完整产品原型：Tapper、Test Management、Test Insights、Low Code Automation、跨模块关联与悬浮助手；Tapper 交互同步（#33）。
- 文档精简与 V1 总纲（#30、#31）。

## V1：可信知识问答（进行中）

退出标准：[V1 总纲](2026-09-29-v1-roadmap.md) 中 6 项能力的“工程完成”全部勾选。

| 项 | 状态 |
| --- | --- |
| 子项目 0 文档精简与总纲 | 已完成（#30、#31） |
| 子项目 1 基础设施收敛 | 已完成（#32） |
| TAP AI 前端清理原型内容 | 已完成（#34） |
| Tapper 模块接口与职责收敛 | 进行中（[设计](../specs/2026-11-06-module-depth-design.md)、[计划](2026-11-06-module-depth.md)；回答执行、会话运行与资产维护写入归属优化，产品范围不变） |
| 子项目 2 可观测性（能力 5） | 已完成（#36） |
| Langfuse 运营看板与用户反馈（能力 5 的增量） | 已完成（2026-10-15 实机验收；[spec](../specs/2026-10-14-langfuse-ops-observability-design.md)、[计划](2026-10-14-langfuse-ops-observability.md)） |
| 推荐问题（能力 1 的一部分） | 已完成（#38、#39） |
| 子项目 3 可靠问答（能力 1） | 进行中（推荐问题部分见上一行；golden set 格式与回归命令已就绪，随子项目 7 PR 3 交付——`make quality-answers` CI 常驻假模型门禁、`make quality-answers-real` 真实模型对比 opt-in，真实基线已在 `d6e432de` 上运行并完成切换后对比（2026-10-22，门禁通过）；链路可靠性修复并入子项目 7 ②） |
| 子项目 4 知识图谱脉络分析（能力 2、3） | 进行中（PR 1–PR 5、验收修复与三轮真实模型复验修复已合并到 `develop`，独立验收有条件通过；能力 2 工程完成（2026-10-23 `graph-bench` 通过）；2026-10-23 真实门禁 FAIL：关系 golden 2/25、边抽样准确率 26%、误合并 30%，真实对话中 `graph.relations` 调用 0/25（[门禁记录](../../reviews/2026-10-23-graph-reasoning-gate.md)）） |
| 子项目 5 Skills/Agents（能力 4） | 工程完成，业务验证待办（[spec](../specs/2026-10-11-skills-agents-design.md)、[计划](2026-10-12-skills-agents.md)、[ADR-032](../../decisions/2026-10-12-adr-032-skill-packages-and-tool-registry.md)；MCP 不在 V1） |
| 子项目 7 LangGraph 编排（能力 6，② 图重构 → ③ 角色与意图 → ④ 围栏 → ⑤ 工具与长任务 → ⑥ 可信知识） | 进行中（PR 1 图骨架已合并到 `develop`（2026-10-15）；PR 2（瞬时失败退避续跑、attempt 上限、worker loop 单次异常不退出）与 PR 4 围栏已合并到 `develop`（2026-10-17）；PR 3（ModelGateway 流式、正文内联引用 `[S<n>]`/`[R<n>]`、Redis 草稿流与唤醒流、SSE 阻塞读取取代数据库轮询、立即停止）已合并到 `develop`（2026-10-18）；PR 5（③ 角色与意图、④ 工具参数范围校验、⑤ `model ⇄ tools` 工具调用循环：工具注册表四项、`graph.relations`/`insights.query` 经工具调用、SSE `task.step`、`TapperPersona` 版本冻结、`CHAT_STATE_SCHEMA_VERSION` 升为 3）已合并到 `develop`（2026-10-22）；剩余意图真实门禁（用户运行）；PR 6 可信知识与 PR 7 长任务 2026-10-23 移入 V2；[spec](../specs/2026-10-14-tapper-langgraph-orchestration-design.md)，7 个 PR；2026-10-14 由 V2 方向提入 V1；PR 1–4 先行，能力 4 在 PR 5 之前合入） |

## V2：主动 Tapper（已设计）

设计：[Agent 团队视图设计](../specs/2026-10-22-agent-team-view-design.md)（V2.1）、[Tapper 主动 Agent 设计](../specs/2026-09-30-tapper-proactive-agent-design.md)（V2.2–V2.4）、[Tapper LangGraph 编排设计](../specs/2026-10-14-tapper-langgraph-orchestration-design.md) 第 9 节（LangGraph 收尾）。前置：V1 能力 4、能力 5 与 worker 可靠性修复完成。

| 项 | 内容 | 状态 |
| --- | --- | --- |
| V2.1 | Agent 团队视图：Agent 人事档案与组织架构图、Langfuse 运行数据、维护履历、汇报页与导出 | 工程完成（[plan](2026-10-23-agent-team-view.md)；[spec](../specs/2026-10-22-agent-team-view-design.md)）；2026-10-28 审查修复、面板重做（分析平台式）与香港语料数据重建合入（[演示](../../guides/2026-10-28-agent-team-business-demo.md)）；待业务验证（spec 7.4） |
| Skill Hub | Skills 模块改为 Skill 库：按描述自动激活取代绑定 Agent、类别/版本/来源元数据、用量统计与导出（第 1 阶段）；来源同步与版本（第 2 阶段）；静态扫描与信任等级、项目内共享（第 3 阶段） | 已设计并确认（[spec](../specs/2026-11-01-skill-hub-design.md)，2026-11-01；替代已否决的"Skill 名册"方向）；第 1 阶段工程完成，待业务验证（已合入 `develop`，2026-11-03，[计划](2026-11-01-skill-hub-phase-1.md)；真实模型激活精度 1.00/1.00、名册去常驻复跑已完成） |
| LangGraph 收尾 | 由 V1 能力 6 移入（2026-10-23）：PR 6 ⑥ 可信知识（依据支撑校验、诱导胡编子集门禁）；PR 7 ⑤ 文档长任务的暂停恢复与进度，2026-11-06 扩展为通用任务底座和双路径 Agent 工作流 | 已设计（[原 spec](../specs/2026-10-14-tapper-langgraph-orchestration-design.md) 第 9 节；[扩展草案](../specs/2026-11-06-agent-workflows-and-test-loop-design.md)待评审，新增 Copilot BYOK 路径、原生编排和 Azure Pipelines 风格实时节点；可信知识仍独立验收） |
| V2.2 | 主动框架、TAP AI 内部剧本（知识缺口、Skill 影响、纠正与 FAQ 沉淀、流程沉淀为 Skill）、动态入口与设置 | 已设计 |
| V2.3 | TAP AI 与平台事件契约 ADR、平台事件接入、Test Insights 失败与 flaky 分析、悬浮助手提示 | 已设计 |
| V2.4 | Test Management 与 Low Code Automation 事件和采纳动作 | 已设计，随 V3 后端就绪 |

退出标准：V2.1、LangGraph 收尾、V2.2、V2.3 工程完成；在真实项目中度量提议采纳率。

## V3：测试闭环（整体设计草案待评审）

2026-11-06 范围调整：[双路径 Agent 工作流与测试闭环设计草案](../specs/2026-11-06-agent-workflows-and-test-loop-design.md)。方向已由产品负责人逐项确认，整体草案尚待评审，不表示实施或验收完成。底座面向测试、软件开发和业务自动化，首版只以测试闭环验收；V1 的范围与未完成门禁不变。

| 项 | 确认范围 | 状态 |
| --- | --- | --- |
| 通用任务与两条路径 | 参考 Claude Code 按需 agent loop，主 Agent 直接处理简单问答或自动/显式委派；先识别意图，编码任务交给 Copilot CLI 内 Orchestrator 与专业 Agent，BYOK 接现有 LiteLLM，非编码任务由原生 LangGraph Orchestrator 编排；统一持久化、暂停恢复和产物 | 已设计（调用与展示方向已确认，整体草案待评审；Copilot 组合能力待实测） |
| 实时 Agent 流水线 | Agents 模块覆盖全部 Agent 档案与运行实例，Tapper 支持自然语言与选择 / @ 调用；Orchestrator 作为运行父层，单个/多个 Agent 以 Azure Pipelines 风格节点展示，结合对话和活动时间线；用户可定向补充要求，由 Orchestrator 协调并记录影响 | 已设计（调用与展示方向已确认，整体草案待评审） |
| 测试资产与代码版本 | 测试计划、用例始终在 TAP 管理且各自版本化；首版代码由 TAP 管理；以易懂的“自动化测试步骤”描述结构化操作、数据和预期结果 | 已设计（整体草案待评审） |
| 执行与结果回写 | 首个执行器改为 TAP 管理的本机隔离容器，运行 Playwright + TypeScript；固定发布版本，回写真实结果与证据，修复形成新版本 | 已设计（整体草案待评审） |
| 后续适配 | Azure DevOps Repos、GitLab 等代码仓库；Jenkins、Azure DevOps Pipelines 执行器；保留 Claude Code、Codex coding provider 接口 | 未设计（仅确认扩展边界，首版不交付这些适配器） |

实施前按草案拆分能力验证、通用任务与实时视图、Copilot 接入、版本化测试闭环四个子项目，各自形成 spec 和 plan。解冻 Test Management 与建设自动化后端时先确定跨应用领域归属，重新评估 [ADR-021](../../decisions/2026-09-04-adr-021-knowledge-first-web-automation-delivery.md)、[ADR-024](../../decisions/2026-09-04-adr-024-tap-managed-automation-revisions.md) 与 [ADR-025](../../decisions/2026-09-04-adr-025-jenkins-first-execution-provider.md)。V2.4 主动事件与采纳动作仍单独交付。

## V4：产品化（未设计）

方向：身份与多项目、局域网与生产安全设计、企业级检索（Azure AI Search 四索引）与生产加固。当前 Tapper Demo 为回环绑定、无认证，在此之前不做局域网或生产承诺。
