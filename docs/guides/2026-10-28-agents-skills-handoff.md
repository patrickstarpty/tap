# Agents 名册与 Skills 名册交接

创建日期：2026-10-28。归属[产品路线图](../superpowers/plans/2026-10-01-product-roadmap.md) V2.1 及其后续。接续 [Agent 团队视图业务流程演示](2026-10-28-agent-team-business-demo.md)与 [Agent 团队视图业务验证交接](2026-10-27-agent-team-business-validation-handoff.md)。本文给下一个会话用：先读第 1 节确认状态，再按第 4、5 节继续。

## 1. 状态

| 项 | 状态 |
| --- | --- |
| V2.1 Agent 团队视图 | 工程完成；2026-10-28 两轮改动已合入并推送 `origin/develop`（`845d1c68`）：审查修复、分析平台式面板 v2、TAP 品牌与完整模块导航、香港语料数据重建、英文版演示文档 |
| 演示数据 | 只用香港公开语料（8 份产品小册子、2 份理赔表、3 份合成流程文档）；真实运行 178 轮 + 仿真历史 397 次，核对 252 项一致；脚本与清理命令在 `~/tap-synthetic-2026-10-28/`（见交接文档第 2–4 节） |
| 真实 Agent 名册 | 已建成并验证（29 个会话、37 轮真实模型），见 [Agent 名册](2026-10-28-agent-roster.md)；第 3 轮提示词修订已完成：Test Case Designer 修好；Automation Script Writer 与 Claims FAQ Helper 的剩余问题属平台与语料（见待办） |
| Skills 模块 | [Skill Hub 设计](../superpowers/specs/2026-11-01-skill-hub-design.md)已由产品负责人确认（2026-11-01）；第 1 阶段[实施计划](../superpowers/plans/2026-11-01-skill-hub-phase-1.md)已写（2026-11-01），未开工 |
| 业务验证（spec 7.4） | 未完成，仍需真实使用一周 |

## 2. 真实 Agent 名册（进行中）

产品负责人确认的名册（2026-10-28）。全部英文名、英文系统提示词，负责人分配给演示库里已有的 9 个人；每个用真实模型跑 2–3 轮验证；删除演示用的 `Claims Guide Assistant` 及其 2 个会话；`Claims FAQ Helper` 重新启用作为通用理赔问答 Agent；`Plan Comparison Assistant`、`Web Test Design Assistant` 与内置 `Knowledge agent` 保留。

| 阶段 | Agent | 岗位 | 负责人 | 工具 / Skill |
| --- | --- | --- | --- | --- |
| 测试左移 | Requirements Reviewer | Requirements review analyst | zhang.min | search、answer、graph.relations |
| 测试左移 | Test Case Designer | Test case designer | li.na | search、answer、graph.relations；Citation skill |
| 测试左移 | Test Data Designer | Test data designer | wang.lei | search、answer |
| 测试进行中 | Automation Script Writer | Test automation engineer | chen.jing | search、answer；webapp-testing |
| 测试进行中 | Defect Triage Assistant | Defect triage analyst | liu.yang | search、answer、insights.query |
| 测试右移 | Release Readiness Reporter | Release readiness analyst | zhao.qian | search、answer、insights.query |
| 测试右移 | Production Issue Analyst | Production issue analyst | sun.hao | search、answer、graph.relations |
| 通用 | Document Summariser | Documentation analyst | zhou.ting | search、answer、graph.relations |
| 通用 | Claims FAQ Helper（已有） | Claims process Q&A specialist | chen.jing | 已有配置 |

完成后应有：`docs/guides/2026-10-28-agent-roster.md`（每个 Agent 的完整提示词、验证问题与结论、重建方法），本地 `~/tap-synthetic-2026-10-28/roster/` 的脚本、清单与备份，以及交接文档状态节的一条链接。接手检查：`git status` 看该文档是否已生成；`GET /api/v1/projects/tapper-demo/ai/agents` 应列出 11 个 Agent；团队页组织架构图应按 9 位负责人分组。确认后由本会话之外的人审查提示词质量与验证答案，再提交合入 `develop`。

## 3. Skills 模块方向（待写 spec）

2026-10-28 晚产品负责人否决了"Skill 名册／Skill 团队"方向：Skill 是独立、灵活调用的能力包，不是成员，不该套 Agent 团队的人事模型；要求参考 Skill Hub 类产品的设计。对照 SkillHub、skills-hub.ai 与 Agent Skills 标准后的结论：

- Skill Hub 的对象是"包"（`SKILL.md` + scripts/references/assets），元数据是类别、来源出处、版本、兼容客户端、更新时间、安全扫描结果、使用量；交互是"库"（搜索、按类别/来源浏览、详情页渲染 SKILL.md 与版本历史、一条命令安装、从来源同步）；调用是"渐进式加载"（只加载名称与描述，任务匹配时才读入全文），技能对任何 Agent 可用，由模型按描述激活，不预先绑定。
- Tapper 现在把 Skill 绑定到 Agent 修订（V1 能力 4），是为可追溯与治理；两者都可以改由"冻结本轮实际激活的 Skill 修订"和"启用/停用 + 项目可用范围"实现。绑定的副作用：新版本要逐个 Agent 升级、未绑定的 Agent 用不到、用户要手动勾选。

建议方向（已向产品负责人提出，待其定夺）：**Skill 库 + 自动激活**。

- Skill 库页（取代名册）：搜索、按类别/适用任务/来源筛选；卡片显示描述、版本、来源、更新时间、周期内激活次数、状态；详情页渲染 SKILL.md、文件清单、版本历史、激活统计（被哪些 Agent/对话激活、满意度）；动作：导入（文件/zip/粘贴）、新建、出新版本、启用/停用。
- 运行时：项目内所有启用的 Skill 对 Tapper 默认与全部 Agent 可见（名称 + 描述进入上下文），模型按任务匹配激活并读入全文；Agent 可选"常驻"或"排除"某些 Skill；用户仍可手动指定；每轮冻结实际激活的 Skill 修订；取消"有新版本/升级"流程。改动涉及 `apps/tap-ai-backend/src/tap/modules/chat/application/skill_selection.py`（现为"绑定 ∪ 手选"）与对话图。
- 统计口径（已确认）：激活次数（自动/手动）、使用者数、满意度为主；成本、耗时为次要、不分摊（同步 worker 给 trace 加 `skill:<skill_id>` 标签）。
- 不做：安全扫描与信任分级、外部 Hub 同步/从 URL 导入、团队/负责人/组织架构。

产品负责人 2026-11-01 决定：不绑定，按 Skill Hub 来做，功能大就分阶段；同日确认了 [Skill Hub 设计](../superpowers/specs/2026-11-01-skill-hub-design.md)（第 1 阶段：库 + 自动激活 + 绑定迁移 + 用量；第 2 阶段：来源与版本；第 3 阶段：信任与共享）。

接手步骤：

1. 用 superpowers writing-plans 按 spec 第 1 阶段写 `docs/superpowers/plans/2026-11-01-skill-hub-phase-1.md`，任务按 TDD 拆分；产品负责人要求计划与实施都交给 Opus，人工只做审查。
2. 实施顺序建议：迁移与 `turn_skill_activation` 回填 → `skill_selection` 改为"手动 ∪ 常驻" + `skill.activate` 工具与预算 → `skill_hub` 模块与接口（复用 `agent_team` 的周期/缓存/降级工具）→ 同步 worker 的 `skill:` 标签与游标重置 → 前端库页、详情、Agent 表单常驻/排除、对话展开 → 激活精度夹具与 e2e 旅程 → 名册 Agent 去常驻复跑验证。
3. 每个任务完成后派审查员（Opus）审查，Important 以上必须修完再合入；最后跑 `make check`、`make test`、`make demo-e2e`。
4. 合入 `develop` 后更新路线图 "Skill Hub" 行、`docs/architecture.md`（`skill_hub` 模块、`skill.activate` 工具、`skill:` 标签）与本文档。

## 4. 环境与操作要点

- 工作副本：`.worktrees/develop`（分支 `develop`），`.env` 已从主检出复制进去（不入库）。`make demo-dev` 正在该目录运行（Web `127.0.0.1:5173`，API `127.0.0.1:8000`）；由 Claude Code 后台启动的进程可能被回收，长期运行请在自己的终端启动。
- 演示 API 写请求需要 `Origin: http://127.0.0.1:5173` 与 `Idempotency-Key`；对话必须带 `sourceRevisionIds`（≤ 20）；删除会话是软删除。
- 运行数据缓存 5 分钟且各查询缓存时间不同：新运行后几分钟内"最近运行"可能仍为"—"，等缓存过期再截图。
- `make demo-e2e` 若被中断，会遗留 `18000` 端口进程与 `$TMPDIR/tap-tapper-e2e.lock` 目录，清理后再跑；一轮约 25 分钟。
- 隔离 MySQL 集成测试需要 `TAP_RUN_MYSQL_INTEGRATION=1`，会自行启动临时 MySQL 容器，约 30 分钟。
- 自定义 Agent 的中文名已在库中改成英文（备份 `~/tap-synthetic-2026-10-28/rename/`）；界面默认英文，演示截图用英文界面、完整 TAP 导航与 TAP 品牌。
- 仓库文件不得出现雇主名称；香港语料原始文件只在 `.worktrees/v1-closeout/.local/` 本地保存。
- Opus 代理会被会话限额中断（"session limit"）；恢复后用同一代理继续而不是重开，以免重做。

## 5. 待办

| 事项 | 负责 | 说明 |
| --- | --- | --- |
| 名册遗留的三个问题 | 开发 | 第 3 轮修订后仍未解决，根因在平台与语料而非提示词：Automation Script Writer 纯文字请求超 2048 token 失败（返回 503）；Claims FAQ Helper 把虚构公司的流程指南规则套到香港表格上（建议把 3 份合成流程文档改写成与香港产品一致，或在选源时不混选）；英文问题检索不到中文指南。见[名册](2026-10-28-agent-roster.md)第 8 节 |
| 名册验证暴露的产品问题 | 开发 | 1) 回答输出上限 2048 token，长回答直接失败；2) 带引用标记的表格行被 `parse_inline_answer` 删除；3) 英文问题检索不到中文流程文档（多文档时被挤出）；4) 对话请求不带 `insightsReportRefs`，`insights.query` 在对话中不可用且拒答无说明；5) 回答正文不进入对话历史，追问轮看不到上一轮生成的内容，Agent 之间无法在同一会话接力；6) 证据标签 `S1` 泄漏到正文与代码注释。建议进入路线图 V2 的 LangGraph 收尾或单独排期 |
| 重拍团队页截图 | 下一会话 | 名册就绪后更新演示文档第 1.3 节的团队总览与组织架构图截图 |
| Skill Hub 第 1 阶段 | 下一会话 | spec 已确认：先用 writing-plans 出计划（Opus），再 Opus 实施、人工审查；步骤见第 3 节 |
| 业务验证一周 | 负责人 | spec 7.4；完成后把路线图 V2.1 改为完成 |
| 清理验证数据 | 负责人 | `~/tap-synthetic-2026-10-28/cleanup.py synthetic|real` |
