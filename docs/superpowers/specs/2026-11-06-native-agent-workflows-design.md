# 原生 Agent 工作流任务持久化设计

本文件固定[总体设计](2026-11-06-agent-workflows-and-test-loop-design.md)第 3–7 节中阶段 1.1 的存储合同。阶段 1.1 只建立持久任务、版本引用、事件和领域状态；命令应用、worker、新 LangGraph 与界面由后续任务实现。默认助手对外仍称 Tapper。

## 事实来源与关系

MySQL 的 `workflow_task` 是任务状态、计划版本、预算和 lease 的权威记录。`workflow_event` 按任务分配递增序号，和其导致的任务变化在同一事务提交。Redis 只能在提交后提示消费者，不保存独立状态。任务通过可空 `conversation_id` 关联聊天入口，独立于 Turn；现有 `ai_graph_run` 仍保存执行底层的 checkpoint 和 graph 状态，其粗状态不能直接替代 workflow 状态。阶段 1.1 不改变既有 `tapper-chat-v2` 或旧 Turn 恢复语义。

八张新表均带现有 `enterprise_id/project_id/actor_id/identity_mode/identity_origin` 范围字段与项目、actor 外键。`workflow_task` 保存目标、创建幂等键及请求摘要、会话关联、状态、`plan_revision`、下一个事件序号、累计预算上限/用量、lease owner/token/until；任务 ID 全局唯一，项目内创建键唯一。`workflow_plan_revision` 保存不可变目标、依赖和输入快照，以 `(project_id, task_id, revision_number)` 唯一。`workflow_node` 保存岗位、冻结 Agent 修订、父节点、运行时、状态、当前 attempt；`workflow_attempt` 保存每次尝试的节点、graph run、checkpoint/provider 引用及错误快照；`workflow_dependency` 保存任务内节点边和计划版本；`workflow_event` 保存事件 ID、任务内唯一序号、节点/attempt、类型、载荷及摘要和时间；`workflow_command` 预留命令 ID、目标、载荷摘要、期望计划版本与回执；`workflow_artifact` 保存不可变对象引用、内容摘要及版本引用。节点父子、事件节点、命令目标、产物节点及依赖两端均通过 `(project_id, task_id, node_id)` 复合外键绑定同一任务；依赖不允许自环，新增依赖时还必须检查路径以拒绝间接环。

计划修订、attempt 和产物采用新增记录保留历史；应用写入不得覆写旧版目标、输入、错误证据或制品摘要。节点失败后的重试建立新 attempt，保留旧终态 attempt。已完成任务的重新运行建立新任务并记录来源，不把旧任务终态改回 queued。预算按整个 task 累计，委派、重试及恢复均不能重置。现阶段 `budget_limit`/`budget_used` 以非负整数计数器记录模型 token 和工具调用等配额；无上限的计数器仍累计。后续计费精度如需金额，须另行明确计量单位，不能用浮点金额。

## 状态与确认边界

`transition(current, signal)` 只接受以下来源与结果；其余组合拒绝。终态 `succeeded/failed/canceled/skipped` 无出边。

| 信号 | 允许来源 → 目标 |
| --- | --- |
| `claim` | queued → running |
| `dependency_ready` | waiting_dependency → queued |
| `dependency_wait` | running → waiting_dependency |
| `input_needed` | running → awaiting_input |
| `input_applied` | awaiting_input → queued |
| `pause_requested` | running → pausing |
| `runtime_paused` | pausing → paused |
| `resume_requested` | paused → queued |
| `lease_lost` | running/pausing/canceling → recovering |
| `reconciled` | recovering → queued |
| `cancel_requested` | queued/running/waiting_dependency/awaiting_input/paused → canceling |
| `runtime_canceled` | canceling → canceled |
| `runtime_succeeded` / `runtime_failed` | running → succeeded / failed |
| `plan_skipped` | queued/waiting_dependency → skipped |

`pausing`、`canceling` 是持久请求，不证明执行端已经停止。只有执行端核对后发出的 `runtime_paused`、`runtime_canceled` 可进入确认状态。失联标为 `recovering`，重新接管前核对外部作业和 checkpoint。1.1 的状态机只表达确认规则；控制命令、worker 核对与真实暂停/取消在 1.2 后实现。

失联时 `workflow_task.recovering_from` 保存原状态。`reconciled` 只有在来源为 `running` 且后续可信核对确认允许继续时才可回到 queued；从 `pausing` 或 `canceling` 失联不得盲目重启，后续控制服务必须提供已停止或仍在运行的明确结果。状态机本身不执行外部核对，1.1 的存储层先拒绝这两种来源的简单 `reconciled`。

## 存储接口和事务

`WorkflowStore` 暴露 `create_task(scope, request, idempotency_key)`、`snapshot(scope, task_id)`、`append_event(scope, task_id, lease_token, event_id, kind, payload)`、`events_after(scope, task_id, sequence, limit)`。`scope` 必须是可信 `ProjectScopeContext`；查询带 enterprise 与 project 条件。创建请求以规范 JSON 摘要去重：同键同载荷返回原任务，同键不同载荷拒绝。创建任务、初始计划修订和序号 1 的 `task.created` 事件一起提交。

事件写入锁定 task 行再分配 `next_sequence`；写状态/预算、插入事件和推进序号在同一事务。重复事件 ID 且同任务/类型/载荷返回原事件，不同载荷拒绝。若 task 的 lease token 与调用 token 不一致，写入拒绝；新 worker 获取新 token 后旧 worker 无法推进。节点引用须同任务；数据库复合外键是最后防线。`task.budget` 只接受非负整数增量，超过上限时整笔事务回滚。`snapshot` 在首次 SQL 前为本次事务显式设定 `REPEATABLE READ`，再取 task、nodes 与不超过 task `last_sequence` 的事件；即使连接池默认 `READ COMMITTED`，游标和视图也属于同一数据库快照，且读取不占用 task 写锁。`events_after` 以项目、任务及序号分页，供断线补读。

`append_event` 的任何写入（状态、预算、普通事件）还要求非空 lease owner、非空 token 与未过期 `lease_until`；仅 token 字符串相等不足以提交。初始 `task.created` 事件属于创建事务，不经过 worker lease。租约领取与续租由后续服务实现。

迁移 `0039_agent_workflows` 接在 AI head `0038_skill_hub`，不碰 TAP 非 AI 的 `0005_insights_semantics` 链。空表可以降回 `0038`；任何 workflow 业务记录存在时，降级拒绝删表，保留新任务证据。迁移前后的既有 graph/Turn 表和数据保持原样。
