# Agent 团队视图实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Agents 页增加"我的 Agent / 团队 / 汇报"三个视角，把每个 Agent 当作团队成员展示人事档案、Langfuse 运行数据、满意度与维护履历，并提供可打印报告页与 CSV 导出。

**Architecture:** 新增后端模块 `agent_team`：运行数据经端口 `AgentRuntimeMetrics` 查询 Langfuse Metrics API v1（生产）或 MySQL Turn 表（仅 `TAP_DEMO_MODE=e2e`），外包 5 分钟缓存；人事字段、维护事件、满意度与待处理反馈来自 MySQL，由应用服务合并为汇总、花名册、档案、人员、架构图与报告。Langfuse 同步为 Turn trace 增加 `agent:` 标签；维护事件在能力 4 的写操作与本模块写接口的同一事务内追加。前端在现有 `AgentsWorkspace` 外加四标签外壳，"配置"即现有列表，其余视图用手写 SVG 图表，不新增依赖。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async + Alembic（MySQL）、httpx（已是后端依赖）、Langfuse 3.225.11 Metrics API v1、React 19 / TypeScript、TanStack Query、antd 6（仅表格）、Vitest、Playwright。

**Spec:** [Agent 团队视图设计](../specs/2026-10-22-agent-team-view-design.md)

## Global Constraints

- 路径相对仓库根；后端源码根 `apps/tap-ai-backend/src/tap` 简写 `B/`，后端测试根 `apps/tap-ai-backend/tests` 简写 `BT/`；前端源码根 `apps/tap-ai-frontend/src` 简写 `F/`。
- 迁移 `0036_agent_team`，`down_revision = "0035_skills_agents_authoring"`。
- `agentKey`：自定义与内置 Agent 为其 `agent_id`；未选 Agent 的 Turn 为 `tapper_default`（常量 `TAPPER_DEFAULT_AGENT_KEY`）。Langfuse 标签 `agent:<agentKey>`。
- 维护动作 `kind` 闭集：`revised`、`skill_upgraded`、`disabled`、`enabled`、`feedback_resolved`、`owner_transferred`。不记录"创建"。
- 字段上限：`job_title` ≤ 64 字符；`note`、`resolution_note` ≤ 500 字符。
- 周期：`from`、`to` 为闭区间日期；时区按 spec"未配置时用 UTC"——代码库目前没有项目时区设置，故恒为 UTC，响应带 `timezone: "UTC"`；日后加项目设置只改 `project_timezone()` 一处。
- 粒度：天数 `n = (to - from).days + 1`；`n ≤ 92` 按 `day`，`n ≤ 731` 按 `week`，否则 `month`。
- 满意度：`up / (up + down)`，按 Turn 创建时间落入周期；有评分轮次 `< 10` 时 `satisfaction = null` 且 `satisfactionSample = "insufficient"`。
- 状态（spec 1.4，与所选周期无关）：`disabled`（已停用）> `active`（截至今天的近 30 天运行 ≥ 1）> `idle`。汇总"活跃数"另按所选周期计（周期内运行 ≥ 1）。
- 运行与失败（spec 2.2）：只有根 span 为 `turn.request` 的 trace 打 `agent:` 与 `outcome:<state>` 标签；`failures` = `outcome:failed`；`abstained`、`canceled` 计入运行、单列次要指标。
- 上一周期（spec 6.4）：按形状判定——完整自然月 → 再上一个自然月；同月 1 日至非月末 → 上月 1 日至同日（无该日取月末）；其他 → 紧邻等长区间。
- 人员范围（spec 2.5）：全部当前负责人 ∪ 周期内调用过 Agent 的人 ∪ 周期内做过维护的人；显示 `actor_id`。
- 只缓存 Langfuse 运行数据；MySQL 部分每次实时查询。`runtimeStatus`：`ok`、`syncing`（`langfuse_sync_cursor` 中 `traces` 落后 `max(trace_span.recorded_at)` 超过 5 分钟）、`unavailable`。
- Langfuse 查询按 `environment = TAP_LANGFUSE_ENVIRONMENT` 过滤。
- 汇总 Agent 总数只计启用中的自定义与内置 Agent，不含 `tapper_default`。
- 待处理反馈：`(rating = 'down' OR comment IS NOT NULL) AND resolved_at IS NULL`；用户改评分或评论时清空 `resolved_*`，重新变为待处理。
- Langfuse 查询：10 秒超时；`config.row_limit = 1000`，返回满 1 000 行时按日期二分重查并合并；缓存键（项目、`from`、`to`、查询类型），TTL 300 秒。
- 错误沿用 `B/interfaces/http/problems.py` 的 `{code, detail}` 简单体：`period_invalid` 400、`actor_not_found` 400、`agent_read_only` 403、`agent_not_found` 404、`feedback_not_found` 404。Langfuse 失败不返回 5xx，返回 `runtimeStatus: "unavailable"`。
- 新权限 `ai.agent_team.read`（读接口）；三个写接口用 `ai.agents.write`。
- "最近运行"取自 Langfuse：Metrics API v1 没有时间戳度量（2026-10-23 本地 3.225.11 实测，traces 视图度量仅 `count, observationsCount, scoresCount, uniqueUserIds, uniqueSessionIds, latency, totalTokens, totalCost`），改用 Traces 列表 API `GET /api/public/traces?tags=project:<id>&tags=agent:<key>&orderBy=timestamp.desc&limit=1`，每个 Agent 一次，随其他查询一起缓存。
- Metrics API v1 实测行为（3.225.11）：`tags` 维度返回整组标签数组而非逐个展开；observations 视图用 `tags` 维度即 trace 标签；`timeDimension` 会补齐空桶（`tags: []`、`count_count: 0`），空桶也计入 `row_limit`；`latency` 单位毫秒；`week` 桶以周一开始；`parentObservationId` 过滤用 `{"operator": "is null", "type": "null"}`。
- `skill_upgraded` 判定：能力 4 没有自动升级路径，修订时若与上一修订相比只有 `skill_revision_ids` 变化且每个变化都是同一 Skill 的新修订，记 `skill_upgraded`，否则 `revised`。
- 前端不新增依赖；图表为手写 SVG，颜色取 `F/` 现有 CSS 变量；页签沿用 `AgentDetailDrawer.tsx` 的 `role="tablist"` 写法；抽屉用 `AccessibleDialog`；表格用 antd `Table`。
- 产品界面不放演示开关或实现说明；用 `Tapper`，不写客户企业名称。
- 仓库规则见 `AGENTS.md`：TDD、小写 Conventional Commit。子代理禁止 `make demo-up/down/reset`；隔离 MySQL 集成测试设 `TAP_RUN_MYSQL_INTEGRATION=1`，一次一个文件。

## Review Focus

1. 停用后又被调用过的 Agent（历史对话"重新生成"仍冻结旧修订）：花名册状态应为"已停用"，但周期内运行数照常计入。→ Task 7 `test_disabled_agent_with_runs_stays_disabled`。
2. 修订已被删除或对话冻结的 `agent_revision_id` 在 `ai_agent_revision` 中找不到（数据不一致）：同步不应失败，应标为 `agent:tapper_default` 之外的 `agent:unknown`，并且团队视图不出现幽灵成员。→ Task 3 `test_unknown_revision_tags_unknown`，Task 7 `test_unknown_agent_key_is_dropped_from_roster`。
3. 自定义周期跨度 3 年、运行量大：汇总数字必须是整周期精确合计，而趋势按月；分段查询合并后不重复计数。→ Task 4 `test_split_on_row_limit_merges_without_double_count`，Task 7 `test_summary_totals_ignore_trend_granularity`。
4. 同一条反馈连点两次"标记已处理"（双击或两个标签页）：第二次返回首次结果，只产生一条 `feedback_resolved` 事件。→ Task 6 `test_resolve_twice_returns_first_resolution`。
5. 负责人转交给自己（当前负责人）：不应产生事件也不报错，返回当前档案。→ Task 7 `test_transfer_to_current_owner_is_noop`。

---

## 文件结构

后端新模块 `B/modules/agent_team/`：

| 文件 | 职责 |
| --- | --- |
| `domain/models.py` | 常量、`Period`、粒度、维护事件与各读模型 dataclass |
| `domain/metrics.py` | 纯函数：状态、满意度、汇总、人员、架构图、报告对比 |
| `ports.py` | `AgentRuntimeMetrics`、`AgentTeamStore` 协议与 `RuntimeMetricsUnavailable` |
| `adapters/langfuse_metrics.py` | Metrics API v1 查询拼装、解析、分段 |
| `adapters/turn_table_metrics.py` | 仅 e2e：从 MySQL Turn 计算运行数据 |
| `adapters/cached_metrics.py` | 5 分钟 TTL 装饰器 |
| `adapters/mysql.py` | 表定义（事件表、新列）、读模型查询、三个写操作 |
| `application/service.py` | `AgentTeamService`：合并运行数据与 MySQL 数据 |
| `application/export.py` | CSV zip |
| `B/interfaces/http/routes/agent_team.py` | 12 个接口 |

前端新目录 `F/widgets/tap/workspace/agents/team/`：`AgentsPage.tsx`（四标签外壳）、`TeamTab.tsx`、`RosterCards.tsx`、`RosterTable.tsx`、`PeopleTable.tsx`、`OrgChart.tsx`、`AgentProfileDrawer.tsx`、`MyAgentsTab.tsx`、`ReportTab.tsx`、`PeriodPicker.tsx`、`charts.tsx`、`team.css`；数据钩子 `F/features/knowledge/api/agentTeam.ts`。

---

### Task 1: 迁移与领域模型

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0036_agent_team.py`
- Create: `B/modules/agent_team/__init__.py`、`B/modules/agent_team/domain/__init__.py`、`B/modules/agent_team/domain/models.py`
- Create: `B/modules/agent_team/adapters/__init__.py`、`B/modules/agent_team/adapters/mysql.py`（本任务只放表定义）
- Modify: `B/modules/ai/adapters/mysql.py`（`ai_agent` 表加三列）、`B/modules/chat/adapters/mysql_feedback.py:31`（`answer_feedback` 加三列）
- Test: `BT/unit/agent_team/test_models.py`、`BT/integration/test_agent_team_migration.py`

**Interfaces:**
- Produces（`domain/models.py`）：
  - `TAPPER_DEFAULT_AGENT_KEY = "tapper_default"`、`UNKNOWN_AGENT_KEY = "unknown"`
  - `MaintenanceKind = Literal["revised","skill_upgraded","disabled","enabled","feedback_resolved","owner_transferred"]`
  - `@dataclass(frozen=True, slots=True) MaintenanceEvent(event_id: str, project_id: str, agent_id: str, actor_id: str, kind: MaintenanceKind, revision_id: str | None, feedback_id: str | None, note: str | None, occurred_at: datetime)`；`new_event(scope, agent_id, kind, *, revision_id=None, feedback_id=None, note=None, now: datetime) -> MaintenanceEvent`（`event_id = f"mevt-{uuid4().hex[:16]}"`）
  - `@dataclass(frozen=True) Period(start: date, end: date)`；`Period.parse(from_: date, to: date) -> Period` 在 `from_ > to` 时抛 `PeriodInvalid`；`.days -> int`；`.granularity -> Literal["day","week","month"]`；`.previous() -> Period`（按 Global Constraints"上一周期"规则）；`.utc_bounds() -> tuple[datetime, datetime]`（`[start 00:00Z, end+1 00:00Z)`）；`trailing_30_days(today: date) -> Period`（模块函数，以 `today` 结尾）
  - `project_timezone(project_id: str) -> str` 恒返回 `"UTC"`
  - 表 `agent_maintenance_event`（`B/modules/agent_team/adapters/mysql.py`，用 `_scoped` 风格的项目作用域列），索引 `(project_id, agent_id, occurred_at)` 与 `(project_id, occurred_at)`

- [ ] **Step 1: 写失败的单元测试** `test_models.py`：

```python
def test_granularity_boundaries():
    d = date(2026, 1, 1)
    assert Period(d, d + timedelta(days=91)).granularity == "day"     # 92 天
    assert Period(d, d + timedelta(days=92)).granularity == "week"    # 93 天
    assert Period(d, d + timedelta(days=730)).granularity == "week"   # 731 天
    assert Period(d, d + timedelta(days=731)).granularity == "month"

def test_parse_rejects_reversed_period():
    with pytest.raises(PeriodInvalid):
        Period.parse(date(2026, 2, 2), date(2026, 2, 1))

def test_previous_of_full_month_is_previous_month():
    assert Period(date(2026, 9, 1), date(2026, 9, 30)).previous() == Period(date(2026, 8, 1), date(2026, 8, 31))

def test_previous_of_month_to_date_clamps_day():
    assert Period(date(2026, 3, 1), date(2026, 3, 30)).previous() == Period(date(2026, 2, 1), date(2026, 2, 28))

def test_previous_of_rolling_period_is_adjacent_equal_length():
    assert Period(date(2026, 10, 17), date(2026, 10, 23)).previous() == Period(date(2026, 10, 10), date(2026, 10, 16))

def test_utc_bounds_are_half_open_days():
    assert Period(date(2026, 9, 1), date(2026, 9, 1)).utc_bounds() == (
        datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC))
```

- [ ] **Step 2:** `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/agent_team/test_models.py -v`，预期 ImportError 失败。
- [ ] **Step 3:** 实现 `domain/models.py`。
- [ ] **Step 4:** 写集成测试 `test_agent_team_migration.py`（`owned_project_mysql`）：升级到 head 后 `ai_agent` 有 `job_title`（VARCHAR 64，可空）、`owner_actor_id`（非空）、`owner_assigned_at`（非空），且迁移前已存在的行 `owner_actor_id == actor_id`、`owner_assigned_at == created_at`；`answer_feedback` 有 `resolved_at`、`resolved_by`、`resolution_note`（VARCHAR 500）；`agent_maintenance_event` 存在；`langfuse_sync_cursor` 中 `name='traces'` 行被删除而 `name='feedback'` 行保留。
- [ ] **Step 5:** 写迁移：加列（`owner_actor_id` 先可空、`UPDATE ai_agent SET owner_actor_id = actor_id, owner_assigned_at = created_at`、再改非空），建事件表，`DELETE FROM langfuse_sync_cursor WHERE name = 'traces'`（让同步 worker 重推历史 trace 补标签）。同步更新两个 SQLAlchemy `Table` 定义。`downgrade` 反向删除（游标不恢复）。
- [ ] **Step 6:** `TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_agent_team_migration.py -v` 与 Step 1 单测均 PASS；另跑 `BT/integration/test_ai_asset_catalog.py` 确认能力 4 不回归。
- [ ] **Step 7:** 提交 `feat: add agent team schema and domain models`

### Task 2: 能力 4 写操作记录维护事件

**Files:**
- Modify: `B/modules/ai/adapters/mysql.py`（`insert_agent_revision` L908、`set_asset_enabled` L976、建 Agent 时写 owner）
- Modify: `B/modules/ai/application/agent_authoring.py`（`revise` L151、`set_enabled` L191）
- Create: `B/modules/agent_team/domain/classify.py`
- Test: `BT/unit/agent_team/test_classify.py`、`BT/unit/ai/test_agent_authoring.py`（追加）、`BT/integration/test_ai_asset_catalog.py`（追加）

**Interfaces:**
- Consumes: Task 1 `MaintenanceEvent`、`new_event`、`agent_maintenance_event` 表
- Produces:
  - `classify_revision(previous: AiAgentRevision, current: AiAgentRevision, skill_of: Mapping[str, str]) -> Literal["revised","skill_upgraded"]`；`skill_of` 把 skill_revision_id 映射到 skill_id
  - `MysqlAssetCatalog.insert_agent_revision(revision, *, base_revision_id, event: MaintenanceEvent | None = None)`、`set_asset_enabled(kind, asset_id, enabled, *, event: MaintenanceEvent | None = None)`：在同一 `self._sessions.begin()` 块内插入事件；首次插入 `ai_agent` 行时 `owner_actor_id = actor_id`、`owner_assigned_at = created_at`

- [ ] **Step 1: 写失败测试** `test_classify.py`：

```python
def test_only_newer_revisions_of_same_skills_is_skill_upgraded(): ...   # skill_revision_ids s1r1→s1r2，其他字段相同 → "skill_upgraded"
def test_prompt_change_with_skill_upgrade_is_revised(): ...             # 同时改 system_instruction → "revised"
def test_adding_a_skill_is_revised(): ...                               # 新增绑定 → "revised"
def test_model_change_is_revised(): ...                                 # default_model_alias 变化 → "revised"
```

在 `test_agent_authoring.py` 追加：`test_revise_passes_revised_event`、`test_revise_passes_skill_upgraded_event`、`test_disable_and_enable_pass_events`、`test_create_passes_no_event`（用现有假 catalog 记录收到的 `event` 参数）。
- [ ] **Step 2:** 跑这两个文件，预期失败。
- [ ] **Step 3:** 实现 `classify_revision`；在 `revise`/`set_enabled` 内构造事件传给 catalog（`skill_of` 通过 `self._catalog` 已有的 Skill 修订查询得到）；`set_enabled` 对已处于目标状态的 Agent 不传事件。
- [ ] **Step 4:** 集成测试追加 `test_revision_and_event_commit_together`（让事件插入抛错，断言修订也未写入）与 `test_new_agent_owner_defaults_to_creator`。
- [ ] **Step 5:** 单元与集成测试 PASS；`BT/contract/test_ai_agents_api.py` 不改即 PASS。
- [ ] **Step 6:** 提交 `feat: record agent maintenance events on authoring writes`

### Task 3: Langfuse 同步写入 `agent:` 标签与工具 span

**Files:**
- Modify: `B/modules/ai/domain/langfuse_sync.py:50`（`TraceBundle` 加 `agent_key: str | None`）
- Modify: `B/modules/ai/adapters/mysql_langfuse_sync.py:213`（`load_trace`）
- Modify: `B/modules/ai/application/langfuse_mapping.py:143`
- Modify: `B/entrypoints/tapper_chat_turn.py:969`（`tools` 节点中每个已准入调用的 `run_conversation_tool` 外包一层 span）
- Test: `BT/unit/ai/test_langfuse_mapping.py`（追加）、`BT/integration/test_langfuse_sync_store.py`（追加；若文件名不同，追加到现有 `load_trace` 集成测试）

**Interfaces:**
- Consumes: Task 1 `TAPPER_DEFAULT_AGENT_KEY`、`UNKNOWN_AGENT_KEY`
- Produces: `TraceBundle.agent_key` 与 `TraceBundle.turn_outcome: str | None`（`chat_turn.state` 为 `completed`/`abstained`/`failed`/`canceled` 时取该值，否则 `None`）。根 span 名不是 `turn.request` 时两者都为 `None`。`agent_key`：`turn_id is None` 时为 `None`；Turn 的 `turn_input_snapshot.snapshot` 中 `agent_revision_id` 为空时为 `"tapper_default"`；能在 `ai_agent_revision` 找到时为其 `agent_id`；找不到时为 `"unknown"`。`to_langfuse_trace` 的 `tags`：`("project:<id>",)`，有 `agent_key` 时追加 `"agent:<agent_key>"`，有 `turn_outcome` 时再追加 `"outcome:<turn_outcome>"`。
- Produces: 每次执行的工具调用产生一个名为 `tool.<tool_id>` 的 span（属性 `tap.tool.step_id`），被拒绝的调用不产生。现状只有每轮一个 `graph.node.tools` span（2026-10-23 本地 Langfuse 实测无 `tool.` 前缀 observation），一轮可含多个调用，无法按 spec 2.2 计"工具调用次数"；命名沿用已有的 `tool.insights.query`。

- [ ] **Step 1: 写失败测试**：`test_turn_trace_gets_agent_tag`、`test_default_turn_gets_tapper_default_tag`、`test_job_trace_has_no_agent_tag`、`test_terminal_turn_gets_outcome_tag`、`test_running_turn_has_no_outcome_tag`（mapping 单测）；`test_standalone_execute_root_gets_no_agent_tag`（集成：根 span 为 `turn.execute` 的 trace）；`test_each_admitted_tool_call_gets_a_tool_span`、`test_rejected_tool_call_has_no_tool_span`（`tapper_chat_turn` 的 tools 节点单测，追加到现有 tools 节点测试文件）；`test_load_trace_resolves_agent_id_from_snapshot`、`test_unknown_revision_tags_unknown`（集成，`owned_project_mysql`）。
- [ ] **Step 2:** 跑测试，预期失败。
- [ ] **Step 3:** 在 tools 节点用 `tap.platform.telemetry` 的 `span(f"tool.{tool_id}", {"tap.tool.step_id": this_step_id})` 包住 `run_conversation_tool`；`load_trace` 在已有 `chat_turn` 查询后再查 `turn_input_snapshot.snapshot`（Python 侧读 `agent_revision_id`，不写 JSON 路径 SQL），再查 `ai_agent_revision.agent_id`；mapping 追加标签。
- [ ] **Step 4:** 测试 PASS；`BT/unit/ai` 下 Langfuse 相关测试全部 PASS。
- [ ] **Step 5:** 提交 `feat: tag langfuse turn traces with agent key`

### Task 4: `AgentRuntimeMetrics` 端口与 Langfuse 适配器

**Files:**
- Create: `B/modules/agent_team/ports.py`、`B/modules/agent_team/adapters/langfuse_metrics.py`
- Test: `BT/unit/agent_team/test_langfuse_metrics.py`、`BT/smoke/test_langfuse_metrics_smoke.py`

**Interfaces:**
- Consumes: Task 1 `Period`
- Produces（`ports.py`）：
  - `class RuntimeMetricsUnavailable(Exception)`
  - `@dataclass(frozen=True, slots=True) ServedActor(actor_id: str, runs: int)`
  - `@dataclass(frozen=True, slots=True) AgentRuntimeStats(agent_key: str, runs: int, failures: int, abstained: int, canceled: int, latency_total_ms: int, latency_p50_ms: int | None, cost_usd: Decimal, served: tuple[ServedActor, ...], generation_calls: int, tool_calls: int)`；`served` 按 `runs` 降序、`actor_id` 升序
  - `@dataclass(frozen=True, slots=True) TrendPoint(bucket_start: date, agent_key: str, runs: int, failures: int, latency_total_ms: int, cost_usd: Decimal)`
  - `class AgentRuntimeMetrics(Protocol)`：`async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]`；`async def trend(self, project_id: str, period: Period, granularity: str) -> list[TrendPoint]`；`async def last_runs(self, project_id: str, agent_keys: Sequence[str]) -> dict[str, datetime]`（不限周期，返回该 Agent 最后一个 trace 的时间；无 trace 的键不出现）；失败抛 `RuntimeMetricsUnavailable`
  - `LangfuseRuntimeMetrics(client: httpx.AsyncClient, *, host: str, public_key: str, secret_key: str, environment: str, timeout_seconds: float = 10.0)`

查询拼装（`GET {host}/api/public/metrics?query=<json>`，Basic 认证）。均带过滤 `tags any of ["project:<id>"]`、`environment = <environment>`，`fromTimestamp`/`toTimestamp` 取 `period.utc_bounds()`，`config.row_limit = 1000`：

| 查询 | view | dimensions | metrics | 额外过滤 |
| --- | --- | --- | --- | --- |
| 运行、失败、拒答、取消、时长、成本 | `traces` | `tags` | `count/count`、`latency/sum`、`latency/p50`、`totalCost/sum` | —（同一 `agent:` 下按 `outcome:` 元素拆分计数；p50 取 `outcome` 各组按运行数加权） |
| 服务对象 | `traces` | `tags`、`userId` | `count/count` | — |
| 模型调用 | `observations` | 同上 | `count/count` | `type = GENERATION` |
| 工具调用 | `observations` | 同上 | `count/count` | `name starts with "tool."` |
| 趋势 | `traces` | `tags` | `count/count`、`latency/sum`、`totalCost/sum` | `timeDimension.granularity = <granularity>` |

以上字段与过滤写法均已在本地 3.225.11 实测可用（见 Global Constraints）。解析：每行 `tags` 数组中取 `agent:` 前缀的元素作为 `agent_key`，没有则丢弃该行（含补齐的空桶）；`null` 度量按 0 处理。最近运行用 Traces 列表 API（见 Global Constraints），其 `latency` 单位为秒，本任务不使用。

- [ ] **Step 1: 写失败单测**（`httpx.MockTransport`）：

```python
async def test_stats_query_groups_traces_by_tags(): ...         # 断言请求 query JSON 的 view/dimensions/metrics/filters 与上表一致
async def test_parses_agent_keys_and_ignores_project_tags(): ...
async def test_outcome_tags_split_failures_abstained_canceled(): ... # [project, agent:a, outcome:failed] 等行 → failures/abstained/canceled # 响应含 project: 与 agent: 两种标签行 → 只产出 agent 行
async def test_split_on_row_limit_merges_without_double_count(): # 首次返回 1000 行 → 按日期二分各查一次，合计等于两半之和
async def test_timeout_raises_unavailable(): ...                 # MockTransport 抛 httpx.ReadTimeout → RuntimeMetricsUnavailable
async def test_http_5xx_raises_unavailable(): ...
async def test_served_actors_sorted_by_runs_desc(): ...
async def test_tool_calls_zero_when_no_tool_spans(): ...
async def test_gap_filled_empty_buckets_are_ignored(): ...       # tags: [] 且 count 0 的行不产出 TrendPoint
async def test_last_runs_queries_traces_api_per_agent(): ...    # 断言 tags 两个参数、orderBy=timestamp.desc、limit=1
```

单天周期返回满 1 000 行无法再分时，按已得结果返回（服务对象截断），不报错：`test_single_day_at_row_limit_returns_partial`。
- [ ] **Step 2:** 跑单测，预期失败。
- [ ] **Step 3:** 实现 `ports.py` 与适配器；分段：按周期中点二分递归，每段独立查询后按 `agent_key`（与 `userId`）求和。p50 无法跨段合并：分段时 `latency_p50_ms` 取各段 p50 按运行数加权平均，并在函数文档注明。
- [ ] **Step 4:** 单测 PASS。
- [ ] **Step 5:** 写 smoke `test_langfuse_metrics_smoke.py`：`TAP_RUN_LANGFUSE_SMOKE != "1"` 时 `pytest.skip`；否则读 `LANGFUSE_*` 环境变量，对本地真实 Langfuse 断言 `agent_stats` 至少一个 `agent:` 键、`runs >= failures >= 0`，`last_runs` 对该键返回时间；用于发现 Langfuse 升级后的 API 变化。
- [ ] **Step 6:** 不设变量运行 smoke，预期 1 skipped。设变量的真实验证由主会话在 Task 10 后执行，子代理不做。
- [ ] **Step 7:** 提交 `feat: query agent runtime metrics from langfuse`

### Task 5: 缓存装饰器与 e2e 运行数据适配器

**Files:**
- Create: `B/modules/agent_team/adapters/cached_metrics.py`、`B/modules/agent_team/adapters/turn_table_metrics.py`
- Test: `BT/unit/agent_team/test_cached_metrics.py`、`BT/integration/test_turn_table_metrics.py`

**Interfaces:**
- Consumes: Task 4 端口
- Produces:
  - `CachedRuntimeMetrics(inner: AgentRuntimeMetrics, *, ttl_seconds: int = 300, clock: Callable[[], float] = time.monotonic)`，额外方法 `computed_at(project_id, period, kind) -> datetime | None`；只缓存成功结果，异常不缓存
  - `TurnTableRuntimeMetrics(sessions: async_sessionmaker[AsyncSession])`：从 `chat_turn` + `turn_input_snapshot` + `ai_agent_revision` 计算 `runs`、`failures`/`abstained`/`canceled`（按 `state`）、`latency_total_ms`（完成时间 − 创建时间）、`served`；`cost_usd = 0`、调用次数 0；`latency_p50_ms` 用 Python 计算；`last_runs` 取各 `agent_key` 最后一个 Turn 的创建时间

- [ ] **Step 1: 写失败测试**：`test_second_call_within_ttl_hits_cache`、`test_call_after_ttl_refetches`、`test_unavailable_is_not_cached`、`test_cache_key_includes_period_and_kind`；集成 `test_turn_table_counts_runs_per_agent_key`（造两个 Agent Turn 与一个默认 Turn）。
- [ ] **Step 2:** 跑测试，预期失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试 PASS。
- [ ] **Step 5:** 提交 `feat: cache agent runtime metrics and add turn table adapter`

### Task 6: `AgentTeamStore`（MySQL 读模型与写操作）

**Files:**
- Modify: `B/modules/agent_team/ports.py`（加 `AgentTeamStore` 协议）、`B/modules/agent_team/adapters/mysql.py`
- Modify: `B/modules/chat/adapters/mysql_feedback.py`（`upsert` 的 `on_duplicate_key_update`：`rating` 或 `comment` 与原值不同时把 `resolved_at`、`resolved_by`、`resolution_note` 置 NULL，用 SQL `IF` 在同一语句内完成）
- Test: `BT/integration/test_agent_team_store.py`

**Interfaces:**
- Consumes: Task 1 表与 `MaintenanceEvent`
- Produces（`ports.py`）：
  - `@dataclass(frozen=True, slots=True) AgentRecord(agent_id: str, name: str, job_title: str | None, owner_actor_id: str, owner_assigned_at: datetime, created_at: datetime, source_kind: str, enabled: bool, current_revision_id: str, current_revision_number: int)`
  - `@dataclass(frozen=True, slots=True) RatingCounts(agent_key: str, up: int, down: int)`
  - `@dataclass(frozen=True, slots=True) PendingFeedback(feedback_id: str, agent_key: str, turn_id: str, conversation_id: str, actor_id: str, rating: str | None, comment: str | None, updated_at: datetime)`
  - `@dataclass(frozen=True, slots=True) HistoryPage(events: tuple[MaintenanceEvent, ...], next_cursor: str | None)`
  - `class AgentTeamStore(Protocol)`：
    - `async def agents(self) -> list[AgentRecord]`
    - `async def agent(self, agent_id: str) -> AgentRecord | None`
    - `async def ratings(self, period: Period) -> list[RatingCounts]`（按 Turn 创建时间落在周期内，按 Turn 冻结 Agent 归属）
    - `async def pending_feedback(self, agent_key: str | None = None) -> list[PendingFeedback]`
    - `async def maintenance_counts(self, period: Period) -> list[tuple[str, str, MaintenanceKind, int]]`（agent_id、actor_id、kind、次数）
    - `async def history(self, agent_id: str, cursor: str | None, limit: int = 50) -> HistoryPage`（按 `occurred_at` 降序、`event_id` 次序；游标为不透明 base64）
    - `async def last_maintenance(self) -> dict[str, datetime]`（每个 `agent_id` 最近一条维护事件时间）
    - `async def active_actors(self, period: Period) -> set[str]`（周期内有 Turn 或维护事件的 actor，用于人员范围）
    - `async def sync_lag(self) -> timedelta | None`（`max(trace_span.recorded_at) - langfuse_sync_cursor['traces']`；无游标行时视为无限落后）
    - `async def actor_exists(self, actor_id: str) -> bool`（`actor_principal` 中同企业且启用）
    - `async def set_job_title(self, agent_id: str, job_title: str | None) -> AgentRecord`
    - `async def transfer_owner(self, agent_id: str, new_owner: str, event: MaintenanceEvent) -> AgentRecord`
    - `async def resolve_feedback(self, feedback_id: str, actor_id: str, note: str | None, event_factory: Callable[[str], MaintenanceEvent]) -> PendingFeedback`（返回解析后的记录；已处理则原样返回，不写事件）
  - `MysqlAgentTeamStore(sessions, scope: ProjectScopeContext)`；所有查询按 `scope.project_id` 过滤

- [ ] **Step 1: 写失败集成测试**（`owned_project_mysql`）：

```python
async def test_ratings_attribute_by_frozen_agent(): ...          # 按 Turn 创建时间落入周期          # 两个 Agent 各有赞踩 + 默认 Turn → 三组计数
async def test_pending_feedback_includes_downvotes_and_comments(): ... # 踩、带评论的赞进入；纯赞不进入；已处理不进入
async def test_resolve_twice_returns_first_resolution(): ...     # 第二次 note 不同，返回首次结果，事件只 1 条
async def test_resolve_writes_feedback_and_event_atomically(): ...
async def test_transfer_updates_owner_and_assigned_at(): ...
async def test_last_maintenance_per_agent(): ...
async def test_rerating_resolved_feedback_makes_it_pending(): ...   # 经 MysqlFeedbackRepository.upsert 改评分 → 重新出现在 pending_feedback
async def test_same_rating_resubmit_keeps_resolution(): ...
async def test_ratings_use_turn_created_time(): ...               # Turn 在周期内、反馈在周期后 → 计入
async def test_sync_lag(): ...
async def test_history_pages_with_cursor(): ...                  # 120 条事件，limit 50 → 3 页，无重复
async def test_queries_are_project_scoped(): ...                 # 另一项目的数据不出现
```
- [ ] **Step 2:** `TAP_RUN_MYSQL_INTEGRATION=1` 跑该文件，预期失败。
- [ ] **Step 3:** 实现。冻结 Agent 归属：`answer_feedback` join `turn_input_snapshot`，在 Python 侧读 `snapshot["agent_revision_id"]`，再按批查 `ai_agent_revision` 映射为 `agent_id`；无修订为 `tapper_default`；找不到为 `unknown`。
- [ ] **Step 4:** PASS。
- [ ] **Step 5:** 提交 `feat: add agent team mysql store`

### Task 7: `AgentTeamService` 与指标口径

**Files:**
- Create: `B/modules/agent_team/domain/metrics.py`、`B/modules/agent_team/application/__init__.py`、`B/modules/agent_team/application/service.py`
- Test: `BT/unit/agent_team/test_metrics.py`、`BT/unit/agent_team/test_service.py`

**Interfaces:**
- Consumes: Task 4、5、6 全部协议与 dataclass（最近运行用 `AgentRuntimeMetrics.last_runs`，不用 store）
- Produces（`domain/models.py` 追加读模型，字段名即 HTTP 字段的 snake_case）：
  - `RuntimeStatus = Literal["ok","syncing","unavailable"]`
  - `MemberRow(agent_key, name, kind: Literal["custom","built_in","tapper_default"], job_title, owner_actor_id: str | None, joined_at: datetime | None, status: Literal["active","idle","disabled"], runs, failures, abstained, canceled, latency_total_ms, latency_p50_ms, cost_usd, served_count, top_served: tuple[ServedActor, ...], last_run_at, satisfaction: float | None, satisfaction_sample: Literal["ok","insufficient"], last_maintenance_at, pending_feedback: int, maintenance: Mapping[MaintenanceKind, int], trend: tuple[TrendPoint, ...])`；`top_served` 前 3 位；`owner_actor_id` 对内置与默认为 `None`（前端显示"Tapper 内置"）
  - `TeamSummary(agent_total, active, idle, owners_avg: float, served_avg: float, runs, latency_total_ms, cost_usd, maintenance_total)`
  - `PersonRow(actor_id, owned_agents, owned_runs, served_by_agents, runs, maintenance)`
  - `OrgChart(people: tuple[str, ...], agents: tuple[str, ...], edges: tuple[tuple[str, str, Literal["owns","serves"]], ...])`
  - `TeamView[T](runtime_status: RuntimeStatus, computed_at: datetime, timezone: str, period: Period, granularity: str, data: T)`
  - `class AgentTeamService(store, metrics: CachedRuntimeMetrics, *, scope, clock)`：`summary(period)`、`members(period, *, owner=None, status=None, kind=None, sort="runs", order="desc")`、`member(agent_key, period)`、`history(agent_id, cursor)`、`people(period, sort="runs", order="desc")`、`person(actor_id, period)`（`actor_id == "me"` 解析为 `scope.actor_id`；返回负责的 Agent、为其服务的 Agent、待其处理的反馈）、`org_chart(period)`、`report(period)`（`current` 与 `previous` 两份 summary + members + people）、`update_job_title(agent_id, title)`、`transfer_owner(agent_id, new_owner)`、`resolve_feedback(feedback_id, note)`
  - 抛出 `AgentTeamRejected(code: str, detail: str)`，`code` 取 Global Constraints 闭集

- [ ] **Step 1: 写失败的纯函数测试** `test_metrics.py`：

```python
def test_status_precedence(): ...                           # disabled 优先于 active；启用且截至今天近 30 天 0 次 → idle
def test_disabled_agent_with_runs_stays_disabled(): ...
def test_status_ignores_selected_period(): ...              # 周期为上月有运行、截至今天近 30 天无 → idle；汇总活跃数仍计 1
def test_people_scope_is_owners_callers_and_maintainers(): ...
def test_satisfaction_below_10_ratings_is_insufficient(): ...  # 9 条 → None/insufficient；10 条 → 0.7
def test_summary_excludes_tapper_default_and_disabled(): ...
def test_summary_totals_ignore_trend_granularity(): ...    # 汇总来自 agent_stats，而非趋势点求和
def test_people_counts_owned_and_served(): ...
def test_org_chart_edges_owns_and_serves(): ...            # 内置与 tapper_default 无 owns 边
def test_unknown_agent_key_is_dropped_from_roster(): ...
```

- [ ] **Step 2:** `test_service.py`（用假 store 与假 metrics）：`test_unavailable_runtime_keeps_roster_and_satisfaction`（metrics 抛 `RuntimeMetricsUnavailable` → `runtime_status="unavailable"`，花名册、负责人、满意度仍在，运行字段为 0/None）、`test_transfer_to_current_owner_is_noop`、`test_transfer_to_unknown_actor_rejected`（`actor_not_found`）、`test_builtin_profile_edit_rejected`（`agent_read_only`）、`test_unknown_agent_rejected`（`agent_not_found`）、`test_job_title_over_64_rejected`、`test_me_resolves_to_scope_actor`、`test_report_includes_previous_period`、`test_sync_lag_over_5_minutes_is_syncing`、`test_writes_bypass_runtime_cache`（处理反馈后立即再查，待处理数已变，Langfuse 假对象调用次数不变）、`test_builtin_pending_feedback_not_in_anyones_queue`。
- [ ] **Step 3:** 跑两文件，预期失败。
- [ ] **Step 4:** 实现 `metrics.py` 纯函数与服务；成员集合 = `store.agents()` ∪（运行数据中出现的 `tapper_default`），丢弃 `unknown`。
- [ ] **Step 5:** PASS。
- [ ] **Step 6:** 提交 `feat: compute agent team views`

### Task 8: HTTP 接口、权限与契约

**Files:**
- Create: `B/interfaces/http/routes/agent_team.py`
- Modify: `B/interfaces/http/app.py:95-128`（注册路由，含 `validation_mode` 镜像）、`B/interfaces/http/dependencies.py:384` 附近（`agent_team_service(request)`）、`HttpServices`（新增字段）、`B/interfaces/http/problems.py:126`（五个错误码）、`B/modules/access/adapters/validation.py:36`（`("ai.agent_team.read", "ai")`）、`B/entrypoints/tapper_runtime.py`（`TapperSettings.agent_team_runtime_metrics: Literal["langfuse","turn_table"]`，读 `TAP_AGENT_TEAM_RUNTIME_METRICS`，默认 `langfuse`；`turn_table` 仅在 `TAP_DEMO_MODE=e2e` 时允许，否则启动报错；未配置 `LANGFUSE_HOST` 时生产适配器每次抛 `RuntimeMetricsUnavailable`）
- Regenerate: `contracts/`、`F/shared/api/generated/schema.ts`（`make contracts`）
- Test: `BT/contract/test_agent_team_api.py`

**Interfaces:**
- Consumes: Task 7 服务
- Produces: 12 个接口，路径、方法与 spec 第 5 节一致；前缀 `/api/v1/projects/{project_id}/ai/`。查询参数 `from`、`to`（ISO 日期，必填）；`members` 另有 `owner`、`status`、`kind`、`sort`、`order`；`history` 有 `cursor`。响应 camelCase，读接口外层统一 `{runtimeStatus, computedAt, timezone, period: {from, to}, granularity, data}`。写接口：`PUT agents/{agentId}/profile` 体 `{jobTitle: string | null}`；`POST agents/{agentId}/owner` 体 `{actorId}`；`POST feedback/{feedbackId}/resolve` 体 `{note?: string}`，均返回更新后的 `MemberRow` 或反馈记录。`operation_id` 前缀 `agent_team_`。`export` 由 Task 9 实现，本任务只注册返回 `application/zip` 的路由壳并在 Task 9 填充。

- [ ] **Step 1: 写失败契约测试**：每个接口一条成功用例；`test_reversed_period_returns_period_invalid`；`test_transfer_to_missing_actor_returns_actor_not_found`；`test_builtin_profile_returns_agent_read_only`；`test_reads_require_agent_team_read`；`test_writes_require_agents_write`；`test_langfuse_down_returns_200_unavailable`。
- [ ] **Step 2:** 跑 `BT/contract/test_agent_team_api.py`，预期失败。
- [ ] **Step 3:** 实现路由与装配；`make contracts` 重新生成。
- [ ] **Step 4:** 契约测试 PASS；`BT/contract/test_ai_agents_api.py` 不改即 PASS；`make contracts` 后 `git diff --exit-code` 只含预期生成物。
- [ ] **Step 5:** 提交 `feat: expose agent team api`

### Task 9: CSV 导出

**Files:**
- Create: `B/modules/agent_team/application/export.py`
- Modify: `B/interfaces/http/routes/agent_team.py`（`GET agent-team/export`）
- Test: `BT/unit/agent_team/test_export.py`、`BT/integration/test_agent_team_export.py`

**Interfaces:**
- Consumes: Task 7 `report`/`members`/`people` 与 `metrics.trend`
- Produces: `build_export(report, trend: list[TrendPoint], *, period: Period, runtime_status) -> bytes`；zip 内 `roster.csv`、`people.csv`、`trend.csv`，每个以 UTF-8 BOM 开头；`trend.csv` 粒度与 `period.granularity` 相同，首行为 `# granularity=<day|week|month>`；`runtime_status="unavailable"` 时运行列留空，首行另加 `# runtime=unavailable`。响应头 `Content-Disposition: attachment; filename="agent-team-<from>-<to>.zip"`。

- [ ] **Step 1: 写失败测试**：`test_zip_has_three_bom_csv_files`、`test_trend_granularity_matches_ui_rule`、`test_unavailable_runtime_leaves_columns_blank`、`test_chinese_names_round_trip`（集成：经 HTTP 下载解压后中文 Agent 名不乱码）。
- [ ] **Step 2:** 跑，预期失败。
- [ ] **Step 3:** 用标准库 `csv`、`zipfile` 实现。
- [ ] **Step 4:** PASS。
- [ ] **Step 5:** 提交 `feat: export agent team csv`

### Task 10: 演示环境默认启动 Langfuse 与 e2e 配置

**Files:**
- Modify: `Makefile:391`（`demo-up` 追加 ClickHouse 与 `--profile langfuse` 的 `langfuse-postgres langfuse-web langfuse-worker`，并调用 `deploy/local/langfuse/bootstrap.sh`；保留 `langfuse-up` 作为兼容别名）
- Modify: `apps/tap-ai-backend/scripts/check-tapper-demo.py`（`_ORDER` 加 `"langfuse"`，`_REMEDIATION`、`checks()` 的 `probes` 加 `_check_langfuse`：`GET {LANGFUSE_HOST}/api/public/health` 200；另检查 Docker 磁盘余量 < 5 GB 时在 `langfuse ok` 后附提示）
- Modify: `scripts/run-tapper-e2e.sh`（`export TAP_AGENT_TEAM_RUNTIME_METRICS=turn_table` 并加入 `readonly` 行）
- Modify: `.env.example:246-248`（取消注释本地默认 `LANGFUSE_HOST=http://127.0.0.1:3000` 与示例键，注明本地值）、`README.md` 中 demo 段落"Langfuse 默认启动"
- Test: `BT/unit/operations/test_check_tapper_demo.py`（若无则新建于 `BT/unit/`）

- [ ] **Step 1: 写失败测试**：`test_langfuse_check_ok`、`test_langfuse_check_failed_has_remediation`、`test_order_has_six_checks`。
- [ ] **Step 2:** 跑，预期失败。
- [ ] **Step 3:** 实现。子代理只改文件与跑单测，不执行 `make demo-up/down`。
- [ ] **Step 4:** PASS；`bash -n scripts/run-tapper-e2e.sh`；`make -n demo-up` 输出包含 `langfuse-web`。
- [ ] **Step 5:** 提交 `feat: start langfuse by default in the tapper demo`
- [ ] **Step 6（主会话）:** `make demo-up && make demo-check` 显示六项 ok；用真实模型对话几轮，等同步 worker 重推历史并补上 `agent:` 标签后，`TAP_RUN_LANGFUSE_SMOKE=1` 跑 Task 4 smoke，PASS。

### Task 11: 前端数据层、文案与四标签外壳

**Files:**
- Create: `F/features/knowledge/api/agentTeam.ts`、`F/widgets/tap/workspace/agents/team/AgentsPage.tsx`、`F/widgets/tap/workspace/agents/team/PeriodPicker.tsx`、`F/widgets/tap/workspace/agents/team/team.css`
- Modify: `F/widgets/tap/TapperWorkspace.tsx:3281`（渲染 `AgentsPage`，原 `AgentsWorkspace` 作为"配置"标签内容传入）、`F/widgets/tap/workspace/copy.ts`（接口 L285 后新增 `agentTeam` 键组，英文 L782、中文 L1278 后各一份）
- Test: `F/widgets/tap/workspace/agents/team/AgentsPage.test.tsx`、`F/features/knowledge/api/agentTeam.test.ts`

**Interfaces:**
- Consumes: Task 8 生成的 `components["schemas"]` 类型
- Produces（`agentTeam.ts`，沿用 `aiAssets.ts` 的 `requestJson` 与 `projectAiPath`）：`agentTeamKeys`；`useTeamSummary(projectId, period)`、`useTeamMembers(projectId, period, filters)`、`useTeamMember(projectId, agentKey, period)`、`useMemberHistory(projectId, agentId)`（`useInfiniteQuery`）、`useTeamPeople(projectId, period)`、`usePerson(projectId, actorId, period)`、`useOrgChart(projectId, period)`、`useTeamReport(projectId, period)`；`useUpdateJobTitle`、`useTransferOwner`、`useResolveFeedback`（成功后失效 `agentTeamKeys.all(projectId)` 与 `aiAssetKeys`）；`agentTeamExportUrl(projectId, period): string`
  - `PeriodPreset = "last7" | "last30" | "thisMonth" | "lastMonth" | "custom"`；`resolvePeriod(preset, today: Date, custom?) -> {from: string; to: string}`（UTC 日期）
  - `AgentsPage({projectId, copy, configTab: ReactNode, initialTab?: "mine" | "team" | "report" | "config"})`，默认 `team`；标签状态写入 URL 查询参数 `agentsTab`

- [ ] **Step 1: 写失败测试**：`resolvePeriod` 四个预设（以 2026-10-23 为今天：近 7 天 10-17..10-23、近 30 天 09-24..10-23、本月 10-01..10-23、上月 09-01..09-30）；`AgentsPage` 渲染四个 `role="tab"` 且默认选中"团队"；切到"配置"显示传入的 `configTab`；中英文案切换。
- [ ] **Step 2:** `pnpm --filter @tap/ai-frontend exec vitest run src/widgets/tap/workspace/agents/team src/features/knowledge/api/agentTeam.test.ts`，预期失败。
- [ ] **Step 3:** 实现；`copy.ts` 的 `agentTeam` 组包含本计划所有界面文案（标签名、指标名、状态名、"样本不足""运行数据暂不可用""数据更新于 {time}（{timezone}）""运行数据可能比对话晚一个同步周期""Tapper 内置""待我处理的反馈""运行数据同步中""标记已处理""转交负责人""查看配置""导出 PDF""导出 CSV"、空状态"还没有 Agent，新建一个"）。
- [ ] **Step 4:** PASS；`AgentsWorkspace.test.tsx` 不改即 PASS。
- [ ] **Step 5:** 提交 `feat: add agents page tabs and team data hooks`

### Task 12: 团队标签：汇总、花名册、人员表与图表

**Files:**
- Create: `F/widgets/tap/workspace/agents/team/TeamTab.tsx`、`RosterCards.tsx`、`RosterTable.tsx`、`PeopleTable.tsx`、`charts.tsx`
- Test: `TeamTab.test.tsx`、`charts.test.tsx`（同目录）

**Interfaces:**
- Consumes: Task 11 钩子、`PeriodPicker`
- Produces:
  - `charts.tsx`：`Sparkline({points: number[], label: string})`（`role="img"` + `aria-label`）、`TrendChart({series: {label: string; points: {bucket: string; value: number}[]}[], unit: string})`（SVG 折线，悬停显示数值）
  - `RuntimeBadge({status, computedAt, timezone, copy})`：`unavailable` 时显示"运行数据暂不可用"，`syncing` 时显示"运行数据同步中"
  - `TeamTab({projectId, copy, onOpenMember: (agentKey: string) => void})`

- [ ] **Step 1: 写失败测试**：汇总栏显示 2.6 各数字与"数据更新于"；卡片默认、可切表格；卡片含名称首字头像、岗位、负责人（内置显示"Tapper 内置"）、状态、近 30 天 Sparkline、满意度（样本不足时显示"样本不足"）、前 3 位服务对象、最近运行、最近维护；按负责人/状态/类型筛选；表格列排序改变请求参数；人员表各列可排序；`runtimeStatus="unavailable"` 时运行数字位置显示"运行数据暂不可用"而花名册照常；空花名册显示新建引导；`Sparkline` 空数组不抛错。
- [ ] **Step 2:** 跑，预期失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** PASS。
- [ ] **Step 5:** 提交 `feat: render agent team roster and people`

### Task 13: 组织架构图

**Files:**
- Create: `F/widgets/tap/workspace/agents/team/OrgChart.tsx`
- Modify: `TeamTab.tsx`（花名册下方加入）
- Test: `OrgChart.test.tsx`

**Interfaces:**
- Consumes: `useOrgChart`
- Produces: `OrgChart({chart, agentNames: Record<string, string>, copy, onOpenMember})`：左列人节点、右列 Agent 节点，SVG 连线，`owns` 实线、`serves` 虚线（`stroke-dasharray`）；点击人节点高亮其所有边与相连 Agent，再次点击取消；点击 Agent 节点调用 `onOpenMember`。节点 > 40 时容器纵向滚动。

- [ ] **Step 1: 写失败测试**：边数量与样式（`data-edge-kind`）正确；点击人节点后非相关边带 `data-dimmed="true"`；点击 Agent 触发回调；无边时显示空状态。
- [ ] **Step 2:** 跑，预期失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** PASS。
- [ ] **Step 5:** 提交 `feat: render agent team org chart`

### Task 14: Agent 档案抽屉

**Files:**
- Create: `F/widgets/tap/workspace/agents/team/AgentProfileDrawer.tsx`
- Modify: `AgentsPage.tsx`（打开抽屉；"查看配置"切到配置标签并以该 Agent 当前修订作为 `initialDetailRevisionId` 打开现有 `AgentDetailDrawer`）
- Test: `AgentProfileDrawer.test.tsx`

**Interfaces:**
- Consumes: `useTeamMember`、`useMemberHistory`、`useUpdateJobTitle`、`useTransferOwner`、`useResolveFeedback`、`TrendChart`、`AccessibleDialog`
- Produces: `AgentProfileDrawer({projectId, agentKey, period, copy, onClose, onViewConfig: (revisionId: string) => void})`，内部五个分页：基本信息、工作量（主指标 + 次要指标：模型调用、工具调用、平均每次运行调用数）、履历（时间线，事件带修订时可点开对应修订）、待处理反馈（"标记已处理" + 说明 ≤ 500）、设置（岗位 ≤ 64、转交负责人输入 actorId）。`tapper_default` 与内置 Agent 隐藏设置分页。

- [ ] **Step 1: 写失败测试**：五个分页切换；履历"加载更多"调用下一页；处理反馈后该项消失且履历出现"处理差评"；岗位超 64 字符时禁用保存；转交返回 `actor_not_found` 时显示错误文案；内置 Agent 无设置分页；"查看配置"回调带当前修订 id。
- [ ] **Step 2:** 跑，预期失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** PASS。
- [ ] **Step 5:** 提交 `feat: add agent profile drawer`

### Task 15: 我的 Agent 标签

**Files:**
- Create: `F/widgets/tap/workspace/agents/team/MyAgentsTab.tsx`
- Test: `MyAgentsTab.test.tsx`

**Interfaces:**
- Consumes: `usePerson(projectId, "me", period)`、`RosterCards`、`AgentProfileDrawer`
- Produces: `MyAgentsTab({projectId, copy, onOpenMember})`：置顶"待我处理的反馈"（来自 `person` 响应），下分"我负责的 Agent""为我服务的 Agent"两组卡片。

- [ ] **Step 1: 写失败测试**：请求用 `actorId=me`；差评区在最上；两组卡片分别渲染；无数据时各自空状态。
- [ ] **Step 2:** 跑，预期失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** PASS。
- [ ] **Step 5:** 提交 `feat: add my agents tab`

### Task 16: 汇报标签：报告页、打印样式与 CSV

**Files:**
- Create: `F/widgets/tap/workspace/agents/team/ReportTab.tsx`
- Modify: `team.css`（`@media print`：隐藏 `.tap-product-shell` 导航、页签、按钮；报告区块 `break-inside: avoid`；A4 纵向边距）
- Test: `ReportTab.test.tsx`

**Interfaces:**
- Consumes: `useTeamReport`、`agentTeamExportUrl`、`TrendChart`、`RosterTable`、`PeopleTable`
- Produces: `ReportTab({projectId, copy})`：周期默认上月；固定版式：标题（项目、周期、时区、数据更新于）→ 汇总及与上一周期对比（变化值与箭头，上一周期为 0 时显示"—"）→ 趋势 → 花名册表 → 人员表；"导出 PDF"调用 `window.print()`；"导出 CSV"为指向导出地址的下载链接。

- [ ] **Step 1: 写失败测试**：默认周期为上月；对比列计算正确（含上一周期为 0）；"导出 PDF"调用 `window.print`；CSV 链接 `href` 含 `from`、`to`；`unavailable` 时报告标注"运行数据暂不可用"。
- [ ] **Step 2:** 跑，预期失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** PASS；`pnpm --filter @tap/ai-frontend run lint` 与 `typecheck` 通过。
- [ ] **Step 5:** 提交 `feat: add agent team report page`

### Task 17: E2E 旅程与截图

**Files:**
- Create: `apps/tap-ai-frontend/tests/e2e/agent-team.spec.ts`
- Modify: `apps/tap-ai-frontend/tests/e2e/ui-capture.spec.ts`（新增五个 `test("captures ...")`，用 `page.route` 返回固定数据）
- Create: `apps/tap-ai-frontend/tests/e2e/agentTeamCaptureFixture.ts`

- [ ] **Step 1:** 写 `agent-team.spec.ts`：新建 Agent 并在档案设置中填岗位 → 选用它对话两轮并对一轮点踩 → 团队标签出现该卡片，服务对象含当前身份 → 打开档案处理该反馈，履历出现"处理差评" → 汇报标签下载 CSV，断言 zip 含三个文件。`afterEach` 停用该 Agent 并清理会话（参考 `skills-agents.spec.ts` 清理方式）。
- [ ] **Step 2:** 写五个截图用例：团队（卡片）、组织架构图、Agent 档案、我的 Agent、汇报页；数据放 `agentTeamCaptureFixture.ts`，覆盖多名负责人、内置 Agent、`tapper_default`、闲置与已停用状态。
- [ ] **Step 3（主会话）:** `make demo-e2e` 全部通过；`pnpm --filter @tap/ai-frontend run ui:capture` 生成截图；对比 Agents 页改动前后截图（"配置"标签与原页面一致）。
- [ ] **Step 4:** 提交 `test: cover agent team journey and captures`

### Task 18: 文档

**Files:**
- Create: `docs/decisions/2026-10-23-adr-033-langfuse-default-runtime-source.md`（按 `adr-template.md`，`status: proposed`）
- Modify: `docs/decisions/index.md`、`docs/architecture.md`（第 2 节加 `agent_team` 模块；第 3 节 Langfuse 改为默认组件；第 4 节加 Langfuse `agent:` 标签与团队视图数据流）、`docs/superpowers/plans/2026-10-01-product-roadmap.md:64`（V2.1 行状态改为"实施中（[plan](2026-10-23-agent-team-view.md)）"，合入后改"工程完成，待业务验证"）

- [ ] **Step 1:** ADR 内容：背景（团队视图需要运行数据）、决策（Langfuse 由可选改为默认；团队视图运行数据以 Langfuse Metrics API v1 为唯一查询来源；固定 3.x）、考虑过的方案（直接查 MySQL `trace_span`/`model_call`；自建汇总表）、后果（本地资源上升；v4 升级需迁移 v2 API；e2e 用 Turn 表适配器）。
- [ ] **Step 2:** 更新架构文档与路线图；预览 Markdown 与 Mermaid。
- [ ] **Step 3:** `git diff --check`；`make check`；`make test`，全部通过。
- [ ] **Step 4:** 提交 `docs: record agent team architecture and adr`

---

## 完成后（不在本计划任务内）

- spec 7.4 业务验证：演示环境用真实模型积累至少一周数据，生成上一周期报告，由用户确认数字与口径。
