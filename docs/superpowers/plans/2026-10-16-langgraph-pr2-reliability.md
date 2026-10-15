# LangGraph PR 2：可靠性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对话与 Insights 解释 Turn 的瞬时失败释放租约、按指数退避后从最后完成节点的 checkpoint 续跑；确定性失败直接终止；所有权丢失静默退出；attempt 有上限；worker 主循环与同批其他 Turn 不受单次异常影响。

**Architecture:** 新增一个纯函数分类器 `classify_turn_failure`（entrypoints 层，可同时引用各模块异常）。`ChatGraph` 对被判为瞬时的节点异常不再捕获为 `state["failure"]`，而是向外抛出，LangGraph 因此不为该节点写 checkpoint；worker 捕获后调用仓储 `defer_turn` 立即让租约过期并写入 `next_attempt_at`。`claim_queued` 只领取 `next_attempt_at` 已到的 Turn，并在 `processing_attempt` 已达上限时直接写 `turn.failed`。主循环抽成可测试的 `run_worker_loop`。

**Tech Stack:** Python 3.13、LangGraph 1.2.12、SQLAlchemy async + Alembic（MySQL）、pytest、OpenTelemetry（`tap.platform.telemetry`）。

**Spec:** [Tapper LangGraph 编排设计](../specs/2026-10-14-tapper-langgraph-orchestration-design.md) 第 3 节、第 9 节 PR 2。

## Global Constraints

- 错误三分类：瞬时（释放租约并续跑）、确定性（`turn.failed` 或 `turn.abstained`）、所有权丢失（静默退出，不写任何东西）。
- 每次领取（含租约过期回收）`processing_attempt` 加 1，上限 `max_turn_attempts`，默认 `3`，配置项 `TAPPER_TURN_MAX_ATTEMPTS`（整数，`1..10`）；超过写 `turn.failed`，problem 类型 `answer-unavailable`。
- 退避：指数，基数 2s，上限 30s，带抖动：`base = min(30, 2 * 2 ** (attempt - 1))` 秒，实际延迟 `base * uniform(0.5, 1.0)`。
- 领取条件：“待处理或租约已过期，且 `next_attempt_at` 为空或 `<= now`”。
- 主循环：`run_once` 异常记录日志与 span 后退避 1s→30s（每次翻倍，成功一次后重置为 1s），只在 SIGINT / SIGTERM 时退出。
- 图版本 `tapper-chat-v2` 与 `CHAT_STATE_SCHEMA_VERSION = 1` 不变；不改 SSE 事件集合与载荷、HTTP 契约，`make contracts` 无差异。
- 测试设计生成（`InteractionGraph`、`modules/test_management/`）不动。
- 不新增依赖；Ruff 与类型检查通过；遵守 `tests/architecture/` 的模块边界。
- 后端测试命令：`uv run --project apps/tap-ai-backend pytest <path> -q`（仓库根目录执行）；MySQL 集成测试需加 `TAP_RUN_MYSQL_INTEGRATION=1`。
- 子代理不得运行 `make demo-up` / `demo-down` / `demo-reset`，不得使用 `git stash`；`make demo-e2e` 由主会话执行。
- 提交信息为简短小写祈使句 Conventional Commit，结尾附 `Co-Authored-By` 行。
- 分支 `feat/langgraph-pr2`，worktree `.worktrees/lg-pr2`，基于 `origin/develop` `07d54291`。

## Review Focus

1. 瞬时失败后续跑时 plan 截止时间早已过去（plan 在第 1 次 attempt 写入，默认 30s）：新 attempt 应获得新的截止时间，而不是立刻以“persisted answer budget exhausted”终止。→ Task 3 `test_new_attempt_rebases_plan_deadline`。
2. worker 在执行中崩溃、租约自然过期，且已是第 3 次 attempt：下一次领取直接写一次 `turn.failed`，不进入第 4 次执行，也不被重复终止。→ Task 2 `test_claim_fails_turn_whose_attempts_are_exhausted`。
3. 退避期间用户取消 Turn 或删除会话：`defer_turn` 之后的取消仍生效，到期后不会被再次领取。→ Task 2 `test_canceled_deferred_turn_is_not_reclaimed`。
4. 同批中一个 Turn 的失败终结本身抛异常（例如写 `turn.failed` 时 MySQL 断开）：其余 Turn 照常处理，主循环不退出。→ Task 4 `test_one_turn_crash_does_not_skip_the_rest_of_the_batch`。
5. 模型网关拒绝请求（4xx）被适配器包成 `AnswerUnavailable`：应判为确定性，不重试 3 次。→ Task 1 `test_gateway_rejection_is_deterministic_through_adapter`。

---

### Task 1: 失败分类与退避

**Files:**
- Create: `apps/tap-ai-backend/src/tap/entrypoints/tapper_turn_failures.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/ports/errors.py`（新增两个异常）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py`（第 264、304、452 行附近的 `except (ModelGatewayRejected, ModelGatewayUnavailable)`）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py:241-243`（`_plan_deadline`）
- Test: `apps/tap-ai-backend/tests/unit/entrypoints/test_turn_failures.py`

**Interfaces:**
- Produces:
  - `TurnFailureClass = Literal["transient", "deterministic", "ownership"]`
  - `classify_turn_failure(error: BaseException) -> TurnFailureClass`
  - `retry_delay(attempt: int, *, rng: random.Random | None = None) -> timedelta`（`attempt` 从 1 起；`attempt < 1` 抛 `ValueError`）
  - `knowledge/ports/errors.py`：`class ModelRejected(ModelUnavailable)`、`class AnswerRejected(AnswerUnavailable)`
  - `knowledge_service.py`：`class AnswerBudgetExhausted(TimeoutError)`，`_plan_deadline` 截止已过时抛它（消息不变）

分类表（按此顺序匹配，先命中先返回；未列出的异常一律 `deterministic`）：

| 结果 | 异常 |
| --- | --- |
| `deterministic` | `InsightsAuthorizationChanged`（虽是 `PermissionError` 子类，须先于所有权判断）、`AuthorizationDenied`、`ModelGatewayRejected`、`ModelOutputTruncated`、`ModelRejected`、`AnswerRejected`、`EvidenceBundleTooLarge`、`AnswerBudgetExhausted`、`InsightsBudgetExceeded`、`InsightsQueryUnavailable`、`DocumentStateChanged` |
| `ownership` | `PermissionError`、`ConversationConflict`、`ConversationNotFound`、`GraphLeaseLost` |
| `transient` | `GraphCheckpointRetryable`、`ModelGatewayUnavailable`、`ModelUnavailable`（含 `AnswerUnavailable`）、`SearchUnavailable`、`KnowledgeRuntimeUnavailable`、`IndexUnavailable`、`ArtifactUnavailable`、`AnswerSnapshotUnavailable`、`PolicyUnavailable`、`TimeoutError`、`ConnectionError`、`sqlalchemy.exc.OperationalError`、`sqlalchemy.exc.TimeoutError` |
| `deterministic` | `GraphCheckpointUnavailable`（非 Retryable 的部分；放在 Retryable 之后匹配） |

- [ ] **Step 1: 写失败测试**

`test_turn_failures.py`：
- `test_classification_table`：`pytest.mark.parametrize` 覆盖上表每个异常类各一个实例，断言返回值；另含 `ValueError("x")`、`RuntimeError("x")` → `"deterministic"`。
- `test_insights_authorization_change_is_deterministic_not_ownership`：`InsightsAuthorizationChanged("x")` → `"deterministic"`。
- `test_plan_budget_timeout_is_deterministic_but_plain_timeout_is_transient`：`AnswerBudgetExhausted("persisted answer budget exhausted")` → `"deterministic"`；`TimeoutError()` → `"transient"`；`GraphCheckpointRetryable()` → `"transient"`，`GraphCheckpointUnavailable()` → `"deterministic"`。
- `test_retry_delay_is_exponential_capped_and_jittered`：用 `random.Random(0)`，attempt 1..6 的延迟分别落在 `[1, 2]`、`[2, 4]`、`[4, 8]`、`[8, 16]`、`[15, 30]`、`[15, 30]` 秒内；`retry_delay(0)` 抛 `ValueError`。
- `test_gateway_rejection_is_deterministic_through_adapter`：给 `knowledge/adapters/litellm.py` 中生成回答的适配器方法注入一个抛 `ModelGatewayRejected` 的假网关（沿用 `tests/unit/knowledge/` 中该适配器既有测试的构造方式），捕获其抛出的异常 `e`，断言 `isinstance(e, AnswerRejected)` 且 `classify_turn_failure(e) == "deterministic"`；把假网关换成抛 `ModelGatewayUnavailable` 时断言 `classify_turn_failure(e) == "transient"`。
- `test_expired_plan_deadline_raises_answer_budget_exhausted`：`KnowledgeHttpService._plan_deadline(replace(plan, deadline_at=time.time() - 1))` 抛 `AnswerBudgetExhausted`，且 `isinstance(..., TimeoutError)`。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/entrypoints/test_turn_failures.py -q`
Expected: FAIL（`ModuleNotFoundError: tap.entrypoints.tapper_turn_failures`）

- [ ] **Step 3: 实现**

- `errors.py` 新增两个子类；`litellm.py` 把每处 `except (ModelGatewayRejected, ModelGatewayUnavailable)` 拆成两段：`except (ModelGatewayRejected, ModelOutputTruncated)` 抛对应的 `AnswerRejected("model-rejected")` / `ModelRejected("model-rejected")`（原处抛 `AnswerUnavailable` 的改抛 `AnswerRejected`，原处抛 `ModelUnavailable` 的改抛 `ModelRejected`），其余仍抛原异常与原消息。注意 `ModelOutputTruncated` 是 `ModelGatewayUnavailable` 的子类，须写在前面。
- `_plan_deadline` 改抛 `AnswerBudgetExhausted`。
- `tapper_turn_failures.py` 按上表用 `isinstance` 顺序匹配；`retry_delay` 用 `rng or random` 的 `uniform(0.5, 1.0)`。

- [ ] **Step 4: 运行通过并跑相关旧测试**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/entrypoints/test_turn_failures.py apps/tap-ai-backend/tests/unit/knowledge -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/entrypoints/tapper_turn_failures.py apps/tap-ai-backend/src/tap/modules/knowledge/ports/errors.py apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py apps/tap-ai-backend/tests/unit/entrypoints/test_turn_failures.py
git commit -m "feat: classify turn failures as transient, deterministic or ownership"
```

---

### Task 2: `next_attempt_at` 迁移、`defer_turn` 与领取上限

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0034_turn_retry.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/adapters/mysql.py:51-64`（`chat_turn` 表定义）
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/adapters/mysql_conversations.py`（构造函数 `:235`、`claim_queued` `:1117`、`_fail_integrity_turn` `:1314`，新增 `defer_turn`）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py`（`TapperSettings` 字段与解析、`:1953` 仓储构造）
- Modify: `.env.example`（`TAPPER_JOB_BATCH_SIZE` 下一行）
- Test: `apps/tap-ai-backend/tests/integration/test_turn_retry_mysql.py`
- Test: `apps/tap-ai-backend/tests/unit/entrypoints/test_tapper_runtime.py`（追加）

**Interfaces:**
- Produces:
  - `chat_turn` 新列：`next_attempt_at DATETIME(6) NULL`、`last_failure VARCHAR(128) NULL`（迁移 `revision = "0034_turn_retry"`，`down_revision = "0033_langfuse_sync"`，`downgrade` 删除两列）。
  - `MysqlConversationRepository(sessions, *, scope, max_turn_attempts: int = 3)`；`max_turn_attempts` 不在 `1..10` 抛 `ValueError`。
  - `async def defer_turn(self, conversation_id: str, turn_id: str, lease_token: str, *, next_attempt_at: datetime, failure: str) -> None`：在一个事务内，仅当 `state == "running"` 且 `processing_lease_token == lease_token` 时，把 `processing_lease_expires_at` 设为当前时间、`next_attempt_at` 设为参数值、`last_failure` 设为 `failure[:128]`；同事务把 `graph_run`（`run_id == turn_id` 且 `lease_token == lease_token`）的 `lease_until` 设为当前时间。turn 行未命中抛 `ConversationConflict("generation lease lost")`。
  - `claim_queued`：候选查询与 `FOR UPDATE` 复检都加 `next_attempt_at IS NULL OR next_attempt_at <= now`；成功领取时把 `next_attempt_at` 置空。复检通过后、写 `turn.started` 之前，若 `row["processing_attempt"] >= self.max_turn_attempts`，则走终结路径写 `turn.failed`（problem `answer-unavailable`）并跳过该 Turn，不计入返回值。
  - `_fail_integrity_turn` 泛化为 `_fail_unclaimable_turn(session, row, has_graph: bool, now: datetime, *, problem_type: str, reason: str) -> None`，原调用传 `problem_type="conversation-integrity", reason="input-snapshot-missing"`；上限路径传 `problem_type="answer-unavailable", reason="attempts-exhausted"`。日志 `extra` 同时带 `last_failure`（列值，空则 `"lease-expired"`）。上限路径另开 `span("turn.attempts_exhausted", {"tap.turn_id": ..., "tap.failure.last": ...})`。
  - `TapperSettings.turn_max_attempts: int`，`_integer(values, "TAPPER_TURN_MAX_ATTEMPTS", 3, minimum=1, maximum=10)`；`tapper_runtime.py` 构造仓储时传入。

- [ ] **Step 1: 写失败测试**

`test_turn_retry_mysql.py`（沿用 `tests/integration/test_graph_run_recovery.py` 的 MySQL 夹具与建 Turn 方式）：
- `test_deferred_turn_is_not_claimed_before_next_attempt_at`：领取一个 Turn，`defer_turn(..., next_attempt_at=now + 1h, failure="transient:ModelGatewayUnavailable")`；随即 `claim_queued(limit=10)` 返回空；把该行 `next_attempt_at` 改为过去时间后再次领取，返回该 Turn，`attempt == 2`，库中 `next_attempt_at IS NULL`、`last_failure` 保留原值。
- `test_defer_requires_the_current_lease_token`：用错误 token 调 `defer_turn` 抛 `ConversationConflict`，行不变。
- `test_defer_expires_the_graph_run_lease`：Turn 已有 `graph_run`（先跑一次图写入 checkpoint，或按现有测试的方式插入 RUNNING 行），`defer_turn` 后 `graph_run.lease_until <= now`，到期后可被领取。
- `test_claim_fails_turn_whose_attempts_are_exhausted`：仓储 `max_turn_attempts=3`；把一个 running Turn 的 `processing_attempt` 设为 3、租约设为已过期；`claim_queued` 返回空，Turn 状态 `failed`，恰好新增一个 `turn.failed` 事件且 problem 类型为 `answer-unavailable`；再调一次 `claim_queued` 不新增事件。
- `test_canceled_deferred_turn_is_not_reclaimed`：领取后 `defer_turn`，再按现有取消路径（`ConversationService` 的取消方法，见 `test_expired_graph_lease_reclaim_and_cancel_have_one_terminal_winner`）取消 Turn；把 `next_attempt_at` 改到过去后 `claim_queued` 返回空。

`test_tapper_runtime.py` 追加：
- `test_turn_max_attempts_defaults_and_bounds`：缺省为 3；`"0"`、`"11"` 被拒绝（沿用该文件对 `TAPPER_JOB_BATCH_SIZE` 越界的断言方式）。

- [ ] **Step 2: 运行确认失败**

Run: `TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_turn_retry_mysql.py -q` 与 `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/entrypoints/test_tapper_runtime.py -q`
Expected: FAIL（`defer_turn` 不存在、列不存在、设置字段不存在）

- [ ] **Step 3: 实现迁移、表定义、仓储方法与设置**

迁移风格参照 `0033_langfuse_sync.py`；`.env.example` 增加 `TAPPER_TURN_MAX_ATTEMPTS=3`。

- [ ] **Step 4: 运行通过，并跑既有恢复与模式漂移测试**

Run: `TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_turn_retry_mysql.py apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py apps/tap-ai-backend/tests/integration/test_schema_drift.py apps/tap-ai-backend/tests/integration/test_conversation_persistence.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/migrations/versions/0034_turn_retry.py apps/tap-ai-backend/src/tap/modules/chat/adapters/mysql.py apps/tap-ai-backend/src/tap/modules/chat/adapters/mysql_conversations.py apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py .env.example apps/tap-ai-backend/tests/integration/test_turn_retry_mysql.py apps/tap-ai-backend/tests/unit/entrypoints/test_tapper_runtime.py
git commit -m "feat: defer turns with next_attempt_at and cap claim attempts"
```

---

### Task 3: 图对瞬时异常放行，新 attempt 重设 plan 截止时间

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/application/chat_graph.py`（`ChatGraph.__init__`、`_wrap`）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_chat_turn.py`（`ChatTurnNodes.__init__`、`_active_plan`、`guard_in`）
- Test: `apps/tap-ai-backend/tests/unit/ai/test_chat_graph.py`（追加）
- Test: `apps/tap-ai-backend/tests/unit/entrypoints/test_chat_turn_graph.py`（追加）

**Interfaces:**
- Consumes: `classify_turn_failure`（Task 1，由 worker 注入，本任务不直接引用）。
- Produces:
  - `ChatGraph(*, checkpointer, nodes, authorize, propagate=(), retryable: Callable[[BaseException], bool] = lambda _error: False)`。`_wrap` 中在 `except self._propagate` 之后、捕获为 `failure` 之前：`if self._retryable(exc): 标记 span ERROR 并 raise`。finalize（`capture_failure=False`）行为不变。
  - `ChatTurnNodes(..., attempt_started_at: float | None = None)`：`guard_in` 写出 plan 时同时返回 `"plan_attempt": turn.attempt`；`_active_plan` 在 `state.get("plan_attempt") != turn.attempt` 且 `attempt_started_at` 非空时返回 `dataclasses.replace(plan, deadline_at=max(plan.deadline_at, attempt_started_at + plan.remaining_seconds))`。`ChatGraphState` 新增可选键 `plan_attempt: int`（`total=False`，不升 schema 版本：旧 checkpoint 无此键即视为跨 attempt）。

- [ ] **Step 1: 写失败测试**

`test_chat_graph.py` 追加：
- `test_retryable_node_error_propagates_and_resume_skips_completed_nodes`：用 `InMemorySaver` 与计数替身节点；`model` 第一次抛 `ConnectionError`，`retryable=lambda e: isinstance(e, ConnectionError)`；`start` 抛 `ConnectionError`，状态中无 `failure`；随后 `resume` 完成，`seed_search` 只调用 1 次、`model` 调用 2 次、`finalize` 调用 1 次。
- `test_non_retryable_node_error_still_routes_to_finalize`：同上但 `retryable` 返回 False，`start` 正常返回且 `state["failure"] == {"node": "model", "error": "ConnectionError"}`。

`test_chat_turn_graph.py` 追加：
- `test_new_attempt_rebases_plan_deadline`：构造 `deadline_at = time.time() - 60`、`remaining_seconds = 30` 的 plan 字典放入 state，`state["plan_attempt"] = 1`，`turn.attempt = 2`，`attempt_started_at = time.time()`；`_active_plan(state).deadline_at >= attempt_started_at + 30 - 0.01`。
- `test_same_attempt_keeps_persisted_plan_deadline`：同上但 `turn.attempt = 1`，返回的 `deadline_at` 等于原值。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_chat_graph.py apps/tap-ai-backend/tests/unit/entrypoints/test_chat_turn_graph.py -q`
Expected: FAIL（`ChatGraph() got an unexpected keyword argument 'retryable'` 等）

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行通过**

Run: 同 Step 2
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/modules/ai/application/chat_graph.py apps/tap-ai-backend/src/tap/entrypoints/tapper_chat_turn.py apps/tap-ai-backend/tests/unit/ai/test_chat_graph.py apps/tap-ai-backend/tests/unit/entrypoints/test_chat_turn_graph.py
git commit -m "feat: let transient node failures escape the chat graph for resume"
```

---

### Task 4: worker 按分类处理失败

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py`（`GenerationWorker` 字段与校验、`run_once`、`_process_claimed_turn` 的 except 链、`main` 构造）
- Modify: `apps/tap-ai-backend/tests/unit/chat/test_turn_processor.py`（重命名参数、更新被新语义改变的断言）
- Test: `apps/tap-ai-backend/tests/unit/chat/test_turn_processor.py`（追加）

**Interfaces:**
- Consumes: `classify_turn_failure`、`retry_delay`（Task 1）；`repository.defer_turn`（Task 2）；`ChatGraph(retryable=...)`、`ChatTurnNodes(attempt_started_at=...)`（Task 3）。
- Produces:
  - `GenerationWorker` 字段 `max_checkpoint_attempts` 更名为 `max_turn_attempts: int = 3`（校验 `1..10`），新增 `rng: random.Random | None = None`（仅测试注入）。
  - `ChatGraph` 构造传 `retryable=lambda e: classify_turn_failure(e) == "transient"`；`propagate` 保留 `(ConversationConflict, ConversationNotFound)`（`GraphCheckpointRetryable` 改由 `retryable` 放行，`GraphCheckpointUnavailable` 归确定性，不再列入 `propagate`）。
  - `_process_claimed_turn` 中 `run_graph` 之后的所有异常（含图外的 Insights 复检、`authorize_completed_answer`、`complete_evidence`）统一进入一个 `except Exception as error` 分支，按 `classify_turn_failure(error)`：
    - `ownership`：返回，不写任何东西；`tap.outcome = "lost"`。
    - `deterministic`：`fail_turn()`（其本身抛 `ConversationConflict` / `PermissionError` 时静默返回）；`tap.outcome = "failed"`。
    - `transient`：若 `turn.attempt >= self.max_turn_attempts` 按确定性处理；否则 `repository.defer_turn(conversation_id, turn.turn_id, turn.lease_token, next_attempt_at=now + retry_delay(turn.attempt, rng=self.rng), failure=f"transient:{type(error).__name__}")`（其抛 `ConversationConflict` 时静默返回）；`tap.outcome = "deferred"`。
    - 三种情况都在 `turn_span` 上设 `tap.failure.class` 与 `tap.failure.error`（异常类型名）。
  - `run_once`：每个 Turn 的处理包在 `try/except Exception` 中，异常以 `_LOGGER.exception` 记录后继续下一个 Turn；`flush_traces()` 仍在 `finally`。

- [ ] **Step 1: 写失败测试**

追加到 `test_turn_processor.py`（沿用该文件的假仓储 / 假知识服务写法；假仓储新增记录调用的 `defer_turn`）：
- `test_transient_model_failure_defers_with_backoff_and_no_terminal_event`：`attempt=1`，生成步骤抛 `ModelGatewayUnavailable`；断言 `defer_turn` 被调用一次，`failure == "transient:ModelGatewayUnavailable"`，`next_attempt_at - now` 在 `[1s, 2s]` 内（`rng=random.Random(0)`），`complete_evidence` 未被调用，span `tap.outcome == "deferred"`。
- `test_transient_failure_on_last_attempt_fails_turn`：`attempt=3`，同样失败；`complete_evidence` 以 `turn.failed`（`answer-unavailable`）调用一次，`defer_turn` 未调用。
- `test_deterministic_failure_fails_turn_immediately`：`attempt=1`，生成步骤抛 `AnswerRejected("model-rejected")`；`turn.failed` 写入一次，`defer_turn` 未调用。
- `test_ownership_loss_writes_nothing`：生成步骤抛 `ConversationNotFound`；`complete_evidence` 与 `defer_turn` 都未调用。
- `test_transient_failure_after_graph_is_deferred`：图成功但 `complete_evidence` 第一次抛 `sqlalchemy.exc.OperationalError("x", None, Exception())`；断言 `defer_turn` 被调用，worker 不抛异常。
- `test_one_turn_crash_does_not_skip_the_rest_of_the_batch`：`claim_queued` 返回两个 Turn；第一个确定性失败且其 `fail_turn` 内 `complete_evidence` 抛 `RuntimeError`；第二个正常完成；`run_once` 返回 2 且第二个 Turn 的 `complete_evidence` 被调用。

更新既有测试：`max_checkpoint_attempts=` 改为 `max_turn_attempts=`；`test_generation_worker_bounds_checkpoint_settlement_conflict_at_attempt_budget` 与 `test_generation_worker_cannot_fail_a_checkpoint_conflict_after_losing_lease` 改为断言 `ConversationConflict` 属所有权丢失、不写 `turn.failed`（上限改由领取路径执行，见 Task 2）；`GraphCheckpointRetryable` 相关两条测试改为断言 `defer_turn` 被调用 / 达上限时 `turn.failed`。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/chat/test_turn_processor.py -q`
Expected: FAIL（`max_turn_attempts` 未知、`defer_turn` 未调用）

- [ ] **Step 3: 实现**

`main()` 构造 worker 时传 `max_turn_attempts=settings.turn_max_attempts`。

- [ ] **Step 4: 运行通过并跑 worker 相关测试**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/chat apps/tap-ai-backend/tests/unit/entrypoints apps/tap-ai-backend/tests/contract/test_worker_stream_events.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py apps/tap-ai-backend/tests/unit/chat/test_turn_processor.py
git commit -m "feat: defer transient turn failures and isolate turns within a batch"
```

---

### Task 5: worker 主循环存活

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py`（`main` 中的 `while` 循环抽出）
- Test: `apps/tap-ai-backend/tests/unit/entrypoints/test_generation_worker_loop.py`

**Interfaces:**
- Produces: `async def run_worker_loop(worker: GenerationWorker, stop: asyncio.Event, *, batch_size: int, poll_seconds: float, wait: Callable[[asyncio.Event, float], Awaitable[None]] = _wait_or_stop) -> None`。`_wait_or_stop(stop, timeout)` 等待 `stop` 或超时后返回（不抛 `TimeoutError`）。循环：`processed = await worker.run_once(limit=batch_size)`；异常时 `_LOGGER.exception`、开 `span("worker.loop_error", {"tap.error": type名})`、`await wait(stop, backoff)` 后 `backoff = min(30.0, backoff * 2)`；成功时 `backoff = 1.0`，`processed == 0` 时 `await wait(stop, poll_seconds)`；`stop.is_set()` 时退出。`main()` 改为调用它。

- [ ] **Step 1: 写失败测试**

`test_generation_worker_loop.py`（假 worker 只实现 `run_once`；`wait` 替身记录 timeout 并在需要时 `stop.set()`）：
- `test_loop_survives_run_once_errors_with_doubling_backoff`：`run_once` 依次抛 `RuntimeError`、`RuntimeError`、`RuntimeError`，然后返回 1，然后返回 0；第 2 次 `poll_seconds` 等待时 `stop.set()`；记录到的 timeout 序列为 `[1.0, 2.0, 4.0, poll_seconds]`。
- `test_backoff_resets_after_success_and_caps_at_30`：连抛 7 次后成功一次再抛一次；timeout 序列为 `[1, 2, 4, 8, 16, 30, 30, 1]`（成功且 `processed > 0` 时不等待）。
- `test_loop_exits_when_stop_is_set`：`stop` 预先 set，`run_once` 不被调用。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/entrypoints/test_generation_worker_loop.py -q`
Expected: FAIL（`ImportError: run_worker_loop`）

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行通过**

Run: 同 Step 2
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py apps/tap-ai-backend/tests/unit/entrypoints/test_generation_worker_loop.py
git commit -m "feat: keep the generation worker loop alive across run errors"
```

---

### Task 6: MySQL 端到端续跑验证与文档

**Files:**
- Test: `apps/tap-ai-backend/tests/integration/test_turn_retry_mysql.py`（追加）
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 6 中“瞬时失败退避后从断点续跑；attempt 有上限；worker 单次异常不退出”对应勾选项）
- Modify: `docs/superpowers/plans/2026-10-01-product-roadmap.md`（子项目 7 行：PR 2 完成）
- Modify: `docs/architecture.md`（仅当其中描述了 Turn 领取 / 失败处理时，补一句瞬时失败退避续跑与 attempt 上限）

**Interfaces:**
- Consumes: Task 1–5 全部。

- [ ] **Step 1: 写集成测试**

追加到 `test_turn_retry_mysql.py`（真实 `MysqlConversationRepository` + MySQL checkpointer + `GenerationWorker`，知识服务用计数替身，沿用 `test_graph_run_recovery.py::test_reclaim_takes_over_completed_checkpoint_lease_without_repeating_provider` 的组装方式）：
- `test_model_failure_resumes_from_checkpoint_without_repeating_retrieval`：生成步骤第一次抛 `ModelGatewayUnavailable`、第二次成功；第一次 `run_once` 后 Turn 仍为 `running` 且 `next_attempt_at > now`，无终止事件；把 `next_attempt_at` 改到过去后第二次 `run_once` 完成 Turn：`prepare` 只调用 1 次、`generate` 调用 2 次，`processing_attempt == 2`，恰好一个 `turn.completed`。
- `test_three_transient_failures_fail_the_turn_once`：生成步骤始终抛 `ModelGatewayUnavailable`，`max_turn_attempts=3`；每次 `run_once` 前把 `next_attempt_at` 改到过去，共 3 次；Turn 最终 `failed`，恰好一个 `turn.failed`，`prepare` 只调用 1 次；第 4 次 `run_once` 返回 0。

- [ ] **Step 2: 运行**

Run: `TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_turn_retry_mysql.py -q`
Expected: PASS（若失败，按 systematic-debugging 定位到对应任务修正后再继续）

- [ ] **Step 3: 全量验证**

Run（仓库根目录）：`make check`；`make test`（对照交接文档的 34 个既有失败：`test_document_ledger.py` 21、`test_document_upload_recovery.py` 7、`test_ingestion_recovery.py` 6，不得新增失败）；`TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py apps/tap-ai-backend/tests/integration/test_turn_retry_mysql.py apps/tap-ai-backend/tests/integration/test_conversation_persistence.py apps/tap-ai-backend/tests/integration/test_insights_generation_persistence.py apps/tap-ai-backend/tests/integration/test_schema_drift.py -q`
Expected: 全部通过（除上述既有失败）；`make contracts` 后 `git status` 无契约差异。

- [ ] **Step 4: 更新文档并提交**

```bash
git add apps/tap-ai-backend/tests/integration/test_turn_retry_mysql.py docs/superpowers/plans/2026-09-29-v1-roadmap.md docs/superpowers/plans/2026-10-01-product-roadmap.md docs/architecture.md
git commit -m "test: prove transient turn failures resume from the last checkpoint"
```
