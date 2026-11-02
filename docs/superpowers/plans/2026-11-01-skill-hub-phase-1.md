# Skill Hub 第 1 阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Skills 模块改成项目内 Skill 库：取消 Skill 绑定 Agent，改为"手动 ∪ 常驻"冻结加模型经 `skill.activate` 按需自动激活，每轮记录实际用到的 Skill 修订，并提供库页、详情、用量统计与 CSV 导出。

**Architecture:** 冻结时（HTTP `_input`）算出"手动 ∪ 常驻"与候选目录并冻结进 `TurnInput`，手动/常驻写入新表 `turn_skill_activation`；`tapper-chat-v2` 的 `tools` 节点执行平台工具 `skill.activate`，按冻结目录读入修订、记 `auto` 行与 `tool.skill.activate` span，正文作为受信 `<skill>` 块进入后续模型调用。新模块 `skill_hub` 合并 MySQL 主指标与 Langfuse `skill:` 标签次要指标；`agent_team` 的周期、缓存、期限、降级与 CSV 工具先原样抽到 `B/platform/reporting/` 再复用。前端在现有 Skills 工作区内换成库页与扩展后的详情抽屉。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async + Alembic（MySQL）、LangGraph、httpx、Langfuse 3.225.11 Metrics API v1、React 19 / TypeScript、TanStack Query、antd 6、Vitest、Playwright。

**Spec:** [Skill Hub 设计](../specs/2026-11-01-skill-hub-design.md)（只实施第 1、3–7 节）

## Global Constraints

- 路径相对仓库根；`apps/tap-ai-backend/src/tap` 简写 `B/`，`apps/tap-ai-backend/tests` 简写 `BT/`，`apps/tap-ai-frontend/src` 简写 `F/`。
- 迁移 `0038_skill_hub`，`down_revision = "0037_agent_team_fk_name"`；新表用 `augment_project_table` 并在 `B/platform/db/schema.py` 的 `PROJECT_PARENT_LINKS` 登记，迁移里的外键名必须是其生成的 `fk_<table>_project_parent_0`（0037 的教训）；迁移末尾 `DELETE FROM langfuse_sync_cursor WHERE name = 'trace'`；0038 在 Task 11 合入前不得对共享演示库执行，分支整体一次合入 `develop`，游标重置只在 `skill:` 标签已就位时生效。
- 类别闭集 `test-design`、`automation`、`knowledge`、`analysis`、`writing`、`other`；方式闭集 `mode` ∈ `manual`、`pinned`、`auto`，优先级从高到低，同一 `skill_id` 一轮只记一次、取最高优先级。
- 维护事件 `kind` 闭集 `revised`、`enabled`、`disabled`、`owner_transferred`、`synced`，与写操作同一事务；不记"创建"；第 1 阶段不产生 `synced`。
- 每轮预算：手动 + 常驻 + 自动的 Skill 文本（`instruction_template + attached_references` 的 UTF-8 字节）≤ 96 KB（`96 * 1024`），自动激活 ≤ 5 个；冻结时超额沿用 `skill_budget_exceeded`；运行中超额只拒绝该次调用并把原因返回模型。
- 候选目录：启用、适用任务含 `knowledge.answer`、未被 Agent 排除、未作为手动/常驻加载的 Skill 的最新修订；每行 `name — description`，描述截断到 200 字符，最多 60 个。
- 满意度 `up / (up + down)`，按 Turn 创建时间落入周期；有评分轮次 `< 10` 时 `satisfaction = null`、`satisfactionSample = "insufficient"`。
- 周期与降级同 Agent 团队视图：时区恒 `UTC`；`n ≤ 92` 天按 `day`、`≤ 731` 按 `week`、否则 `month`；Langfuse 结果 TTL 300 秒、失败负缓存 30 秒、每视图期限 10 秒、单次 ≤ 8 秒；Langfuse 不可用返回 200 与 `runtimeStatus: "unavailable"`，次要指标 `null`，主指标照常。
- 读接口外层 `{runtimeStatus, computedAt, timezone, period: {from, to}, granularity, data}`；`from`、`to` 必填 ISO 日期，`preset` 可选（闭集同团队视图）；前缀 `/api/v1/projects/{project_id}/ai/`；`operation_id` 前缀 `skill_hub_`。
- 错误体 `{code, detail}`（`skill_problem_response`）：`period_invalid`、`filter_invalid`、`sort_invalid`、`cursor_invalid`（无分页接口，保留）、`actor_not_found` 400；`skill_read_only` 403；`skill_not_found` 404。
- 权限：读接口 `ai.skill_hub.read`；`PUT skills/{skillId}/profile`、`POST skills/{skillId}/owner` 用 `ai.skills.write`；`PUT agents/{agentId}/skills` 用 `ai.agents.write`。
- Langfuse：只有根 span 为 `turn.request` 的 trace 追加 `skill:<skill_id>`（每个用到的 Skill 一个，升序）；Metrics API `tags` 维度返回整组数组，按元素拆分计数。
- `skill.activate`：`wire_name = "skill_activate"`，入参 `{"name": string 1..128}`，`platform=True`——目录非空即对所有 Agent 与 Tapper 默认开放，不受白名单约束，不出现在 `GET /ai/tools` 与 Agent 表单。
- 决定（spec 未定，各一行）：
  - 激活正文不进工具结果（untrusted，最终无工具轮被压平为 `untrusted.toolActivity`），改为后续模型调用的 `<skill>` 块；工具结果只回执 `{"status":"activated","name":...}`。
  - 候选目录冻结进 `TurnInput.skill_catalog`，激活按冻结的 `revision_id` 读取。
  - 超 60 个候选时按类别闭集顺序轮流取、类别内按名称升序；库汇总返回 `candidateCount`。
  - 截断按字符计（含 CJK），含末尾 `…` 共 ≤ 200；换行压成空格。
  - 精度门槛：应激活、不应激活命中率各自 ≥ 0.80，只看第一轮工具调用。
  - 导入记录不存上传方式且过期即删，已有导入修订回填 `origin_kind = "unknown"`、`origin_imported_at = created_at`；新导入记 `files`/`zip`/`paste` 与文件名或包根目录。
  - 类别只在新建时取 frontmatter `metadata.category` 或表单值，之后只经 `PUT profile`（体 `{category}`）修改，不记事件；`metadata.version` 符合 SemVer 才存，否则展示 `r<revision_number>`。
  - Tapper 默认（无 Agent）也加载手动与自动 Skill：删除 `TurnInput` 的"skill execution requires a frozen agent authority"校验。
  - `skillRevisionIds` 映射为 `skill_id` 并入 `pinnedSkillIds`；既常驻又排除时 400 `agent_skill_conflict`；`hasSkillUpdates` 恒 `false` 并标 deprecated。
  - "可对比正文" = 两修订并排渲染，不做差异高亮（属第 2 阶段）。
  - 用量按 `activated_at` 落入周期；使用者为 `chat_turn.actor_id` 去重；排序键 `name`、`last_used_at`、`activations`、`updated_at`，默认 `activations desc`，不分页；e2e 次要指标沿用 `TAP_AGENT_TEAM_RUNTIME_METRICS=turn_table`。
- 前端不新增依赖；复用 `F/widgets/tap/workspace/agents/team/` 的 `PeriodPicker`、`useValidPeriod`、`RuntimeBadge`、`charts.tsx`；抽屉用 `AccessibleDialog`；文案进 `F/widgets/tap/workspace/copy.ts`，默认英文、另有中文，不出现实现术语。
- `AGENTS.md`：TDD、小写 Conventional Commit。标记 `[unit]` 纯单元/契约；`[mysql]` 需 `TAP_RUN_MYSQL_INTEGRATION=1`（自启临时 MySQL，约 30 分钟，一次一个文件）；`[real-model]`（`TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1`）与 `[demo-e2e]` 只由主会话执行。子代理禁止 `make demo-up/down/reset`。
- 每个任务由 Opus 审查员审查，Important 及以上必须修完再合入；最后跑 `make check`、`make test`、`make demo-e2e`。

## Review Focus

1. 幂等重试期间库变了（导入新 Skill 或常驻 Skill 出新修订）：重试应返回原 Turn（202、同一 `turnId`），不是 409。→ Task 4 `test_replay_after_library_change_is_not_a_conflict`。
2. 同一轮对同一 Skill 调两次 `skill.activate`，或 worker 重启后 `tools` 节点重放：只算一次、只一行、预算不重复扣。→ Task 6 `test_duplicate_activation_in_turn_is_rejected_already_loaded`、`test_resumed_tools_node_does_not_duplicate_activation_row`。
3. Skill 在冻结后、模型调用前被停用：本次激活被拒（`skill_disabled`），Turn 照常完成。→ Task 6 `test_activation_of_skill_disabled_after_freeze_is_rejected`。
4. Agent 常驻的 Skill 后来被停用：跳过并给出提示，Turn 不失败；表单把同一 Skill 同时设为常驻与排除时被拒。→ Task 4 `test_disabled_pinned_skill_is_skipped_with_notice`，Task 2 `test_pinned_and_excluded_overlap_rejected`。
5. 名称或描述含换行、`</skill`、超长 CJK：目录行不破坏提示词结构，截断按字符计。→ Task 3 `test_catalog_line_collapses_newlines_and_counts_cjk_chars`，Task 5 `test_catalog_description_cannot_close_skill_block`。

---

### Task 1: 迁移与回填 `[unit]` `[mysql]`

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0038_skill_hub.py`、`B/modules/ai/domain/skill_metadata.py`、`B/modules/chat/adapters/mysql_skill_activations.py`（本任务只放表）、`B/modules/skill_hub/__init__.py`、`B/modules/skill_hub/adapters/{__init__,mysql}.py`（本任务只放表）
- Modify: `B/modules/ai/adapters/mysql.py:69-186`、`B/platform/db/schema.py:32-87`、`B/platform/db/registry.py:5-120`
- Test: `BT/unit/ai/test_skill_metadata.py`、`BT/integration/test_skill_hub_migration.py`

**Interfaces:**
- Produces（`skill_metadata.py`）：`SkillCategory`、`SKILL_CATEGORIES`（闭集顺序）、`ActivationMode`、`OriginKind = Literal["files","zip","paste","authored","built_in","unknown"]`；`category_from_frontmatter(frontmatter: Mapping[str, object] | None) -> SkillCategory | None`；`version_label_from_frontmatter(frontmatter) -> str | None`（SemVer、≤ 32 字符）；`display_version(version_label: str | None, revision_number: int) -> str`；`compatibility_of(frontmatter) -> str | None`；`pinned_from_bound(bound_revision_ids: Sequence[str], skill_of: Mapping[str, str]) -> tuple[str, ...]`（保序去重、丢弃查不到的）；`FrozenActivation(skill_revision_id: str, mode: Literal["manual","pinned"], bytes: int)`；`frozen_activations(snapshot: Mapping[str, object], agent_bound_revision_ids: frozenset[str]) -> tuple[FrozenActivation, ...]`（有 `requested_skill_revision_ids` 键时按它判手动，无此键时按 Agent 修订的绑定判常驻；字节取 `skill_instruction_templates` 对应项）
- Produces（schema）：`turn_skill_activation(activation_id, turn_id, skill_id, skill_revision_id, mode, activated_at, bytes)`，唯一 `uq_turn_skill_activation_turn_skill(project_id, turn_id, skill_id)`，索引 `(project_id, skill_id, activated_at)`，父表 `skill`；`activation_id_for(turn_id, skill_id) -> str` = `"act-" + sha256(f"{turn_id}/{skill_id}").hexdigest()[:24]`；`skill_maintenance_event(event_id, skill_id, kind, revision_id, note, occurred_at)`，索引 `(project_id, skill_id, occurred_at)`；`skill` 加 `category VARCHAR(16)`、`owner_actor_id VARCHAR(128)`（FK `fk_skill_owner_actor`）、`owner_assigned_at DATETIME(6)`，均 NOT NULL；`skill_revision` 加 `version_label VARCHAR(32) NULL`、`origin_kind VARCHAR(16) NOT NULL`、`origin_ref VARCHAR(512) NULL`、`origin_imported_at DATETIME(6) NULL`；`ai_agent_revision` 加 `pinned_skill_ids JSON NULL`、`excluded_skill_ids JSON NULL`

- [x] **Step 1:** 写失败单测：`test_category_from_frontmatter_is_closed_set`（`"automation"` 通过，`"ops"` 与 `None` → `None`）、`test_version_label_requires_semver`（`"1.2.0"` 通过，`"v2"` → `None`，`display_version(None, 3) == "r3"`）、`test_pinned_from_bound_maps_revisions_to_unique_skills`（`["r1","r2","rX"]` + `{"r1":"s1","r2":"s1"}` → `("s1",)`）、`test_frozen_activations_split_manual_and_pinned`（`requested=["r2"]`、正文 `"ab"`/`"数据"` → `pinned 2`、`manual 6`）、`test_frozen_activations_without_requested_key_uses_agent_binding`。
- [x] **Step 2:** `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_skill_metadata.py -v`，预期 ImportError 失败。
- [x] **Step 3:** 实现 `skill_metadata.py`、两张表与新列的 SQLAlchemy 定义，登记 `registry.py` 与 `PROJECT_PARENT_LINKS`。
- [x] **Step 4:** 写失败集成测试（`owned_project_mysql`，在 0037 上造数据再升级）：`test_skill_category_backfilled_from_latest_frontmatter_or_other`、`test_skill_owner_defaults_to_creator`、`test_imported_revision_origin_is_unknown_and_authored_keeps_kind`、`test_bound_revisions_become_pinned_skill_ids`（`excluded_skill_ids == []`，`skill_revision_ids` 原样保留）、`test_turn_skill_activation_backfilled_from_snapshots`（`activated_at = chat_turn.created_at`）、`test_trace_cursor_deleted_feedback_cursor_kept`、`test_downgrade_drops_new_schema`。
- [x] **Step 5:** 写迁移：加可空列 → Python 侧分批读出、调 `skill_metadata.py` 纯函数回填（不写 JSON 路径 SQL）→ 收紧 NOT NULL → 建表与外键 → 删 `trace` 游标；`downgrade` 反向删除。
- [x] **Step 6:** `TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_skill_hub_migration.py -v` PASS；再分别跑 `BT/integration/test_schema_drift.py`、`BT/architecture/test_migration_metadata.py`、`BT/integration/test_ai_asset_catalog.py`，PASS。
- [x] **Step 7:** 提交 `feat: add skill hub schema and backfill`

### Task 2: 作者端写入：元数据、负责人、常驻/排除与维护事件 `[unit]` `[mysql]`

**Files:**
- Create: `B/modules/skill_hub/domain/{__init__,models}.py`
- Modify: `B/modules/ai/domain/assets.py:75-260`、`B/modules/ai/adapters/mysql.py:478-1050`（读写新列；`list_skills` 带出 `skill.category`；新 Skill 首次插入写 `category`、`owner_*`）、`B/modules/skill_hub/adapters/mysql.py`、`B/modules/ai/application/skill_authoring.py:83-120,242-325,472-541,677-789`、`B/modules/ai/application/agent_authoring.py:60-135,163-320`、`B/contracts/http.py:56-160,216`、`B/interfaces/http/routes/ai_agents.py:37-90`、`B/interfaces/http/routes/ai_skills.py:168-206`、`B/interfaces/http/problems.py:127-157`
- Test（追加）：`BT/unit/ai/test_agent_authoring.py`、`BT/unit/ai/test_skill_authoring.py`、`BT/contract/test_ai_agents_api.py`、`BT/contract/test_ai_skills_api.py`、`BT/integration/test_skill_authoring_mysql.py`

**Interfaces:**
- Consumes: Task 1 闭集、表、纯函数
- Produces：`SkillMaintenanceKind`（闭集）；`SkillMaintenanceEvent(event_id, project_id, skill_id, actor_id, kind, revision_id: str | None, note: str | None, occurred_at)`；`new_skill_event(scope, skill_id, kind, *, revision_id=None, now) -> SkillMaintenanceEvent`（`event_id = f"sevt-{uuid4().hex[:16]}"`）；`insert_skill_maintenance_event(session, scope, event) -> None`；`MysqlAssetCatalog.insert_skill_revision(..., skill_event=None)`、`set_asset_enabled(..., skill_event=None)` 同事务插入
- Produces（领域）：`SkillRevision` 加 `category`、`version_label`、`origin_kind`、`origin_ref`、`origin_imported_at`；`AiAgentRevision`、`AgentDraft` 加 `pinned_skill_ids`、`excluded_skill_ids: tuple[str, ...] = ()`；`ZipUpload.filename: str | None = None`；`ImportSelection.category`、`SkillDraft.category: SkillCategory | None = None`；`AgentAuthoringService.set_skills(scope, agent_id, pinned: tuple[str, ...], excluded: tuple[str, ...]) -> AiAgentRevision`（以最新修订为 base、复制其余字段、记 `revised`）；`compute_outdated_skill_ids` 恒返回空集
- Produces（契约）：`AgentDraftRequest.pinned_skill_ids`、`excluded_skill_ids`（各 ≤ 64），`skill_revision_ids` 标 `deprecated=True`；`AgentSkillsRequest`；`PUT /ai/agents/{agent_id}/skills`（`operation_id="ai_set_agent_skills"`，返回 `AiAgentRevisionDetail`）；`AiAgentRevisionSummary` 加两字段、`has_skill_updates` 标 deprecated；`SkillRevisionSummary` 加 `category`、`version_label`；`SkillDraftRequest` 与导入提交选择加可选 `category`；`agent_skill_conflict: 400`

- [x] **Step 1:** 写失败测试：`test_create_maps_deprecated_skill_revision_ids_to_pinned`、`test_pinned_and_excluded_overlap_rejected`、`test_unknown_pinned_skill_rejected`（`agent_skill_unavailable`）、`test_set_skills_creates_revision_with_revised_event`、`test_set_skills_on_built_in_rejected`（`agent_built_in_immutable`）、`test_import_records_zip_origin_and_frontmatter_category`、`test_paste_import_without_category_uses_selection_or_other`、`test_revise_skill_passes_revised_event`、`test_disable_enable_skill_pass_events_only_on_change`；契约 `test_put_agent_skills_returns_new_revision`、`test_skill_revision_ids_marked_deprecated_in_openapi`、`test_has_skill_updates_is_always_false`；集成 `test_skill_revision_and_event_commit_together`（事件插入抛错时修订也未写入）、`test_new_skill_owner_defaults_to_creator`。
- [x] **Step 2:** 跑单元与契约文件，预期失败。
- [x] **Step 3:** 实现；`stage_import` 把 `{"origin": {"kind", "ref"}}` 写入 `preview_json`，`_commit_one` 据此与 frontmatter 填修订字段。
- [x] **Step 4:** 单元、契约 PASS；`TAP_RUN_MYSQL_INTEGRATION=1` 跑 `test_skill_authoring_mysql.py` PASS；`BT/unit/agent_team/test_classify.py` 不改即 PASS。
- [x] **Step 5:** 提交 `feat: record skill metadata, owners and agent pinned skills`

### Task 3: 激活领域规则与 `skill.activate` 注册 `[unit]`

**Files:**
- Create: `B/modules/ai/domain/skill_activation.py`
- Modify: `B/modules/ai/domain/tools.py:22-235`、`B/interfaces/http/routes/ai_assets.py:30-45`、`B/modules/ai/application/agent_authoring.py:246-320`（`toolIds` 含平台工具时 `agent_tool_allowlist_invalid`）、`B/modules/chat/application/skill_selection.py:16-41`（预算常量与字节函数改为从本模块导入）
- Test: `BT/unit/ai/test_skill_activation.py`、`BT/unit/ai/test_tool_registry.py`（"四个工具"改五个）、`BT/contract/test_ai_asset_http.py`（追加）

**Interfaces:**
- Consumes: Task 1 闭集；Task 2 `SkillRevision.category`
- Produces：`SKILL_TURN_BUDGET_BYTES = 96 * 1024`、`MAX_AUTO_ACTIVATIONS = 5`、`MAX_CATALOG_ENTRIES = 60`、`CATALOG_DESCRIPTION_CHARS = 200`；`skill_text_bytes(skill: SkillRevision) -> int`；`SkillCatalogEntry(skill_id, revision_id, name, description, category)`；`CatalogBuild(entries: tuple[SkillCatalogEntry, ...], candidate_count: int)`，`.overflow -> bool`；`build_skill_catalog(candidates: Sequence[SkillRevision], *, task: str, excluded_skill_ids: frozenset[str], loaded_skill_ids: frozenset[str]) -> CatalogBuild`；`truncate_description(text: str | None) -> str`；`render_skill_catalog(entries) -> str`（标题 `Available Skills`、每行 `name — description`、末句要求只在任务需要时调用 `skill_activate`）；`catalog_entry(name, catalog) -> SkillCatalogEntry | None`（精确匹配）；`ActivationRejection = Literal["not_in_catalog","already_loaded","skill_disabled","budget_count","budget_bytes"]`；`activation_rejection(entry, *, loaded_skill_ids, auto_count: int, loaded_bytes: int, candidate_bytes: int) -> ActivationRejection | None`；`PromptSkill(skill_id, revision_id, name, instruction, mode)`，`.bytes -> int`；`TurnSkillContext(catalog: tuple[SkillCatalogEntry, ...] = (), blocks: tuple[PromptSkill, ...] = ())`（`blocks` = 不在治理对象里、需另行渲染的 Skill）
- Produces（`tools.py`）：`ToolDefinition.platform: bool = False`；注册 `skill.activate`（`required=False`、`default_enabled=True`、`model_callable=True`、`platform=True`、`tasks={"knowledge.answer"}`）；`platform_tool_ids() -> frozenset[str]`；`visible_tool_ids(..., skills_available: bool = False)`：平台工具在白名单交集之后追加

- [x] **Step 1:** 写失败单测：

```python
def test_description_truncated_to_200_chars_with_ellipsis():
    out = truncate_description("x" * 300)
    assert len(out) == 200 and out.endswith("…")

def test_catalog_line_collapses_newlines_and_counts_cjk_chars():
    out = truncate_description("第一行\n" + "测" * 250)
    assert "\n" not in out and len(out) == 200

def test_budget_boundaries():
    e, none = SkillCatalogEntry("s1", "r1", "A", "d", "other"), frozenset()
    assert activation_rejection(e, loaded_skill_ids=none, auto_count=5, loaded_bytes=0, candidate_bytes=1) == "budget_count"
    assert activation_rejection(e, loaded_skill_ids=none, auto_count=0, loaded_bytes=96 * 1024 - 10, candidate_bytes=10) is None
    assert activation_rejection(e, loaded_skill_ids=none, auto_count=0, loaded_bytes=96 * 1024 - 10, candidate_bytes=11) == "budget_bytes"
    assert activation_rejection(e, loaded_skill_ids=frozenset({"s1"}), auto_count=0, loaded_bytes=0, candidate_bytes=1) == "already_loaded"
    assert activation_rejection(None, loaded_skill_ids=none, auto_count=0, loaded_bytes=0, candidate_bytes=0) == "not_in_catalog"
```

另加 `test_catalog_capped_at_60_round_robin_by_category`（61 个候选 → 60 条、`candidate_count == 61`、`overflow`）、`test_catalog_skips_excluded_loaded_disabled_and_inapplicable`、`test_skill_activate_bypasses_agent_allowlist`、`test_skill_activate_hidden_without_skills`、`test_platform_tool_in_agent_tool_ids_rejected`、契约 `test_tools_listing_excludes_platform_tools`。
- [x] **Step 2:** `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_skill_activation.py apps/tap-ai-backend/tests/unit/ai/test_tool_registry.py -v`，预期失败。
- [x] **Step 3:** 实现。
- [x] **Step 4:** 上述文件与 `BT/contract/test_ai_asset_http.py`、`BT/unit/ai/test_tool_calls.py` PASS。
- [x] **Step 5:** 提交 `feat: add skill activation rules and the skill.activate tool`

### Task 4: 冻结链路：手动 ∪ 常驻、候选目录与冻结记录 `[unit]` `[mysql]`

**Files:**
- Modify: `B/modules/chat/application/skill_selection.py:44-91`、`B/modules/chat/domain/conversations.py:113-280`（加 `skill_catalog`；删除 L190-191 校验；`material()` 在 `skill_catalog` 为空时 `pop`）、`B/interfaces/http/routes/conversations.py:81-226`、`B/modules/chat/adapters/mysql_conversations.py:249-270,389-426,517-620`、`B/modules/chat/adapters/mysql_skill_activations.py`
- Test（追加/重写）：`BT/unit/chat/test_skill_selection.py`、`BT/unit/chat/test_mysql_conversations_input_json.py`、`BT/contract/test_conversation_http.py`、`BT/integration/test_conversation_persistence.py`

**Interfaces:**
- Consumes: Task 3 `build_skill_catalog`、`SkillCatalogEntry`、预算常量；Task 2 `pinned_skill_ids`/`excluded_skill_ids`、`list_skills`（每个 Skill 的最新修订，含停用）
- Produces：`merge_skill_selection(requested: Sequence[SkillRevision], pinned: Sequence[SkillRevision], *, task: str) -> SkillSelection`；`SkillSelection(applied, modes: tuple[ActivationMode, ...], notices)`：手动在前、按 `asset_id` 去重，停用/不适用记 `skill_disabled:<revision_id>`/`skill_inapplicable:<revision_id>`，常驻 id 不在库中记 `skill_unavailable:<skill_id>`；`TurnInput.skill_catalog: tuple[SkillCatalogEntry, ...] = ()`；`_replay_comparable(value: TurnInput) -> TurnInput`（清空 `skill_*` 与 `skill_catalog`；手动请求一致性仍由 `_matches_replay` 比 `requested_skill_revision_ids`）；`insert_frozen_activations(session, scope, *, turn_id, value: TurnInput, activated_at) -> None`（`INSERT ... SELECT skill_id FROM skill_revision`，在 `_insert_turn` 同一事务）

- [x] **Step 1:** 写失败测试：`test_manual_skills_come_before_pinned`、`test_same_skill_manual_and_pinned_records_manual_once`、`test_disabled_pinned_skill_is_skipped_with_notice`、`test_inapplicable_skill_is_skipped`、`test_combined_budget_over_96kb_raises`、`test_agent_bound_revisions_are_no_longer_read`；`test_tapper_default_turn_accepts_manual_skills`、`test_empty_catalog_keeps_legacy_digest`、`test_catalog_round_trips_through_input_json`；契约 `test_turn_freezes_catalog_without_loaded_and_excluded_skills`、`test_pinned_skill_uses_latest_enabled_revision`、`test_replay_after_library_change_is_not_a_conflict`、`test_replay_with_different_manual_skills_conflicts`；集成 `test_frozen_manual_and_pinned_activations_written_with_turn`（`activated_at` = Turn 创建时间，`bytes` = 冻结正文字节）。
- [x] **Step 2:** 跑单元与契约文件，预期失败。
- [x] **Step 3:** 实现。`_input` 调一次 `list_skills(scope)` 按 `asset_id` 建索引取常驻，手动仍用 `resolve_historical_skill`；`_validate_replay` 比 `_replay_comparable(current) != _replay_comparable(frozen)`。
- [x] **Step 4:** PASS；`TAP_RUN_MYSQL_INTEGRATION=1` 跑 `test_conversation_persistence.py` PASS。
- [x] **Step 5:** 提交 `feat: freeze manual and pinned skills with a candidate catalog`

### Task 5: 提示词装配：Skill 块与候选目录 `[unit]`

**Files:**
- Modify: `B/modules/knowledge/adapters/litellm.py:86-110,774-1060`、`B/modules/knowledge/application/retrieve.py:824-911`、`B/modules/knowledge/api.py:224-254`、`B/modules/knowledge/application/answers.py:247-279`、`B/interfaces/http/knowledge_service.py:518-594`、`B/modules/chat/application/ports.py:280-289`、`B/entrypoints/tapper_chat_turn.py:295-306`、`B/testing/deterministic_model.py:122`
- Test（追加）：`BT/contract/test_skill_prompt_assembly.py`、`BT/unit/knowledge/test_conversation_answer_steps.py`

**Interfaces:**
- Consumes: Task 3 `TurnSkillContext`、`PromptSkill`、`render_skill_catalog`、`platform_tool_ids`；Task 4 `skill_catalog`
- Produces：五层加 `skills: TurnSkillContext | None = None`（"非空才传"写法，`use_extended_answer` 含 `skills is not None`）；`generate_conversation_answer(..., tools=(), activated_skills: tuple[PromptSkill, ...] = ())`；`turn_skill_context(frozen_input, activated, *, governed: bool) -> TurnSkillContext | None`（治理轮 `blocks = activated`；非治理轮 `blocks = 冻结手动 + activated`；全空为 `None`）
- Produces（`answer`）：治理轮 `(platform, system_instruction, *治理 Skill 块, *skills.blocks, 目录段?, boundary, *task_prompts)`；非治理轮 `(platform, *skills.blocks, 目录段?, boundary, *task_prompts)`；目录段只在本轮 `tools` 含 `skill.activate` 时出现；块沿用 `_escape_skill_name`、`_neutralize_skill_body`；治理轮 `tool_allowlist |= frozenset(tools) & platform_tool_ids()`；伪引用剥离与 `tap.skill.revision_ids` 含 `skills.blocks`

- [x] **Step 1:** 写失败测试：`test_activated_skill_renders_as_trusted_skill_block_before_boundary`、`test_ungoverned_turn_renders_manual_and_auto_skill_blocks`、`test_catalog_section_only_when_skill_activate_offered`、`test_catalog_description_cannot_close_skill_block`（描述含 `</skill>` 时提示词里 `</skill` 次数等于块数）、`test_governed_request_allows_platform_tool`、`test_auto_skill_name_is_stripped_as_pseudo_citation`、`test_generate_passes_activated_skills_and_frozen_catalog`。
- [x] **Step 2:** 跑两文件，预期失败。
- [x] **Step 3:** 实现。
- [x] **Step 4:** PASS；`BT/unit/knowledge` 与 `BT/contract/test_tapper_http_contract.py` 不改即 PASS。
- [x] **Step 5:** 提交 `feat: render activated skills and the skill catalog in answer prompts`

### Task 6: 工具循环执行 `skill.activate` 与本轮用量 `[unit]` `[mysql]` `[real-model]`

**Files:**
- Modify: `B/modules/ai/application/chat_graph.py:41-72`（`ChatGraphState.activated_skills: list[dict[str, object]]`）、`B/entrypoints/tapper_chat_turn.py:353-469,698-1010`、`B/modules/chat/application/ports.py`、`B/modules/chat/adapters/mysql_skill_activations.py`、`B/entrypoints/tapper_generation_worker.py:63-80,185-199`、`B/entrypoints/tapper_runtime.py:1123-1285`、`B/testing/deterministic_model_gateway.py:132-200`、`B/contracts/http.py:1273-1286`、`B/interfaces/http/routes/conversations.py:229-295`、`B/interfaces/http/dependencies.py:235-270`
- Create: `BT/integration/test_turn_skill_activations_mysql.py`、`BT/smoke/test_skill_activation_real_model.py`
- Test（追加）：`BT/unit/entrypoints/test_chat_turn_graph.py`、`BT/unit/entrypoints/test_deterministic_tool_calls.py`、`BT/contract/test_conversation_history_http.py`

**Interfaces:**
- Consumes: Task 3 `catalog_entry`、`activation_rejection`；Task 4 冻结目录；Task 5 `activated_skills`
- Produces（`ports.py`）：`TurnSkillUse(skill_id, skill_revision_id, label, mode)`；`SkillActivationRuntime(Protocol)`：`async load(revision_id: str) -> SkillRevision`、`async record_auto(*, turn_id, skill_id, skill_revision_id, bytes_: int, activated_at: datetime) -> None`
- Produces（adapter）：`MysqlSkillActivationRuntime(catalog, sessions, *, scope)`（`INSERT IGNORE` + `activation_id_for`）；`MysqlTurnSkillActivations(sessions, *, scope).for_turns(turn_ids) -> dict[str, tuple[TurnSkillUse, ...]]`；`HttpServices.skill_activations`；`GenerationWorker.skills`；`ChatTurnNodes(..., skills: SkillActivationRuntime | None = None)`
- Produces（节点）：`ingress` 传 `skills_available = bool(value.skill_catalog) and self._skills is not None`；`activated_skills` 每项 `{"skillId","revisionId","name","instruction","bytes"}`，`model` 节点转成 `PromptSkill(mode="auto")` 传入；`tools` 节点 `skill.activate` 分支与 `insights.query` 分支并列，`span("tool.skill.activate", {"tap.tool.step_id", "tap.skill.activation.outcome"})`，已加载字节 = 冻结正文 + 已激活；被拒：`task.step` `rejected`、转录 `{"status":"rejected","reason":...}`；成功：`record_auto` 后 `completed`、转录 `{"status":"activated","name":...}`；`load` 到已停用修订记 `skill_disabled`
- Produces（契约）：`ConversationTurnSkillView(skill_id, revision_id, label, mode)`；`ConversationTurnInputView.skills_used: list[ConversationTurnSkillView] = []`

- [x] **Step 1:** 写失败测试：`test_ingress_offers_skill_activate_only_with_catalog`、`test_skill_activate_loads_records_and_emits_steps`、`test_sixth_auto_activation_is_rejected_budget_count`、`test_activation_over_96kb_is_rejected_budget_bytes`、`test_duplicate_activation_in_turn_is_rejected_already_loaded`、`test_activation_of_skill_disabled_after_freeze_is_rejected`（Turn 继续到 `verify`）、`test_model_node_passes_activated_skills`、`test_resume_after_skill_activate_does_not_repeat_steps`；`test_skill_activate_marker_is_consumed_once`（`[e2e-tool:skill.activate:<name>]` → `{"name": ...}`，`_consumed_tool_marker_keys` 按 `name` 计）；契约 `test_turn_view_lists_skills_used_with_modes`。
- [x] **Step 2:** 跑三个文件，预期失败。
- [x] **Step 3:** 实现；worker 用同一 `MysqlAssetCatalog` 与会话工厂装配。
- [x] **Step 4:** PASS；`TAP_RUN_MYSQL_INTEGRATION=1` 跑 `test_turn_skill_activations_mysql.py`（`test_resumed_tools_node_does_not_duplicate_activation_row`、`test_for_turns_merges_frozen_and_auto_rows`）PASS。
- [x] **Step 5:** 写 smoke `test_real_model_auto_activates_a_matching_skill`：`TAP_RUN_TAPPER_REAL_MODEL_SMOKE != "1"` 时 `pytest.skip`；否则 Tapper 默认、无手动 Skill 发一个明显需要某 Skill 的问题，断言 `skills_used` 含它且 `mode == "auto"`。不设变量运行，预期 1 skipped。
- [x] **Step 6:** 提交 `feat: execute skill.activate in the chat tool loop`

### Task 7: 抽取共享报表工具（不改行为）`[unit]`

**Files:**
- Create: `B/platform/reporting/{__init__,period,runtime,ttl_cache,langfuse_metrics_client,csv_zip,satisfaction}.py`
- Modify: `B/modules/agent_team/domain/models.py:40-165`、`ports.py:16-20`、`adapters/cached_metrics.py:78-223`、`adapters/langfuse_metrics.py:408-505`、`application/service.py:191-283`、`application/export.py:100-226`、`domain/metrics.py:28-58`、`B/interfaces/http/routes/agent_team.py:83-110`
- Test: `BT/unit/platform/test_reporting.py`

**Interfaces:**
- Produces：`period.py`：`Period`、`PeriodInvalid`、`PeriodPreset`、`PERIOD_PRESETS`、`trailing_30_days`、`project_timezone`、`parse_period_query(from_: str, to: str) -> Period`、`parse_preset(value: str | None) -> PeriodPreset | None`；`runtime.py`：`RuntimeStatus`、`RuntimeMetricsUnavailable`、`combine_status`、`view_deadline(seconds: float) -> Callable[[], float]`、`gather_runtime(*awaitables)`；`ttl_cache.py`：`TtlSingleFlightCache(*, ttl_seconds=300, failure_ttl_seconds=30, fetch_timeout_seconds=8.0, clock, now)`，`async get(key: Hashable, fetch, timeout_seconds=None)`、`computed_at(key) -> datetime | None`；`langfuse_metrics_client.py`：`LangfuseMetricsClient(client, *, host, public_key, secret_key, environment, timeout_seconds=10.0)`，`async rows(*, project_id, view, dimensions, metrics, period, extra_filters=(), granularity=None) -> list[dict[str, Any]]`（含满 1000 行按日期二分）；`csv_zip.py`：`text_cell`、`blank_cell`、`iso_datetime_cell`、`csv_bytes(header, rows, *, comment_lines=()) -> bytes`、`zip_bytes(files: Mapping[str, bytes]) -> bytes`；`satisfaction.py`：`satisfaction(up, down) -> tuple[float | None, Literal["ok","insufficient"]]`（阈值 10）
- `agent_team` 原有名字全部从新位置再导出

- [x] **Step 1:** 写 `test_ttl_cache_single_flight_and_negative_cache`、`test_csv_bytes_bom_and_formula_escape`、`test_parse_period_query_rejects_bad_dates`，运行预期 ImportError 失败。
- [x] **Step 2:** 把实现原样移出（只改导入），`agent_team` 改为调用与再导出。
- [x] **Step 3:** `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/platform/test_reporting.py apps/tap-ai-backend/tests/unit/agent_team apps/tap-ai-backend/tests/contract/test_agent_team_api.py -v` 全部 PASS，且这些 `agent_team` 测试文件零改动。
- [x] **Step 4:** 提交 `refactor: extract shared reporting helpers from agent team`

### Task 8: `SkillHubStore`：MySQL 读模型与两个写操作 `[mysql]`

**Files:**
- Create: `B/modules/skill_hub/ports.py`
- Modify: `B/modules/skill_hub/adapters/mysql.py`
- Test: `BT/integration/test_skill_hub_store.py`

**Interfaces:**
- Consumes: Task 1 表；Task 2 `SkillMaintenanceEvent`；Task 7 `Period`
- Produces：`SkillRecord(skill_id, name, description, category, version, latest_revision_id, revision_number, source_kind, origin_kind, origin_ref, origin_imported_at, owner_actor_id, owner_assigned_at, status, applicable_tasks, compatibility, updated_at, built_in)`（`version` 已经 `display_version`）；`ActivationCount(skill_id, mode, runs)`；`SkillRatings(skill_id, up, down)`；`UsagePoint(bucket_start: date, mode, runs)`；`RankedKey(key, runs)`；`SkillHubStore(Protocol)`：`skills()`、`skill(skill_id)`、`activation_counts(period)`、`distinct_users(period) -> dict[str, int]`、`ratings(period)`、`last_used() -> dict[str, datetime]`、`pinned_by() -> dict[str, tuple[str, ...]]`（最新且启用的 Agent 修订）、`usage_trend(skill_id, period, granularity)`、`top_agents(skill_id, period, limit=5)`（无 Agent 记 `tapper_default`）、`top_callers(skill_id, period, limit=5)`、`actor_exists(actor_id)`、`set_category(skill_id, category) -> SkillRecord`、`transfer_owner(skill_id, new_owner, event) -> SkillRecord`；`MysqlSkillHubStore(sessions, *, scope)` 全部按 `scope.project_id` 过滤

- [x] **Step 1:** 写失败集成测试：`test_activation_counts_split_by_mode`、`test_counts_use_activated_at_period`、`test_distinct_users_count_turn_actors`、`test_ratings_use_turn_created_time`、`test_last_used_ignores_period`、`test_pinned_by_counts_latest_enabled_agent_revisions_only`、`test_usage_trend_buckets_by_granularity`（周桶从周一开始）、`test_top_agents_include_tapper_default`、`test_transfer_writes_owner_and_event_atomically`、`test_queries_are_project_scoped`。
- [x] **Step 2:** `TAP_RUN_MYSQL_INTEGRATION=1` 跑该文件，预期失败。
- [x] **Step 3:** 实现。
- [x] **Step 4:** PASS。
- [x] **Step 5:** 提交 `feat: add skill hub mysql store`

### Task 9: 运行数据适配器与 `SkillHubService` `[unit]` `[mysql]`

**Files:**
- Create: `B/modules/skill_hub/adapters/{langfuse_metrics,turn_table_metrics,cached_metrics}.py`、`B/modules/skill_hub/domain/metrics.py`、`B/modules/skill_hub/application/{__init__,service}.py`
- Test: `BT/unit/skill_hub/test_{langfuse_metrics,metrics,service}.py`、`BT/integration/test_skill_turn_table_metrics.py`

**Interfaces:**
- Consumes: Task 7 全部共享工具；Task 8 `SkillHubStore`
- Produces（`ports.py` 追加）：`SkillRuntimeStats(skill_id, runs, failures, latency_total_ms, cost_usd: Decimal)`；`SkillRuntimeMetrics(Protocol)`：`async skill_stats(project_id, period) -> list[SkillRuntimeStats]`；`LangfuseSkillRuntimeMetrics(client: LangfuseMetricsClient)`（`traces` 视图、`tags` 维度、`count/count`、`latency/sum`、`totalCost/sum`，`outcome:failed` 计入 `failures`）；`TurnTableSkillRuntimeMetrics(sessions)`（仅 e2e，成本 0）；`CachedSkillRuntimeMetrics(inner, cache: TtlSingleFlightCache)`
- Produces（`domain/models.py` 追加，字段即 HTTP 字段的 snake_case）：`CatalogItem(record, activations, by_mode)`；`SkillUsage(by_mode, users, satisfaction, satisfaction_sample, last_used_at, pinned_by_agents, runtime: SkillRuntimeStats | None)`；`LibrarySummary(skill_total, enabled, used_in_period, unused, auto_share: float | None, candidate_count, candidate_limit=60)`；`SkillDetail(record, usage, revisions, files, pinned_by, top_agents, top_callers)`；`UsageSeries(points, by_mode)`；`HubView[T](runtime_status, computed_at, timezone, period, granularity, data)`
- Produces（`service.py`）：`SkillHubRejected(code, detail)`；`SkillHubService(store, metrics, *, asset_catalog, scope, clock, sync_lag: Callable[[], Awaitable[timedelta | None]] | None = None)`（`asset_catalog.list_revisions("skill", skill_id)` 供修订与文件；`sync_lag` 由装配接 `MysqlAgentTeamStore.oldest_unsynced_age`，超 5 分钟为 `syncing`）：`catalog(period, *, q=None, category=None, task=None, source=None, status=None, sort="activations", order="desc")`、`detail(skill_id, period)`、`usage(skill_id, period)`、`summary(period)`、`export_data(period)`、`update_profile(skill_id, category) -> SkillRecord`、`transfer_owner(skill_id, actor_id) -> SkillRecord`

- [x] **Step 1:** 写失败单测：`test_skill_stats_split_tag_arrays_per_skill`、`test_trace_with_two_skills_counts_once_for_each`、`test_timeout_raises_unavailable`；`test_activation_total_is_sum_of_modes`、`test_satisfaction_below_10_ratings_is_insufficient`（9 条 → None/insufficient，10 条 7 赞 → 0.7）、`test_auto_share_is_none_without_activations`、`test_unused_counts_enabled_skills_with_zero_activations`；`test_unavailable_runtime_keeps_primary_metrics`、`test_invalid_category_filter_rejected`（`filter_invalid`）、`test_invalid_sort_rejected`（`sort_invalid`）、`test_q_longer_than_128_rejected`、`test_unknown_skill_rejected`（`skill_not_found`）、`test_built_in_profile_and_owner_rejected`（`skill_read_only`）、`test_transfer_to_unknown_actor_rejected`（`actor_not_found`）、`test_transfer_to_current_owner_is_noop`、`test_sort_by_last_used_puts_never_used_last`。
- [x] **Step 2:** 跑三个单元文件，预期失败。
- [x] **Step 3:** 实现。
- [x] **Step 4:** PASS；`TAP_RUN_MYSQL_INTEGRATION=1` 跑 `test_skill_turn_table_metrics.py`（`test_turn_table_counts_runs_and_failures_per_skill`）PASS。
- [x] **Step 5:** 提交 `feat: compute skill hub usage views`

### Task 10: HTTP 接口、权限、契约与 CSV 导出 `[unit]` `[mysql]`

**Files:**
- Create: `B/interfaces/http/routes/skill_hub.py`、`B/modules/skill_hub/application/export.py`
- Modify: `B/contracts/http.py`、`B/interfaces/http/app.py:105-123`、`B/interfaces/http/dependencies.py:235-410`（`HttpServices.skill_hub`、`skill_hub_service(request)`）、`B/interfaces/http/problems.py:127-157,466`、`B/modules/access/adapters/validation.py:24-46`（`("ai.skill_hub.read", "ai")`）、`B/entrypoints/tapper_runtime.py:1057-1063,2070-2130`（`_create_skill_hub_service`）
- Regenerate: `contracts/`、`F/shared/api/generated/schema.ts`（`make contracts`）
- Test: `BT/contract/test_skill_hub_api.py`、`BT/unit/skill_hub/test_export.py`、`BT/integration/test_skill_hub_export.py`

**Interfaces:**
- Consumes: Task 9 服务
- Produces：`GET skill-hub/catalog`（`q`、`category`、`task`、`source ∈ imported|authored|built_in`、`status ∈ enabled|disabled`、`sort`、`order`）、`GET skill-hub/catalog/{skillId}`、`GET skill-hub/catalog/{skillId}/usage`、`GET skill-hub/summary`、`GET skill-hub/export`；`PUT skills/{skillId}/profile` 体 `{category}`、`POST skills/{skillId}/owner` 体 `{actorId}`，返回 `SkillHubSkill`（`SkillRecord` 的 camelCase）
- Produces（`export.py`）：`build_skill_export(items, usage_rows, *, period, runtime_status) -> bytes`：`library.csv`（元数据、三种方式激活次数、使用者、满意度、最近使用、常驻 Agent 数、成本、耗时、失败）与 `usage.csv`（`bucket_start, bucket_end, skill_id, mode, runs`，首行 `# granularity=<g>`）；BOM、公式转义、`# runtime=unavailable` 规则同 Agent 导出；`Content-Disposition: attachment; filename="skill-hub-<from>-<to>.zip"`

- [x] **Step 1:** 写失败测试：每个接口一条成功用例；`test_reversed_period_returns_period_invalid`、`test_unknown_preset_returns_period_invalid`、`test_reads_require_skill_hub_read`、`test_profile_and_owner_require_skills_write`、`test_built_in_profile_returns_skill_read_only`、`test_missing_skill_returns_skill_not_found`、`test_langfuse_down_returns_200_unavailable`；`test_zip_has_two_bom_csv_files`、`test_formula_like_skill_name_is_escaped`、`test_unavailable_runtime_leaves_columns_blank`；集成 `test_chinese_names_round_trip`。
- [x] **Step 2:** 跑契约与导出单测，预期失败。
- [x] **Step 3:** 实现并 `make contracts`。
- [x] **Step 4:** PASS；`BT/contract/test_generated_contracts.py` PASS；`git status --short contracts apps/tap-ai-frontend/src/shared/api/generated` 只含预期生成物；`TAP_RUN_MYSQL_INTEGRATION=1` 跑导出集成测试 PASS。
- [x] **Step 5:** 提交 `feat: expose skill hub api and csv export`

### Task 11: Langfuse 同步追加 `skill:` 标签 `[unit]` `[mysql]`

**Files:**
- Modify: `B/modules/ai/domain/langfuse_sync.py:50-61`（`TraceBundle.skill_ids: tuple[str, ...] = ()`）、`B/modules/ai/adapters/mysql_langfuse_sync.py:218-307`、`B/modules/ai/application/langfuse_mapping.py:137-141`
- Test（追加）：`BT/unit/ai/test_langfuse_mapping.py`、`BT/integration/test_mysql_langfuse_sync_store.py`

**Interfaces:**
- Consumes: Task 1 `turn_skill_activation`（游标重置已在 Task 1 迁移；0038 在本任务合入前不得对共享演示库执行，分支整体一次合入 `develop`，重置只在 `skill:` 标签已就位时生效）
- Produces：根 span 为 `turn.request` 时 `load_trace` 读该 Turn 的全部 `skill_id`（升序去重）；`to_langfuse_trace` 在 `agent:`、`outcome:` 之后追加每个 `skill:<skill_id>`

- [x] **Step 1:** 写失败测试：`test_turn_trace_gets_one_skill_tag_per_used_skill`、`test_trace_without_skills_has_no_skill_tag`、`test_job_trace_has_no_skill_tag`；集成 `test_load_trace_reads_frozen_and_auto_skill_ids`、`test_standalone_execute_root_gets_no_skill_ids`。
- [x] **Step 2:** 跑，预期失败。
- [x] **Step 3:** 实现。
- [x] **Step 4:** PASS（集成用 `TAP_RUN_MYSQL_INTEGRATION=1`）；`BT/unit/ai/test_langfuse_sync.py` 不改即 PASS。
- [x] **Step 5:** 提交 `feat: tag langfuse turn traces with used skills`

### Task 12: 前端数据层与库页 `[unit]`

**Files:**
- Create: `F/features/knowledge/api/skillHub.ts`（及 `.test.ts`）、`F/widgets/tap/workspace/skills/{SkillLibrary,SkillCard}.tsx`、`skillHub.css`、`SkillLibrary.test.tsx`
- Modify: `F/widgets/tap/workspace/skills/SkillsWorkspace.tsx:39-239`（持有 `PeriodSelection` 状态，主体换成 `SkillLibrary`，导入、新建、表单、抽屉接线保留）、`F/widgets/tap/workspace/copy.ts`（接口 L184 后加 `skillHub` 组，英文 L930 后、中文 L1701 后各一份）、`SkillsWorkspace.test.tsx`

**Interfaces:**
- Consumes: Task 10 生成类型；`requestJson`、`projectAiPath`；`PeriodPicker`、`useValidPeriod`、`RuntimeBadge`
- Produces（`skillHub.ts`）：`skillHubKeys`；`CatalogFilters { q?; category?; task?; source?; status?; sort?: "name"|"last_used_at"|"activations"|"updated_at"; order?: "asc"|"desc" }`；`useSkillHubCatalog(projectId, period, filters)`、`useSkillHubSummary(projectId, period)`、`useSkillHubDetail(projectId, skillId, period)`、`useSkillHubUsage(projectId, skillId, period)`、`useUpdateSkillProfile(projectId)`、`useTransferSkillOwner(projectId)`（成功后失效 `skillHubKeys.all(projectId)` 与 `aiAssetKeys`）、`skillHubExportUrl(projectId, period): string`
- Produces：`SkillLibrary({projectId, copy, selection: PeriodSelection, onSelectionChange, onOpen: (latestRevisionId: string) => void, onImport, onCreate})`；`SkillCard({item, copy, onOpen})`

- [x] **Step 1:** 写失败测试：搜索与四个筛选改变 `q`、`category`、`task`、`source`、`status`；排序改变 `sort`/`order`；卡片显示名称、描述、类别、版本（`1.2.0` 或 `r3`）、来源、更新时间、周期内激活次数、状态；汇总栏数字；`candidateCount > 60` 时顶部提示；空库显示"导入 Skill"并调用 `onImport`；`runtimeStatus="unavailable"` 时提示而卡片照常；"导出 CSV"链接含 `from`、`to`；中英文案切换。
- [x] **Step 2:** `pnpm --filter @tap/ai-frontend exec vitest run src/widgets/tap/workspace/skills src/features/knowledge/api/skillHub.test.ts`，预期失败。
- [x] **Step 3:** 实现。
- [x] **Step 4:** PASS。
- [x] **Step 5:** 提交 `feat: turn the skills page into a skill library`

### Task 13: 详情抽屉：基本信息、修订对比、用量与设置 `[unit]`

**Files:**
- Modify: `F/widgets/tap/workspace/skills/SkillDetailDrawer.tsx:17-226`（分页改为 `overview|instructions|files|revisions|usage|settings`，`metadata` 并入 `overview`；加 `period: TeamPeriod` 属性，由 `SkillsWorkspace` 传 `useValidPeriod(selection).period`）、`SkillDetailDrawer.test.tsx`
- Create: `F/widgets/tap/workspace/skills/{SkillUsagePanel,SkillSettingsPanel,RevisionCompare}.tsx` 及同名 `.test.tsx`

**Interfaces:**
- Consumes: Task 12 钩子；`skillMarkdown`；`TrendChart`；`useSetSkillEnabled`、`useSkillRevisions`
- Produces：`SkillUsagePanel({projectId, skillId, period, copy})`（指标卡：三种方式次数并注明"不可相加"、使用者、满意度或"样本不足"、最近使用、常驻 Agent 数、成本/耗时/失败或"运行数据暂不可用"；按方式趋势；Agent 与调用者排行）；`SkillSettingsPanel({projectId, detail, copy, onEdit})`（类别、转交负责人、启用/停用、新建修订；内置只读）；`RevisionCompare({projectId, skillId, copy})`（两个修订下拉，左右并排渲染）

- [x] **Step 1:** 写失败测试：六个分页切换；`overview` 显示描述、类别、版本、来源出处（方式、文件名、导入时间、导入人）、负责人、兼容性；选两个修订后并排渲染；样本不足与运行数据不可用的显示；改类别发 `PUT profile`；转交得 `actor_not_found` 时显示错误；内置 Skill 设置分页无可写控件。
- [x] **Step 2:** `pnpm --filter @tap/ai-frontend exec vitest run src/widgets/tap/workspace/skills`，预期失败。
- [x] **Step 3:** 实现。
- [x] **Step 4:** PASS。
- [x] **Step 5:** 提交 `feat: add skill detail usage, settings and revision compare`

### Task 14: Agent 表单常驻/排除、对话输入与"本轮用到的 Skill" `[unit]`

**Files:**
- Modify: `F/features/conversations/api/queries.ts:422-500`（`useConversationStream`）、`F/widgets/tap/workspace/agents/AgentForm.tsx:74-245,383-420`（"常驻 Skills""排除 Skills"两个多选，预算提示按常驻 Skill 最新修订合计）、`AgentDetailDrawer.tsx:30-130`（删除升级与 `onUpgraded`，改列常驻与排除）、`F/widgets/tap/TapperWorkspace.tsx:120-215,1936-1946,2423-2437,2732-2738`、`F/widgets/tap/workspace/copy.ts`
- Test（追加/更新）：`AgentForm.test.tsx`、`AgentDetailDrawer.test.tsx`、`F/widgets/tap/TapperWorkspace.interactions.test.tsx`、`F/features/conversations/api/queries.test.tsx`

**Interfaces:**
- Consumes: Task 2 `pinnedSkillIds`/`excludedSkillIds`；Task 6 `skillsUsed`
- Produces：表单只发 `pinnedSkillIds`、`excludedSkillIds`；`bundledSkillIdsFor` 把选中 Agent 的 `pinnedSkillIds` 映射到最新修订 id，只用于锁定标签与预算，发送的 `skillRevisionIds` 只含手选；`TurnContext` 从 `turn.input.skillsUsed` 渲染本轮用到的 Skill，每项标"手动/常驻/自动"，仍可点开详情
- 刚流式完成的回答：现在会话详情只在发送后由 create/append 的 `invalidateQueries(conversationKeys.all)` 取一次（此时只有冻结的手动/常驻行），`useConversationStream` 收到目标 Turn 的终态事件（`isTerminalEvent`）后只停止轮询、不重取详情，故自动激活要到重新加载才可见；本任务在 `useConversationStream` 设置 `targetTerminal` 时 `invalidateQueries({queryKey: conversationKeys.detail(projectId, conversationId)})`，`turn.input.skillsUsed` 随之刷新

- [x] **Step 1:** 写失败测试：`submits pinned and excluded skill ids`、`a pinned skill is disabled in the excluded picker`、`shows no upgrade prompt`、`sends only manually selected skills`、`answer context lists skills used with their modes`（含一条自动）、`refetches conversation detail when the streamed turn reaches a terminal event`（`queries.test.tsx`）。
- [x] **Step 2:** `pnpm --filter @tap/ai-frontend exec vitest run src/widgets/tap/workspace/agents src/widgets/tap/TapperWorkspace.interactions.test.tsx src/features/conversations/api/queries.test.tsx`，预期失败。
- [x] **Step 3:** 实现。
- [x] **Step 4:** PASS；`pnpm --filter @tap/ai-frontend run lint` 与 `run build` 通过。
- [x] **Step 5:** 提交 `feat: pin and exclude skills on agents and show skills used per turn`

### Task 15: 激活精度夹具（`make check` 之外的 opt-in 评估）`[unit]` `[real-model]`

**Files:**
- Create: `B/quality/skill_activation_golden.py`、`scripts/run-skill-activation-golden.py`、`BT/fixtures/quality/skill-activation/{golden-v1,fixture-observations-v1}.json`、`BT/unit/quality/test_skill_activation_golden.py`
- Modify: `Makefile`（`quality-skill-activation`、`quality-skill-activation-real`；脚本加入 `check` 的 ruff/mypy 列表，但不加入任何执行评估的目标）

**Interfaces:**
- Consumes: Task 3 `render_skill_catalog`；Task 5 `KnowledgeModelGateway.answer(..., tools=("knowledge.search","skill.activate"), skills=TurnSkillContext(catalog=...))`
- Produces：夹具 `{"version": "skill-activation-golden-v1", "skills": [{name, description, category}], "cases": [{id, question, evidence, expected: [name, ...]}]}`，10 题，`expected` 可空；`validate_activation_golden(data) -> None`；`ActivationMetrics(should_rate, should_not_rate, pairs)`；`compute_activation_metrics(golden, observations: Mapping[str, frozenset[str]]) -> ActivationMetrics`（逐个"问题 × 候选 Skill"判定，只看第一轮 `skill_activate` 的 `name`）；`gate_passed(metrics) -> bool`（两率均 ≥ 0.80）；脚本 `--mode fake` 校验夹具并评分已提交观测，`--mode real` 走上面的生产装配；`-real` 目标需 `TAP_RUN_QUALITY_SKILL_ACTIVATION=1` 与 `TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION=approved:*`

- [x] **Step 1:** 写失败单测：`test_golden_has_ten_cases_and_valid_skill_names`、`test_metrics_count_should_and_should_not_pairs`（8/10、9/10 → 0.8、0.9）、`test_gate_requires_both_rates_at_least_080`（0.79 不过）、`test_unknown_skill_name_in_observation_counts_as_wrong_activation`。
- [x] **Step 2:** `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/quality/test_skill_activation_golden.py -v`，预期失败。
- [x] **Step 3:** 实现模块、脚本、夹具与 Makefile 目标。
- [x] **Step 4:** 单测 PASS；`make quality-skill-activation` 退出 0；不设变量时 `make quality-skill-activation-real` 退出码 2。
- [x] **Step 5:** 提交 `test: add skill activation precision fixture`
- [x] **Step 6（主会话）:** 授权后跑 `make quality-skill-activation-real`，两率均 ≥ 0.80；不达标则调整 `render_skill_catalog` 说明句与 `skill.activate` 工具描述后重跑，结果记入 Task 18 的交接文档。

### Task 16: e2e 旅程与截图 `[demo-e2e]`

**Files:**
- Create: `apps/tap-ai-frontend/tests/e2e/skill-hub.spec.ts`、`apps/tap-ai-frontend/tests/e2e/skillHubCaptureFixture.ts`
- Modify: `scripts/tapper-e2e-specs.json`（`journey` 加 `tests/e2e/skill-hub.spec.ts`）、`apps/tap-ai-frontend/tests/e2e/skills-agents.spec.ts:162`（"绑定到新 Agent"改为"设为常驻"）、`apps/tap-ai-frontend/tests/e2e/ui-capture.spec.ts`（四个 `test("captures ...")`，`page.route` 返回固定数据）

- [x] **Step 1:** 写 `skill-hub.spec.ts`：导入 `tests/e2e/fixtures/skills/claim-intake-checklist` → 库中出现该卡片 → 不手选 Skill、不选 Agent 提问（含 `[e2e-tool:skill.activate:<name>]`）→ 回答展开里该 Skill 标"自动" → 详情用量分页自动激活次数为 1 → 下载 CSV，zip 含 `library.csv`、`usage.csv` 且该 Skill 自动激活列为 1。`afterEach` 停用该 Skill 并清理会话（同 `skills-agents.spec.ts`）。
- [x] **Step 2:** 写截图用例：库、详情（用量）、Agent 表单（常驻/排除）、对话展开；夹具覆盖 6 个类别、内置与停用 Skill、样本不足、61 个候选的超限提示。
- [x] **Step 3:** `pnpm --filter @tap/ai-frontend exec playwright test --list tests/e2e/skill-hub.spec.ts` 列出用例；提交 `test: cover the skill hub journey and captures`
- [x] **Step 4（主会话）:** `TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1` 跑 Task 6 smoke PASS；`make demo-e2e` 全部通过；`pnpm --filter @tap/ai-frontend run ui:capture`，与改动前的 Skills 页、Agent 表单、对话展开截图对比。

### Task 17: 业务验证：名册 Agent 去常驻复跑 `[real-model]`（主会话，不写代码）

- [x] **Step 1:** 演示环境（`make demo-dev` 已运行、0038 已执行）`GET /api/v1/projects/tapper-demo/ai/agents`，确认 Test Case Designer 常驻 `Citation skill`、Automation Script Writer 常驻 `webapp-testing`（迁移映射结果），把两者当前修订 id 备份到 `~/tap-synthetic-2026-10-28/roster/`。
- [x] **Step 2:** 对两者 `PUT /ai/agents/{agentId}/skills`，体 `{"pinnedSkillIds": [], "excludedSkillIds": []}`（带 `Origin: http://127.0.0.1:5173` 与 `Idempotency-Key`）。
- [x] **Step 3:** 用真实模型复跑 [Agent 名册](../../guides/2026-10-28-agent-roster.md) 第 3.2、3.4 节的验证问题（对话带 `sourceRevisionIds`），从会话详情的 `skillsUsed` 记录每轮是否以 `auto` 激活了原常驻 Skill。
- [x] **Step 4:** 期望：需要该 Skill 的问题全部自动激活；回答质量（引用、结构、名册第 3 节判定要点）不低于名册记录的绑定时结论。不达标则恢复常驻并记原因。结论写入名册第 4 节一行。

### Task 18: 文档

**Files:**
- Modify: `docs/superpowers/plans/2026-10-01-product-roadmap.md:65`（"Skill Hub"行：实施中并链接本计划；合入 `develop` 后改"第 1 阶段工程完成，待业务验证"）、`docs/architecture.md:14-30,46-90`（第 2 节加 `skill_hub` 与 `B/platform/reporting/`；第 4 节加 `skill.activate` 平台工具、`turn_skill_activation` 与 Langfuse `skill:` 标签数据流）、`docs/guides/2026-10-28-agents-skills-handoff.md:12,74`（第 1 阶段状态、Task 15 精度结果与 Task 17 结论）、`docs/superpowers/specs/2026-11-01-skill-hub-design.md` 第 1.3 节

- [x] **Step 1:** 更新三份文档；预览 Markdown 与 Mermaid。
- [x] **Step 1b:** 在 spec 第 1.3 节"激活"条目后补一句：`skill.activate` 只返回回执，Skill 正文作为受信 Skill 块进入下一次模型调用，因为工具结果属不受信内容，最终无工具轮会被压平为 `untrusted.toolActivity`；只加这一句。
- [ ] **Step 2:** `git diff --check`；`make check`；`make test`，全部通过。
- [x] **Step 3:** 提交 `docs: record skill hub phase 1 in roadmap, architecture and handoff`
