# Agents 名册与 Skills 名册交接

创建日期：2026-10-28。归属[产品路线图](../superpowers/plans/2026-10-01-product-roadmap.md) V2.1 及其后续。接续 [Agent 团队视图业务流程演示](2026-10-28-agent-team-business-demo.md)与 [Agent 团队视图业务验证交接](2026-10-27-agent-team-business-validation-handoff.md)。本文给下一个会话用：先读第 1 节确认状态，再按第 4、5 节继续。

## 1. 状态

| 项 | 状态 |
| --- | --- |
| V2.1 Agent 团队视图 | 工程完成；2026-10-28 两轮改动已合入并推送 `origin/develop`（`845d1c68`）：审查修复、分析平台式面板 v2、TAP 品牌与完整模块导航、香港语料数据重建、英文版演示文档 |
| 演示数据 | 只用香港公开语料（8 份产品小册子、2 份理赔表、3 份合成流程文档）；真实运行 178 轮 + 仿真历史 397 次，核对 252 项一致；脚本与清理命令在 `~/tap-synthetic-2026-10-28/`（见交接文档第 2–4 节） |
| 真实 Agent 名册 | 已建成并验证（29 个会话、37 轮真实模型），见 [Agent 名册](2026-10-28-agent-roster.md)；Test Case Designer、Automation Script Writer、Claims FAQ Helper 的提示词还需再修订一轮（已派 Opus，结果未提交时见 `git status`） |
| Skills 名册设计 | 口径已和产品负责人确认（第 3 节），设计要点已在对话中提出、尚未确认；spec 未写 |
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

## 3. Skills 名册设计（待写 spec）

已确认的决定：

- 范围只做"Skill 名册与使用统计"，把 Agent 团队视图的做法搬到 Skill；安全扫描、从 URL/Git 导入、内置 Skill 包、成本分摊、按 Skill 处理反馈都不做。
- 口径：主指标从演示库 Turn 冻结输入算——参与运行数（`skill_revision_ids` 含该 Skill 任一修订；拆成直接选用 `requested_skill_revision_ids` 与经 Agent 绑定）、使用者数、当前绑定的 Agent 数、满意度（样本 < 10 为"样本不足"）、最近使用；成本、耗时、失败只作次要指标，由同步 worker 给 `turn.request` trace 追加 `skill:<skill_id>` 标签后从 Langfuse 取，多 Skill 共享一次运行时各自全额计入、不分摊、页面注明不可相加。

已提出、待确认的设计要点：

1. 成员：自定义、导入、内置（负责人显示"平台维护"）；档案含负责人（默认创建/导入人，可转交）、来源、修订历史、适用任务、绑定它的 Agent（各自绑定的修订、是否落后）、状态（已停用／近 30 天参与运行 ≥1 为活跃／闲置）。
2. 维护动作：`revised`、`disabled`、`enabled`、`owner_transferred`，与写操作同一事务。
3. 架构：新后端模块 `skill_team`，复用 `agent_team` 的周期、粒度、状态、缓存、期限与降级（抽成共享工具，不改行为）；迁移 0038 给 `skill` 加 `owner_actor_id`、`owner_assigned_at`（回填创建人），新表 `skill_maintenance_event` 与 `turn_skill_usage(turn_id, skill_id, skill_revision_id, direct, created_at)`（Turn 创建时写入，从历史快照回填）；同步 worker 加 `skill:` 标签并重置游标补推。
4. API：`/ai/skill-team/{summary, members, members/{skillId}, members/{skillId}/history, people, report, export}` 与 `POST skills/{skillId}/owner`；错误码、`runtimeStatus`、`preset`、CSV 规则与 Agent 侧一致；权限 `ai.skill_team.read`，写沿用 `ai.skills.write`。
5. 前端：Skills 页四个标签（My Skills / Roster / Report / Configuration），复用 Agents 页的指标卡、趋势、热力图、排行表、档案抽屉、汇报页组件；组织架构图换成"负责人 → Skill → 绑定 Agent"的绑定关系图。
6. 方案：独立 `skill_team` 模块（推荐）而非把 `agent_team` 泛化成资产团队。
7. 验收：单元、契约、隔离 MySQL 集成、Vitest、`make demo-e2e` 旅程（导入 Skill → 绑定 Agent → 对话 → 名册出现用量 → 汇报导出）、截图；业务验证同 Agent 侧。

下一步：产品负责人确认要点后，写 `docs/superpowers/specs/2026-10-28-skill-roster-design.md`（按 superpowers brainstorming 流程），再用 writing-plans 出计划，由 Opus 实施、人工审查。参考实现：`apps/tap-ai-backend/src/tap/modules/agent_team/`、`apps/tap-ai-frontend/src/widgets/tap/workspace/agents/team/`、Agent 侧 spec 与 [Agent 团队视图交接](2026-10-25-agent-team-view-handoff.md)第 2 节的代码位置表。

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
| Agent 名册第 3 轮提示词修订 | 下一会话 | 审查 Opus 对 Test Case Designer、Automation Script Writer、Claims FAQ Helper 的修订与重测结果，更新名册文档后提交 |
| 名册验证暴露的产品问题 | 开发 | 1) 回答输出上限 2048 token，长回答直接失败；2) 带引用标记的表格行被 `parse_inline_answer` 删除；3) 英文问题检索不到中文流程文档（多文档时被挤出）；4) 对话请求不带 `insightsReportRefs`，`insights.query` 在对话中不可用且拒答无说明；5) 回答正文不进入对话历史，追问轮看不到上一轮生成的内容，Agent 之间无法在同一会话接力；6) 证据标签 `S1` 泄漏到正文与代码注释。建议进入路线图 V2 的 LangGraph 收尾或单独排期 |
| 重拍团队页截图 | 下一会话 | 名册就绪后更新演示文档第 1.3 节的团队总览与组织架构图截图 |
| Skills 名册 spec | 下一会话 | 第 3 节要点确认后写 spec、出计划、Opus 实施 |
| 业务验证一周 | 负责人 | spec 7.4；完成后把路线图 V2.1 改为完成 |
| 清理验证数据 | 负责人 | `~/tap-synthetic-2026-10-28/cleanup.py synthetic|real` |
