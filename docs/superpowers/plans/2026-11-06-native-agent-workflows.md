# 通用任务与原生 Agent 工作流实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 原生 LangGraph 能按需运行单个或多个 Agent，支持定向补充、暂停恢复，并在 Agents/Tapper 展示真实工作流。

**Architecture:** 新 `workflows` 域拥有任务、Agent 实例、计划修订、事件与控制命令；读取现有 `ai` 资产修订。独立 worker 执行新的版本化 LangGraph，既有聊天通过适配入口关联任务；前端消费持久快照和增量事件。

**Tech Stack:** 现有 Python/FastAPI/LangGraph/MySQL/Redis；React/TypeScript/antd、Vitest、Playwright；不新增图布局依赖。

**Spec:** [整体设计](../specs/2026-11-06-agent-workflows-and-test-loop-design.md) §3–7；[总计划与路径缩写](2026-11-06-agent-workflows-and-test-loop.md)。

## Global Constraints

- 使用总计划全部约束；本阶段不实现 Copilot 运行，不把编码请求静默改为原生编码。
- 原生路径只通过现有 `B/modules/ai/ports/gateway.py::ModelGateway` 调用模型。
- 不修改既有 `tapper-chat-v2` 检查点含义；新图使用 `agent-workflow-v1` 与 state schema 1。旧 Turn 能继续恢复。
- Orchestrator 保持主上下文，子 Agent 独立上下文；主 Agent 决定委派，平台校验权限、预算、依赖与实例身份。
- 所有业务表具有项目范围；MySQL 事务提交状态与事件后才发送 Redis 通知。
- 简单问答可单次响应结束；不得为显示流水线额外制造专业 Agent 或模型调用。
- 暂停与取消先记录请求，执行端确认后改变终态；失联显示 recovering。

## 文件结构

| 区域 | 新文件与职责 |
| --- | --- |
| `B/modules/workflows/domain/` | `models.py` 状态/实例；`transitions.py` 状态机；`commands.py` 控制规则；`delegation.py` 委派验证 |
| `B/modules/workflows/ports/` | `store.py` 事务存储；`runtime.py` 运行控制；`catalog.py` 资产快照 |
| `B/modules/workflows/application/` | `service.py` 创建/控制；`native_graph.py` 主循环；`dispatch.py` 子运行；`recovery.py` 核对 |
| `B/modules/workflows/adapters/` | `schema.py/mysql.py` 持久化；`asset_catalog.py` 现有资产适配；`native_runtime.py` LangGraph 运行；`events.py` 通知 |
| `B/interfaces/http/` | `routes/workflows.py` 与 `workflow_service.py` |
| `B/contracts/workflows.py` | HTTP 和 SSE 的严格 schema |
| `F/features/workflows/` | `api/client.ts`、`api/queries.ts`、`model/events.ts`、`model/layout.ts`、运行组件 |

### Task 1.1：任务状态、领域接口与表结构

**Files:** Create domain `models.py/transitions.py`、ports `store.py`、adapters `schema.py/mysql.py`、AI migration `0039_agent_workflows.py`；Modify `B/platform/db/registry.py`、`schema.py`；Create `BT/unit/workflows/test_transitions.py`、`BT/integration/test_workflow_store.py`、`BT/integration/test_workflow_migration.py`；Create `docs/superpowers/specs/2026-11-06-native-agent-workflows-design.md`。

**Interfaces:** 定义 `WorkflowStatus`：`queued/running/waiting_dependency/awaiting_input/pausing/paused/recovering/succeeded/failed/canceling/canceled/skipped`。`transition(current: str, signal: str) -> str` 拒绝不允许的转换。`TaskSnapshot(task_id, project_id, conversation_id: str | None, plan_revision: int, status: WorkflowStatus, last_sequence: int)`；`NodeSnapshot(node_id, task_id, parent_node_id: str | None, agent_revision_id: str | None, kind: Literal["agent","service","approval"], status, attempt: int)`。`WorkflowEvent(event_id, task_id, sequence: int, node_id: str | None, attempt: int, kind: str, payload: dict[str, object], occurred_at: datetime)`。

表：`workflow_task`（目标、scope、状态、累计预算、plan_revision、next_sequence、lease owner/token/until）；`workflow_plan_revision`（不可变目标/依赖/输入快照）；`workflow_node`（岗位/修订/父节点/runtime、状态）；`workflow_attempt`（node_id+attempt、graph_run_id、checkpoint/provider refs）；`workflow_dependency`（from/to 同任务且无环）；`workflow_event`（task+sequence 唯一，event_id 去重）；`workflow_command`（command_id、payload_digest、target、expected_plan_revision、receipt）；`workflow_artifact`（不可变对象摘要与版本引用）。复用现有 `graph_run` 作执行底层，不把它的粗状态直接暴露成任务状态。

- [ ] 固化独立 spec：本节表、状态转换、项目隔离、与 Turn/graph_run 的关系及任务预算规则。
- [ ] 写失败测试，覆盖暂停/取消确认、终态不可覆盖、恢复不清预算；代表性断言：

```python
import pytest
from tap.modules.workflows.domain.transitions import transition

def test_pause_requires_runtime_ack():
    assert transition("running", "pause_requested") == "pausing"
    assert transition("pausing", "runtime_paused") == "paused"
    with pytest.raises(ValueError):
        transition("running", "runtime_paused")
    with pytest.raises(ValueError):
        transition("succeeded", "runtime_failed")
```

- [ ] 运行 `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/workflows/test_transitions.py -q`，先失败；实现状态机、类型和表。状态信号包含 claim、dependency_ready、input_needed、input_applied、pause_requested、runtime_paused、resume_requested、lease_lost、reconciled、cancel_requested、runtime_canceled、runtime_succeeded、runtime_failed、plan_skipped；每项列明允许来源和目标状态。
- [ ] 存储接口实现 `create_task(scope, request: dict[str, object], idempotency_key: str) -> TaskSnapshot`、`snapshot(scope, task_id: str) -> dict[str, object]`、`append_event(scope, task_id, lease_token, event_id, kind, payload) -> WorkflowEvent`、`events_after(scope, task_id, sequence: int, limit: int) -> tuple[WorkflowEvent, ...]`。锁任务行分配序号，同事务写状态/事件；旧 fencing token、跨项目 node、重复 key 不同 payload 拒绝。
- [ ] MySQL 测试使用 `owned_project_mysql`：两个独立 session 并发写序号不重复；事件提交前异常不留状态变化；重复创建只一个 task；升级/降级不删旧数据，旧 graph recovery 仍通过。

```bash
TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_workflow_store.py apps/tap-ai-backend/tests/integration/test_workflow_migration.py apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py -q
```

- [ ] 提交 `feat: persist versioned workflow tasks and events`。

### Task 1.2：控制指令与故障恢复

**Files:** Create domain `commands.py`、application `service.py/recovery.py`、ports `runtime.py`；Modify store/mysql；Create `BT/unit/workflows/test_commands.py`、`BT/integration/test_workflow_recovery.py`。

**Interfaces:** `ControlCommand(command_id, task_id, target_node_id: str | None, kind: Literal["steer","pause","resume","cancel","retry"], expected_plan_revision: int, text: str | None)`；receipt 状态 `accepted/pending/applied/rejected/stale`，含 reason、applied_plan_revision。`command_disposition(expected: int, actual: int, already_applied: bool) -> str`。`WorkflowService.submit(scope, command: ControlCommand) -> dict[str, object]` 只确认持久化；执行端 `ack_command(scope, command_id, lease_token, receipt)` 才确认应用。

- [ ] 写失败测试：

```python
from tap.modules.workflows.domain.commands import command_disposition

def test_stale_plan_cannot_apply_new_instruction():
    assert command_disposition(2, 3, False) == "stale"
    assert command_disposition(2, 3, True) == "applied"
```

- [ ] 运行 `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/workflows/test_commands.py -q`，先失败，再实现去重、版本比较和授权。
- [ ] 增补要求创建新的 plan revision；已完成实例保留，新返工建实例；暂停节点阻塞后继、独立分支继续；终态 retry 新 attempt，已完成任务 rerun 新 task。预算从 task 累加，不能因 attempt 清零。
- [ ] 恢复服务先核对 lease、checkpoint、运行状态，失联进 recovering；权威未确认停止前不标 paused/canceled。新 worker 取得新 token 后，旧 worker 任何后续写入失败。
- [ ] 在独立 MySQL 测试：重复指令返回同回执、同 ID 不同内容冲突；两条并发 steer 有稳定服务端顺序；崩溃发生在接收后/应用前与应用后/回执前均不重复应用；取消后的重启不能自动续跑。

```bash
TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_workflow_recovery.py -q
```

- [ ] 提交 `feat: coordinate workflow controls and recovery`。

### Task 1.3：目录冻结、路由与显式 Agent 选择

**Files:** Create ports `catalog.py`、adapters `asset_catalog.py`、application `routing.py`；Modify `B/modules/ai/domain/tools.py`、`B/contracts/http.py`、`B/interfaces/http/routes/conversations.py`、`B/interfaces/http/conversation_selection.py`；Create `BT/unit/workflows/test_catalog_routing.py`、`BT/contract/test_workflow_selection.py`。

**Interfaces:** `AgentSnapshot(agent_id, revision_id, name, description, instruction, tool_ids: tuple[str, ...], model_alias: str | None, skill_revision_ids: tuple[str, ...])`；`freeze_agents(scope, requested_ids: tuple[str, ...]) -> tuple[AgentSnapshot, ...]`。Skill 保持现有自动激活机制；开始时冻结候选 Skill 修订与常驻正文，不能恢复后换成最新文本。`RouteDecision(kind: Literal["native","coding","execute"], reason: str, confidence: float, required_capabilities: tuple[str, ...])`。分类可与入口模型响应合并，不新增强制前置模型轮次。

- [ ] 写失败测试，覆盖选择被停用/越权 Agent、重复显示名、恢复保持旧修订、解释代码仍 native、生成脚本 coding、执行已发布制品 execute。用于验证显式意图与模型建议合并的纯函数：

```python
from tap.modules.workflows.application.routing import requested_delegate

def test_selected_agent_is_delegate_not_replacement_root():
    choice = requested_delegate("qae", ("orchestrator", "qae"))
    assert choice == {"root": "orchestrator", "delegate": "qae"}
```

- [ ] 同文件窄测先失败，再实现 `requested_delegate(selected_id: str | None, allowed_ids: tuple[str, ...]) -> dict[str, str | None]`，未知 ID 拒绝；内置角色通过既有资产 seed 机制创建版本，不改用户自定义 Agent。
- [ ] 新请求字段 `requestedAgentIds` 与 `workflowTaskId` 明确委派及已有任务关联；旧 `agentRevisionId` 保持旧入口兼容，在新入口映射为显式委派，不能替换主 Orchestrator。多个显式选择保留全部，不再使用 `.find()` 静默只取一个。
- [ ] 编码能力未启用时返回明确不可用；低置信度且需扩大执行权限时请求澄清；进度查询读现有状态，追加要求走 Task 1.2，不能新建重复 task。
- [ ] 单测与 HTTP 契约通过，提交 `feat: route requests with frozen agent selections`。

### Task 1.4：原生 agent loop、子运行与独立 worker

**Files:** Create domain `delegation.py`、application `native_graph.py/dispatch.py`、adapters `native_runtime.py/events.py`、`B/entrypoints/tapper_workflow_worker.py`；Modify `B/entrypoints/tapper_runtime.py`、`.env.example`、`scripts/run-tapper-dev.sh`；Create `BT/unit/workflows/test_native_graph.py`、`BT/integration/test_native_workflow.py`。

**Interfaces:** `NativeWorkflowRuntime.start(scope, task_id: str) -> None`、`advance(scope, task_id: str, lease_token: str) -> None`；`DelegationRequest(agent_id: str, objective: str, input_refs: tuple[str, ...], expected_output: str, depends_on: tuple[str, ...])`；`delegate(scope, task_id, request, idempotency_key) -> str` 返回 node ID。`can_dispatch(dependencies: tuple[str, ...], statuses: dict[str, str], slots: int) -> bool`。

- [ ] 写失败测试：

```python
from tap.modules.workflows.domain.delegation import can_dispatch

def test_waiting_dependency_does_not_take_a_worker_slot():
    assert not can_dispatch(("ba",), {"ba": "running"}, 2)
    assert can_dispatch(("ba",), {"ba": "succeeded"}, 1)
    assert not can_dispatch((), {}, 0)
```

- [ ] 通过现有 DeterministicModelGateway 增加脚本化 response fixture：直接回答、普通工具、单委派、双独立委派、等待后汇总。运行 `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/workflows/test_native_graph.py -q`，先失败。
- [ ] 构建新图 `ingress → model ⇄ tools → verify → finalize`；工具执行按类型分派普通工具或委派命令，子运行使用独立 messages/checkpoint namespace。记录 tool_call_id→node_id，恢复不能重复 spawn；等待子任务时持久化并释放 worker，不 busy-poll、不把子任务全文塞回主上下文。
- [ ] worker 领取任务使用现有 `claim_transaction` 重试规则；新增 `TAP_WORKFLOW_ENABLED=false`、`TAP_WORKFLOW_MAX_PARALLEL_AGENTS=3`、`TAP_WORKFLOW_MAX_MODEL_CALLS=40`、`TAP_WORKFLOW_MAX_SECONDS=900`、`TAP_WORKFLOW_MAX_ATTEMPTS=3`，task 创建时冻结。费用上限可配置，未知成本保持 unknown，仍执行调用数/时间硬限制。委派和普通工具均重查授权。
- [ ] 独立 MySQL 集成测试用 barrier 协调两个子任务，不用 sleep 证明并行；断电重启只重试未结算动作；保持旧 `test_agent_loop_mysql.py` 的既有聊天验收。

```bash
TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_native_workflow.py apps/tap-ai-backend/tests/integration/test_agent_loop_mysql.py -q
```

- [ ] 提交 `feat: run durable native agent workflows`。

### Task 1.5：快照、SSE、节点详情与控制 HTTP

**Files:** Create `B/contracts/workflows.py`、`B/interfaces/http/routes/workflows.py`、`workflow_service.py`；Modify HTTP app/dependencies、`scripts/export_contracts.py`；Create `BT/contract/test_workflow_http.py`、`BT/contract/test_workflow_stream.py`；生成 `contracts/events/workflow-stream.schema.json`、OpenAPI 与 TS schema。

**Interfaces:** 路由前缀 `/api/v1/projects/{project_id}/workflows`：POST 创建、GET 列表、GET `/{task_id}` 快照、GET `/{task_id}/events` SSE、POST `/{task_id}/commands`、GET `/{task_id}/nodes/{node_id}`、GET `/{task_id}/nodes/{node_id}/logs` 分页。快照包含 `lastSequence/nodes/edges/planRevision/commands/artifacts`；事件包含 `eventId/taskId/sequence/nodeId/attempt/kind/payload/occurredAt`。POST 使用 Idempotency-Key，过期计划 409；跨项目/不可见对象统一现有授权错误，不泄露存在性。

- [ ] 写失败契约测试：快照拿到序号 N 后写 N+1，重连只回 N+1；重复 event 不重复节点；过期游标返回 `snapshot_required`；未提交事务不推送；节点和产物访问逐个校验 scope。纯游标函数位于 workflow_service：

```python
from tap.interfaces.http.workflow_service import replay_from

def test_snapshot_cursor_and_client_cursor_do_not_skip_events():
    assert replay_from(snapshot_sequence=10, client_sequence=7) == 7
    assert replay_from(snapshot_sequence=10, client_sequence=None) == 10
```

- [ ] `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_workflow_http.py apps/tap-ai-backend/tests/contract/test_workflow_stream.py -q` 先失败；实现路由、snapshot+cursor 和分页日志。`replay_from` 不接受大于当前高水位的游标，返回 409 要求重取快照。
- [ ] SSE 从 MySQL 回放，Redis 仅唤醒；快照使用一致性读；日志读取脱敏并限制单页，返回对象摘要引用而非本机任意路径。
- [ ] 执行 `make contracts`，再运行窄测与既有 generated-contract 测试；不得手写前端后端各一套事件类型。
- [ ] 提交 `feat: expose workflow snapshots streams and commands`。

### Task 1.6：Agents 运行视图与 Tapper 交互

**Files:** Create `F/features/workflows/api/{client,queries}.ts`、`model/{events,layout}.ts`、`components/{RunList,RunDetail,WorkflowGraph,NodeDrawer,ActivityTimeline,WorkflowCard}.tsx` 及同目录测试；Modify `F/widgets/tap/workspace/agents/team/AgentsPage.tsx`、`AgentProfileDrawer.tsx`、`F/widgets/tap/workspace/TapperChat.tsx`、`F/widgets/tap/TapperWorkspace.tsx`；Create `apps/tap-ai-frontend/tests/e2e/workflows.spec.ts`。

**Interfaces:** API types 引用生成 schema。`advanceCursor(current: number, incoming: number): "ignore" | "apply" | "resync"`；`applyWorkflowEvent(snapshot, event)` 按 sequence/attempt 幂等投影，缺口请求快照。`layoutWorkflow(nodes, edges)` 使用有向分层布局，同层平行、父层折叠，拒绝环并显示数据异常，不用 force graph 表示执行依赖。

- [ ] 写失败测试：

```typescript
import { expect, it } from "vitest";
import { advanceCursor } from "./events";

it("requests a snapshot when an event gap appears", () => {
  expect(advanceCursor(8, 8)).toBe("ignore");
  expect(advanceCursor(8, 9)).toBe("apply");
  expect(advanceCursor(8, 11)).toBe("resync");
});
```

- [ ] `corepack pnpm --filter @tap/ai-frontend exec vitest run src/features/workflows` 先失败；实现 API hooks/reducer，然后实现 UI。顶层显示 Orchestrator 当前活动与等待原因；单问答仅轻量卡，专业任务节点可展开；服务/审批节点明确不同类型。
- [ ] Agents 增加运行页签，档案能看本 Agent 多个并行实例；Tapper picker/@ 解析成稳定 ID，重名时要求选择具体档案；发送全部显式选择。定向输入展示 accepted/pending/applied/stale，暂停中不显示已暂停。
- [ ] 组件测试覆盖重连提示、失败 attempt 保留、更新不抢滚动/选择、日志分页、节点补充要求、取消状态竞态、键盘展开/焦点、窄屏纵向和 reduced-motion。检查实际视觉，沿用现有浅色/橙色，不加入装饰性虚假工作动画。
- [ ] 浏览器场景：原生单问答→两个专业节点并行→指定节点补充→刷新/重连→从 Agent 档案找回同一 task；运行环境由阶段 1.7 专属脚本创建，不能连共享 Demo。
- [ ] 窄测与 UI 验收通过后，提交 `feat: show interactive agent workflow runs`。

### Task 1.7：现有 Agent 全覆盖与阶段验收

**Files:** Create `B/modules/workflows/application/observations.py`、`BT/integration/test_workflow_agent_coverage.py`、`scripts/run-workflow-e2e.py`、`docs/guides/2026-11-06-agent-workflows.md`；Modify 现有 `tapper_chat_turn.py/tapper_suggestion_worker.py/tapper_test_design_worker.py/tapper_graph_worker.py` 入口和相关实际 Agent 调用边界、`B/modules/agent_team/application/service.py`、`Makefile`、`docs/architecture.md`、产品路线图。

**Interfaces:** `record_agent_invocation(scope, task_id, agent_key, parent_node_id, invocation_key)` async context manager：实际进入/退出才发事件；同 invocation_key 重试去重，内部工具步骤不是额外 Agent。现有默认、内置、自定义、后台 Agent 的清单写入 guide，包含对应调用入口和集成测试；纯解析/摄取作业保持 service 类型。

- [ ] 写失败覆盖测试：运行普通问答、自定义 Agent、推荐问题生成、关系分析、测试方案生成，断言每个实际 Agent invocation 有且只有一个可查询实例；平台可创建任务父层归属，但不能凭空记录 Orchestrator 模型调用。通过受控入口逐步统一到原生 Orchestrator，不重复执行既有模型工作。
- [ ] 实现观察适配与目录映射；历史统计仍按原口径，实时视图查新库；Agent 停用/改名不改变旧实例。
- [ ] 实现 `scripts/run-workflow-e2e.py`：新建带唯一标签的本机容器/项目/临时目录，显式注入确定性模型；启动 API/Relay/workflow worker/Web，调用指定 Playwright spec，finally 仅删除自己标签资源。新增 `make workflow-e2e` 指向该脚本。
- [ ] `make workflow-e2e`；原生真实模型验收另以 `TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1` 调用 smoke，记录模型来源与结果，不用模拟场景代替。
- [ ] 运行总计划合入检查，更新现状架构和 guide，仅将阶段 1 标工程完成；提交 `feat: integrate native workflow coverage and acceptance`。
