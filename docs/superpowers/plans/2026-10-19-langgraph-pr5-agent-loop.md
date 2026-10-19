# LangGraph PR 5：角色与 agent 循环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tapper 基础角色版本化并进入 Turn 冻结与 trace；Agent 修订与工具白名单进入 `tapper-chat-v2` 图状态；planner 每轮给出 `intent` 与理由（只用于观测、评测与 `out_of_scope` 拒答）；`model` 节点以 LiteLLM 原生 tool calling 在单一受限 ReAct 循环中调用 `knowledge.search`、`graph.relations`、`insights.query`，每次调用发出 `task.step`；模型可直接给出不带引用的澄清回复；关系分析只以 `graph.relations` 工具形式执行。

**Architecture:** 图从 `seed_search → model → verify` 变为 `seed_search → model ⇄ tools → verify`：`model` 节点返回 `tool_calls` 时进入 `tools` 节点，`tools` 校验参数范围与预算后执行工具，把结果并入可序列化的证据池（`EvidenceBundle`，S/R/I 标签全局唯一、只追加不重排），再回到 `model`；工具轮数达到 `maxToolRounds` 后 `model` 不再提供工具，强制出最终回答。工具定义、可见性与参数校验是 `modules/ai/domain/` 中的纯函数；知识侧工具（检索、关系）在 `AuthorizedRetrieval` 上新增“扩充证据池”方法，经 `ConversationAnswerSteps` 端口暴露；`insights.query` 经现有 Insights 运行时执行。不做意图路由：是否继续调工具、是否澄清由模型在循环中决定。

**Tech Stack:** Python 3.13、LangGraph 1.2.12、httpx（LiteLLM OpenAI 兼容 tool calling）、pytest；React/TypeScript/Vitest、Playwright（`make demo-e2e`）。

**Spec:** [Tapper LangGraph 编排设计](../specs/2026-10-14-tapper-langgraph-orchestration-design.md) 第 1 节、2.1、4.1、4.2、4.3（工具轮数）、4.4（工具参数硬约束）、5.1、6.1、7、8 与第 9 节 PR 5 行；能力 4 输入见 [Skills/Agents 设计](../specs/2026-10-11-skills-agents-design.md) 与 [ADR-032](../../decisions/2026-10-12-adr-032-skill-packages-and-tool-registry.md)；剖面定义见 [ADR-029](../../decisions/2026-09-18-adr-029-langgraph-ai-interaction-task-orchestrator.md)（Bounded Agentic Task）。

## Global Constraints

- 分支 `feat/langgraph-pr5`（worktree `.worktrees/lg-pr5`），基于 `origin/develop` `c66d4108`（已含 PR 1–4 与能力 4）。
- 不在 PR 5 范围：`document.read_section` / `document.analyse`、durable 子图、`interrupt` / `resume`、`task.progress`、`turn.awaiting_input`（PR 7）；依据支撑校验、诱导胡编子集（PR 6）；MCP、执行 Skill 包内代码、多 Agent。
- 图版本 `tapper-chat-v2` 不变；`CHAT_STATE_SCHEMA_VERSION` 由 `2` 升为 `3`（旧 checkpoint 沿用既有逻辑丢弃后从头重跑）。LangGraph `recursion_limit` 显式设为 `2 * maxToolRounds + 12`（`maxToolRounds` 上限 20 时为 52）。
- 预算沿用 PR 4：`TAPPER_TURN_MAX_TOOL_ROUNDS=6`、`TAPPER_TURN_MAX_SECONDS=180`、`TAPPER_TURN_MAX_MODEL_CALLS=10`、`TAPPER_TURN_MAX_COST_MICROS=0`。一“轮”= 一次返回 `tool_calls` 的模型响应及其全部调用；`toolRounds` 在 `tools` 节点结束时加 1。`toolRounds >= maxToolRounds` 时 `model` 以空工具集调用（不失败）。单轮最多执行 `3` 个工具调用（`MAX_TOOL_CALLS_PER_ROUND = 3`），多余的按 `too-many-calls` 拒绝。模型调用、墙钟、费用耗尽仍按 PR 4 落 `turn.failed`（`budget-exhausted`）。
- 工具 ID 与线上函数名（OpenAI 函数名不允许 `.`）：`knowledge.search` ↔ `knowledge_search`、`graph.relations` ↔ `graph_relations`、`insights.query` ↔ `insights_query`；`knowledge.answer` 不是可调用工具（`model_callable=False`）。`knowledge.search`、`knowledge.answer` 保持 `required=True`（Agent 表单勾选且禁用）；`graph.relations`、`insights.query` 为 `required=False`、`default_enabled=True`（新建 Agent 默认勾选）。已存 Agent 修订不可变，其白名单不含新工具即不可见（不迁移）。
- 证据池上限：S 证据 20 条（沿用 `_MAX_EVIDENCE`）、R 关系 20 条、I（Insights 指标记录）20 条；编码后 ≤ `EVIDENCE_BUNDLE_MAX_BYTES = 524_288`，超限为确定性错误。新条目标签接续最大序号，已有标签永不重排；按 `(source_id, revision, chunk_id)` / 边 ID / `(queryId, metricId)` 去重。
- 工具参数硬约束（spec 4.4）：`knowledge.search {query: string 1..500, sourceIds?: string[] ⊆ Turn 冻结 resolved_resources 的 source_id}`；`graph.relations {query: string 1..500}`（范围固定为冻结选择的修订）；`insights.query` 沿用 `StructuredInsightsTool` 的闭合输入契约，授权范围固定为 Turn 冻结的 `insights_report_refs`。片段或工具结果里的 ID、项目、URL 不扩大范围。被拒调用不执行，以 `{"status": "rejected", "reason": <code>}` 回灌模型并发 `task.step status=rejected`。拒绝原因码：`unknown-tool`、`tool-not-visible`、`invalid-arguments`、`source-out-of-scope`、`too-many-calls`。
- `task.step` 载荷：`{stepId, tool, status, summary, durationMs, toolRound}`，`status ∈ {"running", "completed", "rejected", "failed"}`，`stepId = f"{turnId}:r{round}:c{index}"`，`summary` 经 v2 对话出口脱敏且 ≤ 200 字符。每个调用先发 `running`，结束发终态；去重键 `task.step:{stepId}:{status}`。
- 澄清：模型输出去掉前导空白后以 `CLARIFY:` 开头即为澄清回复；去掉前缀后的正文为答案，`answerKind = "clarification"`，`claims = []`、`citations = []`、`abstained = False`，`turn.completed`。确定性 `clarify` 路由（planner 缺来源/指代不明）同样标 `answerKind = "clarification"`。其余回答 `answerKind = "answer"`。
- `TapperPersona` 版本 `tapper-persona-v1`，正文从 `modules/knowledge/adapters/litellm.py` 的 `_TAPPER_PLATFORM_INSTRUCTION` / `_TAPPER_CUSTOMIZATION_BOUNDARY` 逐字迁移；Turn 冻结字段 `persona_version`（旧 Turn 为 `None`，按 `tapper-persona-v1` 解释，且不进入 `material()` 以保持旧摘要不变）。trace 属性 `tap.persona.version`。
- planner `intent`（spec 4.2）落为新字段 `AnswerPlan.agent_intent ∈ AGENT_INTENTS = ("knowledge_qa", "multi_step_qa", "document_analysis", "insights_analysis", "clarify", "out_of_scope")` 与 `intent_reason`（≤ 300 字符）；现有 9 值 `intent` 仍只驱动回答模板，不改名。trace 属性 `tap.plan.intent`（= `agent_intent`）、`tap.plan.intent_reason`、`tap.plan.answer_intent`（= 旧 `intent`）。
- 关系分析迁为工具：图路径的 `seed_search` 不再执行关系分析（`include_relations=False`）；单次 `AuthorizedRetrieval.answer` / HTTP 直答 / 质量脚本路径保持现状。
- Insights 联合分析在 V1 只以 fake 验证（spec 非目标）。`insights.query` 仅当 Turn 冻结了 `insights_report_refs` 且未带 `insights_query_id`、且 Insights 运行时已配置时可见。I 标签不生成知识引用：被引用的 I 记录进入回答的 `insightsFacts`，句中 `[I<n>]` 标记从正文与 claim 中移除（见 Task 8）。
- 契约变化（改后运行 `make contracts` 并提交生成物）：SSE 新增 `task.step`；`retrieval.hits_ready` 载荷增加可选 `stepId`；`RetrievalAnswerResponse` 增加 `answerKind`、`insightsFacts`；`ConversationCreateRequest` 增加 `insightsReportRefs`；`ToolDescriptor` 增加 `defaultEnabled`。不新增 HTTP 接口。
- 不需要数据库迁移（Turn 输入快照与事件为 JSON）；若实现中发现确需迁移，编号为 `0036_*`，`down_revision = "0035_skills_agents_authoring"`。
- 测试设计生成（`InteractionGraph`、`modules/test_management/`）不动；不新增依赖；Ruff、mypy、`tests/architecture/` 通过；不新增 ADR（工具注册表扩展已由 ADR-032“新工具通过新增 `ToolDefinition` 接入”覆盖，循环剖面由 ADR-029 覆盖）。
- 后端测试命令（仓库根目录）：`uv run --project apps/tap-ai-backend pytest <path> -q`；MySQL 隔离集成测试一次一个文件：`TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest <file> -q`；前端：`pnpm --filter @tap/ai-frontend exec vitest run <path>`。
- 子代理**不得**运行 `make test`（会写到项目外的 `127.0.0.1:3306`），改跑后端 `tests/unit tests/contract tests/architecture` 与上面的单文件集成测试、前端 vitest；`make demo-e2e` 只由主会话运行；任何人不得运行 `make demo-up` / `demo-down` / `demo-reset`；不得使用 `git stash`。
- 确定性假网关（`apps/tap-ai-backend/src/tap/testing/deterministic_model_gateway.py`）必须支持 E2E 需要的全部 tool calling 行为（Task 12）；真实模型检查一律 opt-in，由用户执行。
- 提交信息为简短小写祈使句 Conventional Commit，正文结尾附 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`；不在仓库文档或代码中写入客户公司名。

## Review Focus

1. Worker 在 `tools` 节点完成、`model` 尚未完成时崩溃后续跑：已执行的工具调用不重跑、`task.step` 不重复、证据标签不变。→ Task 9 `test_resume_after_tools_node_does_not_repeat_tool_calls_or_steps`。
2. 模型在同一轮先输出一段文字再发 `tool_calls`：这段文字不得留在草稿里，更不得进入最终回答。→ Task 9 `test_tool_round_preamble_is_discarded_from_draft`。
3. 工具参数试图扩大范围（未选来源的 `sourceIds`、Agent 白名单外的工具、未知函数名、单轮超过 3 个调用）：不执行、回灌拒绝、发 `rejected`，回合照常计数。→ Task 2 `test_validate_tool_call_*`、Task 9 `test_disallowed_tool_call_is_rejected_without_execution`。
4. 达到 `maxToolRounds` 后模型仍想调工具：最后一轮不提供工具，给出回答，Turn 不失败。→ Task 9 `test_final_round_offers_no_tools_after_max_rounds`。
5. 证据池已满或重复命中：`knowledge.search` 不新增、不重排标签，状态 `completed` 且摘要说明“证据已满/无新证据”。→ Task 6 `test_search_more_never_relabels_and_caps_pool`。

---

### Task 1: `TapperPersona` 版本化、Turn 冻结与 trace

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/ai/domain/persona.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py`（删除两个字符串常量，`chat()` / `answer()` 增加 `persona` 参数）
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/domain/conversations.py`（`TurnInput.persona_version`、`material()`）
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/adapters/mysql_conversations.py`（读回 `persona_version`）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/routes/conversations.py`（`_input` 写入当前版本）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py`、`apps/tap-ai-backend/src/tap/modules/knowledge/application/retrieve.py`（把 `persona` 从 frozen input 传到 `_generate_from_bundle` 与直答 `chat`）
- Test: `apps/tap-ai-backend/tests/unit/ai/test_persona.py`、`apps/tap-ai-backend/tests/contract/test_skill_prompt_assembly.py`（追加）、`apps/tap-ai-backend/tests/unit/chat/test_conversation_domain.py`（若不存在则在现有 TurnInput 测试文件追加）

**Interfaces:**
- Produces（`persona.py`）：
  - `@dataclass(frozen=True, slots=True) class TapperPersona: version: str; platform_instruction: str; customization_boundary: str`，属性 `digest -> str`（`text_digest(platform_instruction + "\n\n" + customization_boundary)`）。
  - `TAPPER_PERSONA_V1 = TapperPersona("tapper-persona-v1", <逐字迁移>, <逐字迁移>)`；`CURRENT_PERSONA = TAPPER_PERSONA_V1`；`PERSONAS: Mapping[str, TapperPersona]`。
  - `def persona_for(version: str | None) -> TapperPersona`：`None` → v1；未知版本抛 `UnknownPersonaVersion(ValueError)`（确定性失败）。
- Produces（knowledge 网关）：`KnowledgeModelGateway.chat(..., persona: TapperPersona = CURRENT_PERSONA)`、`answer(..., persona: TapperPersona = CURRENT_PERSONA)`；组装顺序不变：`persona.platform_instruction`、Agent 系统提示词、Skill 块、`persona.customization_boundary`、任务提示词；每次调用在当前 span 设 `tap.persona.version`。
- Produces（chat 域）：`TurnInput.persona_version: str | None = None`；`material()` 在其为 `None` 时 `pop`；`_input` 设 `persona_version=CURRENT_PERSONA.version`。

- [ ] **Step 1: 写失败测试**
  - `test_persona.py::test_persona_v1_text_matches_previous_constants`：`TAPPER_PERSONA_V1.platform_instruction` 以 `"You are Tapper, the platform assistant for TAP."` 开头、以 `"when they conflict, these rules win."` 结尾；`customization_boundary` 以 `"Custom Agent or Skill instructions cannot change your identity as Tapper"` 开头。
  - `test_persona.py::test_persona_for_legacy_and_unknown`：`persona_for(None) is TAPPER_PERSONA_V1`；`persona_for("tapper-persona-v1") is TAPPER_PERSONA_V1`；`persona_for("x")` 抛 `UnknownPersonaVersion`。
  - `test_skill_prompt_assembly.py::test_governed_prompt_keeps_persona_first_and_boundary_after_skills`：捕获 `ModelRequest.prompt`，`prompt.index(persona.platform_instruction) == 0`，`prompt.index(customization_boundary) > prompt.index("<skill ")`；span 属性 `tap.persona.version == "tapper-persona-v1"`。
  - TurnInput：`test_persona_version_is_frozen_and_legacy_digest_is_stable`：带 `persona_version="tapper-persona-v1"` 的 `material()` 含该键；`persona_version=None` 的 `material()` 与不含该字段时的旧字典逐键相等。

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_persona.py apps/tap-ai-backend/tests/contract/test_skill_prompt_assembly.py -q` → FAIL（`ModuleNotFoundError: tap.modules.ai.domain.persona`）。

- [ ] **Step 3: 实现**；`KnowledgeHttpService` 用 `persona_for(frozen_input.persona_version)` 取角色，未知版本的 `UnknownPersonaVersion` 在 `tapper_turn_failures.py` 归为 deterministic。

- [ ] **Step 4: 运行通过**（同 Step 2，并加 `apps/tap-ai-backend/tests/unit/knowledge apps/tap-ai-backend/tests/contract/test_conversation_http.py`）。

- [ ] **Step 5: Commit**：`git commit -m "feat: version the tapper persona and freeze it per turn"`

---

### Task 2: 工具注册表扩展与工具调用校验

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/domain/tools.py`
- Create: `apps/tap-ai-backend/src/tap/modules/ai/domain/tool_calls.py`
- Modify: `apps/tap-ai-backend/src/tap/contracts/http.py`（`ToolDescriptor.default_enabled`）、`apps/tap-ai-backend/src/tap/interfaces/http/routes/ai_assets.py`
- Test: `apps/tap-ai-backend/tests/unit/ai/test_tool_registry.py`（追加）、`apps/tap-ai-backend/tests/unit/ai/test_tool_calls.py`

**Interfaces:**
- Produces（`tools.py`）：`ToolDefinition` 新增 `wire_name: str`、`input_schema: Mapping[str, object]`、`model_callable: bool`、`default_enabled: bool`；注册表顺序 `knowledge.search`、`knowledge.answer`、`graph.relations`、`insights.query`（取值见 Global Constraints；`knowledge.answer` 的 `input_schema = {}`）。`known_tool_ids()`、`required_tool_ids(task)` 语义不变（`required_tool_ids("knowledge.answer") == {"knowledge.search", "knowledge.answer"}`）。新增：
  - `def tool_for_wire_name(name: str) -> ToolDefinition | None`
  - `def visible_tool_ids(*, agent_allowlist: frozenset[str] | None, relations_available: bool, insights_available: bool) -> tuple[str, ...]`：只含 `model_callable` 工具，按注册表顺序；按可用性剔除 `graph.relations` / `insights.query`；`agent_allowlist` 非 `None` 时再与之求交。
  - `input_schema`：`knowledge.search = {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500}, "sourceIds": {"type": "array", "maxItems": 20, "items": {"type": "string", "minLength": 1, "maxLength": 128}}}}`；`graph.relations` 同上但只有 `query`；`insights.query` 为 `StructuredInsightsTool` 闭合字段（`metricIds`、`filters`、`from`、`to`、`timezone`、`asOf`）对应的 JSON Schema。
- Produces（`tool_calls.py`，纯函数）：
  - `MAX_TOOL_CALLS_PER_ROUND = 3`
  - `@dataclass(frozen=True, slots=True) class RequestedToolCall: call_id: str; name: str; arguments_json: str`（`name` 为模型返回的线上函数名）
  - `@dataclass(frozen=True, slots=True) class AdmittedToolCall: call_id: str; tool_id: str; arguments: Mapping[str, object]`
  - `@dataclass(frozen=True, slots=True) class RejectedToolCall: call_id: str; name: str; reason: Literal["unknown-tool", "tool-not-visible", "invalid-arguments", "source-out-of-scope", "too-many-calls"]`
  - `def admit_tool_calls(calls: Sequence[RequestedToolCall], *, visible: tuple[str, ...], selected_source_ids: frozenset[str]) -> tuple[AdmittedToolCall | RejectedToolCall, ...]`：顺序保持；第 4 个起 `too-many-calls`；JSON 解析失败或不满足 `input_schema`（用 `tap.modules.ai.application.schema.validate_output` 等价的纯校验——若该函数在 application 层，则把其纯校验部分移到 domain 或在 domain 内实现同等的最小校验，不引入依赖）为 `invalid-arguments`；`sourceIds` 不 ⊆ `selected_source_ids` 为 `source-out-of-scope`。
  - `def step_id(turn_id: str, round_number: int, index: int) -> str` → `f"{turn_id}:r{round_number}:c{index}"`。
- Produces（HTTP）：`ToolDescriptor.default_enabled: StrictBool`，`GET /ai/tools` 按注册表返回四项。

- [ ] **Step 1: 写失败测试**
  - `test_tool_registry.py::test_registry_has_four_tools_with_wire_names`：`list(TOOL_REGISTRY) == ["knowledge.search", "knowledge.answer", "graph.relations", "insights.query"]`；wire 名如上；`TOOL_REGISTRY["knowledge.answer"].model_callable is False`；`required_tool_ids("knowledge.answer") == {"knowledge.search", "knowledge.answer"}`。
  - `test_tool_registry.py::test_visible_tools_respect_agent_allowlist_and_availability`：`visible_tool_ids(agent_allowlist=None, relations_available=True, insights_available=False) == ("knowledge.search", "graph.relations")`；`agent_allowlist=frozenset({"knowledge.search", "knowledge.answer"})` 时为 `("knowledge.search",)`；`relations_available=False` 时不含 `graph.relations`。
  - `test_tool_calls.py::test_validate_tool_call_rejects_out_of_scope_sources`：`sourceIds: ["src-x"]`、`selected_source_ids={"src-a"}` → `RejectedToolCall(reason="source-out-of-scope")`。
  - `test_validate_tool_call_rejects_invisible_unknown_and_malformed`：`graph_relations` 不在 `visible` → `tool-not-visible`；`delete_all` → `unknown-tool`；`arguments_json="{"` 或 `{"query": ""}` 或多余键 → `invalid-arguments`。
  - `test_validate_tool_call_caps_calls_per_round`：4 个合法调用 → 前 3 个 `AdmittedToolCall`，第 4 个 `too-many-calls`。
  - `test_step_id_format`：`step_id("t1", 2, 0) == "t1:r2:c0"`。
  - 追加 `tests/contract/test_ai_assets_http.py`（或现有 `/ai/tools` 契约测试文件）：返回四项，`graph.relations` 的 `required is False`、`defaultEnabled is True`。

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_tool_registry.py apps/tap-ai-backend/tests/unit/ai/test_tool_calls.py -q` → FAIL。

- [ ] **Step 3: 实现**；确认 `agent_authoring.py` 的 `required <= tool_ids <= known_tool_ids()` 校验无需改动即接受新工具；在仓库根目录运行 `make contracts`（`ToolDescriptor.defaultEnabled`）。

- [ ] **Step 4: 运行通过**（同 Step 2，另跑 `apps/tap-ai-backend/tests/unit/ai apps/tap-ai-backend/tests/architecture -q`）。

- [ ] **Step 5: Commit**：`git commit -m "feat: register relation and insights tools with argument admission"`

---

### Task 3: `ModelGateway` 原生 tool calling

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/domain/models.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/adapters/litellm.py`（`_validate`、`_prepare`、`_normalize`、`chat_stream`、记录内容）
- Test: `apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py`（追加）

**Interfaces:**
- Produces（`models.py`）：
  - `@dataclass(frozen=True, slots=True) class ModelToolSpec: tool_id: str; wire_name: str; description: str; parameters: Mapping[str, object]`；`def model_tool_specs(tool_ids: Sequence[str]) -> tuple[ModelToolSpec, ...]`（从注册表构造，`description` 用 `en` 描述）。
  - `@dataclass(frozen=True, slots=True) class ModelToolCall: call_id: str; name: str; arguments_json: str`
  - `@dataclass(frozen=True, slots=True) class ModelMessage: role: Literal["assistant", "tool"]; content: str = ""; tool_calls: tuple[ModelToolCall, ...] = (); tool_call_id: str | None = None`
  - `ModelRequest` 新增 `tools: tuple[ModelToolSpec, ...] = ()`、`messages: tuple[ModelMessage, ...] = ()`；`ModelResult` 新增 `tool_calls: tuple[ModelToolCall, ...] = ()`。
- Produces（网关）：
  - `_validate`：`tools` 只允许 `ModelOperation.CHAT`；每项 `tool_id` 必须是 `model_callable` 注册工具且 `wire_name` 一致；`request.tool_allowlist` 非空（受治理）时 `tool_id ⊆ tool_allowlist`；`messages` 中每条 `content` 与 `arguments_json` 经 `_check_redacted`，总大小计入 262144 字节上限；`messages` 非空要求 `tools` 非空。否则 `ModelGatewayRejected`。
  - `_prepare`：`payload["tools"] = [{"type": "function", "function": {"name": wire_name, "description": ..., "parameters": ...}}]`、`payload["tool_choice"] = "auto"`；`messages` 追加在 user 之后（assistant 带 `tool_calls`，tool 带 `tool_call_id`）。
  - `_normalize`（非流式）与 `chat_stream`（流式，按 `delta.tool_calls[i].index` 累积 `id`、`function.name`、`function.arguments` 片段）：`finish_reason == "tool_calls"` 且请求带 `tools` 时返回 `ModelResult(output=<已输出文本，可为空>, tool_calls=...)`；请求不带 `tools` 却收到 `tool_calls` 仍为 `ModelGatewayUnavailable`（现状）；`refusal` 仍拒绝。流式路径在 tool 轮仍会产出已收到的文本 delta（由调用方决定丢弃）。
  - `ModelCallRecord` 的响应文本在 tool 轮为 `json.dumps([{"name", "arguments"}...])`；计量与成本不变（一次请求计一次调用）。

- [ ] **Step 1: 写失败测试**（沿用该文件 `httpx.MockTransport` 与 fake SSE 行构造方式）
  - `test_chat_stream_accumulates_tool_call_fragments`：SSE 依次给出 `tool_calls[0]` 的 `id="c1"`、`name="knowledge_search"`、`arguments` 片段 `'{"que'`、`'ry":"理赔"}'`，`finish_reason="tool_calls"`，usage 在末块；最终 `ModelResult.tool_calls == (ModelToolCall("c1", "knowledge_search", '{"query":"理赔"}'),)`，`usage.input_tokens` 等于末块值。
  - `test_chat_with_tools_sends_function_specs_and_transcript`：捕获请求体，`body["tools"][0]["function"]["name"] == "knowledge_search"`，`body["tool_choice"] == "auto"`，`body["messages"][2]["role"] == "assistant"`、`[3]["role"] == "tool"` 且 `tool_call_id == "c1"`。
  - `test_non_streaming_chat_returns_tool_calls`：`choices[0].finish_reason == "tool_calls"`、`message.content is None` → 返回 `tool_calls`，不抛错。
  - `test_tool_calls_without_offered_tools_are_unavailable`：请求无 `tools`、响应含 `tool_calls` → `ModelGatewayUnavailable`。
  - `test_governed_request_rejects_tool_outside_allowlist`：`tool_allowlist={"knowledge.search", "knowledge.answer"}` 且 `tools` 含 `graph.relations` → `ModelGatewayRejected`，MockTransport 未收到请求。
  - `test_unredacted_tool_message_is_rejected`：`messages` 中 tool 内容含 `api_key=abc` → `ModelGatewayRejected`。

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py -q` → FAIL。

- [ ] **Step 3: 实现**。

- [ ] **Step 4: 运行通过**（同 Step 2，另跑 `apps/tap-ai-backend/tests/architecture/test_model_gateway_composition.py`）。

- [ ] **Step 5: Commit**：`git commit -m "feat: support native tool calling in the model gateway"`

---

### Task 4: planner `intent` 与理由

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/domain/answer_plan.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/plan_answer.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/adapters/model_gateway_planner.py`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_chat_turn.py`（`guard_in` 的 trace 属性）
- Test: `apps/tap-ai-backend/tests/unit/chat/test_answer_planning.py`（追加）、`apps/tap-ai-backend/tests/unit/entrypoints/test_chat_turn_graph.py`（追加）

**Interfaces:**
- Produces（`answer_plan.py`）：`AGENT_INTENTS` 常量（见 Global Constraints）；`AnswerPlan` 新增 `agent_intent: str = "knowledge_qa"`、`intent_reason: str = ""`；`__post_init__` 校验 `agent_intent in AGENT_INTENTS`、`len(intent_reason) <= 300`、`(agent_intent == "out_of_scope") == (intent == "out_of_scope")`。`def derive_agent_intent(intent: str, route: str, query_count: int) -> str`：`out_of_scope`→`out_of_scope`；`route == "clarify"`→`clarify`；`route == "insights"`→`insights_analysis`；`route == "file_analysis"`→`document_analysis`；`intent == "comparison"` 或 `query_count > 1`→`multi_step_qa`；否则 `knowledge_qa`。
- Produces（planner）：`PLANNER_SCHEMA` 增加 `"title": "AnswerPlan"`、必需字段 `agent_intent`（enum `AGENT_INTENTS`）与 `intent_reason`（`maxLength: 300`）；`PLANNER_PROMPT` 末尾加一句：`"Also classify agent_intent for observability only (it never limits tools): knowledge_qa, multi_step_qa, document_analysis, insights_analysis, clarify or out_of_scope, with a one-sentence intent_reason."`。`AnswerPlanner.plan`：确定性捷径（问候/改写 `direct`、`insights`/`file_analysis` 能力路由、`clarify`）不调模型、用 `derive_agent_intent`；其余 `retrieve`/`graph` 路由在有模型时**每轮**调用模型（去掉 `complex_query` 条件）；模型结果缺字段或不一致按现有方式降级，降级后 `agent_intent = derive_agent_intent(...)`、`intent_reason = ""`。
- Produces（`guard_in`）：在 `turn_span` 设 `tap.plan.intent`、`tap.plan.intent_reason`（非空时）、`tap.plan.answer_intent`；`out_of_scope` 路由逻辑不变（以 `plan.intent == "out_of_scope"` 判定，二者已强制一致）。

- [ ] **Step 1: 写失败测试**
  - `test_model_plan_carries_agent_intent_and_reason`：替身模型返回合法规划加 `agent_intent="multi_step_qa"`、`intent_reason="needs two lookups"` → plan 对应字段相等。
  - `test_simple_question_now_consults_the_model_planner`：非比较类问题 `"出险后几天内报案？"`、`source_ids` 非空 → 替身模型被调用 1 次。
  - `test_deterministic_shortcuts_skip_model_and_derive_intent`：`"你好"` → 模型未调用、`agent_intent == "knowledge_qa"`、`route == "direct"`；缺来源 → `agent_intent == "clarify"`。
  - `test_agent_intent_out_of_scope_must_pair_with_legacy_intent`：模型返回 `intent="factual_lookup"`、`agent_intent="out_of_scope"` → 降级（`degradation_reason == "invalid-plan"`），`agent_intent != "out_of_scope"`。
  - `test_chat_turn_graph.py::test_guard_in_records_plan_intent_on_turn_span`：span 属性 `tap.plan.intent == "multi_step_qa"`、`tap.plan.answer_intent == "comparison"`。

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/chat/test_answer_planning.py apps/tap-ai-backend/tests/unit/entrypoints/test_chat_turn_graph.py -q` → FAIL。

- [ ] **Step 3: 实现**；更新既有断言“简单问题不调模型”的用例为新行为。

- [ ] **Step 4: 运行通过**（同 Step 2，另跑 `TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_answer_plan_execution.py -q`）。

- [ ] **Step 5: Commit**：`git commit -m "feat: classify agent intent in the planner for every turn"`

---

### Task 5: 澄清回复与 `answerKind`

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/ports/models.py`（`AnswerGeneration.answer_kind`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/domain/models.py`（`AnswerResponse.answer_kind`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py`（`_CLARIFY_PROMPT`、前缀解析）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/retrieve.py`（`verify_answer` 短路）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py`（`_stopped_answer` 的 clarify 分支）与 HTTP 映射
- Modify: `apps/tap-ai-backend/src/tap/contracts/http.py`（`RetrievalAnswerResponse.answer_kind`）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_draft_batcher.py`（剥离前导 `CLARIFY:`）
- Regenerate: `make contracts`
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_inline_citations.py`、`apps/tap-ai-backend/tests/unit/knowledge/test_retrieval_answer_phases.py`、`apps/tap-ai-backend/tests/unit/entrypoints/test_draft_batcher.py`（均追加）

**Interfaces:**
- Produces：`AnswerGeneration.answer_kind: Literal["answer", "clarification"] = "answer"`；`AnswerResponse.answer_kind` 同；`RetrievalAnswerResponse.answer_kind: Literal["answer", "clarification"] = "answer"`（JSON `answerKind`）。
- `_CLARIFY_PROMPT = "If the question is ambiguous or lacks information you need and the evidence cannot resolve it, do not answer; instead reply with one line that starts with CLARIFY: followed by a short question to the user. A clarification states no facts and cites nothing."`，追加到 `_ANSWER_PROMPT` 与 `_GOVERNED_ANSWER_PROMPT` 组装之后（两条路径都加）。
- `KnowledgeModelGateway.answer`：`raw_text.lstrip().startswith("CLARIFY:")` 时返回 `AnswerGeneration(text=<去前缀并 strip 的正文>, claims=(), answer_kind="clarification", ...)`，不调用 `parse_inline_answer`；正文为空时按无依据处理（`""`、`()`）。
- `AuthorizedRetrieval.verify_answer`：在 flowchart 检查之前，`generation.answer_kind == "clarification"` 时复检授权与发布后直接返回 `AnswerResponse(answer=text, abstained=False, claims=(), citations=(), answer_kind="clarification", ...)`（其余字段与正常回答一致）。
- `KnowledgeHttpService._stopped_answer`：`plan.route == "clarify"` 时 `answer_kind="clarification"`。
- `DraftBatcher._flush`：`visible_raw` 去掉前导空白后的 `CLARIFY:` 前缀（只在开头）。

- [ ] **Step 1: 写失败测试**
  - `test_inline_citations.py::test_clarify_prefix_yields_clarification_generation`：流式输出 `"  CLARIFY: 你指的是哪个产品版本？"` → `answer_kind == "clarification"`、`text == "你指的是哪个产品版本？"`、`claims == ()`。
  - `test_clarify_marker_mid_text_is_ordinary_text`：`"保单生效[S1]。CLARIFY: x"` → `answer_kind == "answer"`。
  - `test_retrieval_answer_phases.py::test_verify_passes_clarification_without_claims`：`verify_answer` 返回 `abstained is False`、`claims == ()`、`answer_kind == "clarification"`。
  - `test_draft_batcher.py::test_leading_clarify_prefix_never_reaches_redis`：推入 `"CLARIFY: 哪个版本？"` 后 `close()`，Redis 草稿拼接为 `"哪个版本？"`（允许前导空格被去掉）。
  - 契约：`test_conversation_http.py` 或 `test_public_retrieval_contract_strict.py` 追加 `answerKind` 默认 `"answer"`。

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge/test_inline_citations.py apps/tap-ai-backend/tests/unit/knowledge/test_retrieval_answer_phases.py apps/tap-ai-backend/tests/unit/entrypoints/test_draft_batcher.py -q` → FAIL。

- [ ] **Step 3: 实现**，然后在仓库根目录运行 `make contracts`。

- [ ] **Step 4: 运行通过**（同 Step 2，另跑 `apps/tap-ai-backend/tests/contract -q`）。

- [ ] **Step 5: Commit**：`git commit -m "feat: let the model reply with a citation-free clarification"`

---

### Task 6: 知识侧证据池工具（`knowledge.search`、`graph.relations`）

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/retrieve.py`（`prepare_answer(include_relations=...)`、新增两方法）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/relation_analysis.py`（`merge_relation_contexts`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/evidence_bundle.py`（`EvidenceBundle.insights_records` 默认空元组，Task 8 使用）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/api.py` 与 `KnowledgeAPI` 的 frozen 包装（`search_more_frozen`、`relations_frozen`）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py`（`prepare_conversation_answer(..., include_relations=True)`、`run_conversation_tool`、`relations_available`）
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/ports.py`（`ConversationAnswerStep` 新字段、协议新方法）
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_retrieval_answer_phases.py`、`apps/tap-ai-backend/tests/unit/knowledge/test_relation_analysis.py`、`apps/tap-ai-backend/tests/unit/knowledge/test_conversation_answer_steps.py`（均追加）

**Interfaces:**
- Consumes: Task 2 `AdmittedToolCall`。
- Produces（retrieve）：
  - `prepare_answer(..., include_relations: bool = True)`：`False` 时跳过 `self._relation_analysis.analyse` 与 `_augment_from_relations`（冲突来源检查照常）。
  - `async def search_more(self, bundle: EvidenceBundle, query: str, source_ids: tuple[str, ...], policy, *, frozen_policy: bool = False, authorize=None) -> tuple[EvidenceBundle, tuple[Evidence, ...]]`：以 `bundle.request` 替换 `query`（并把资源限制为 `source_ids` 与 `bundle.plan.resources` 的交集；空 `source_ids` 表示全部已选）调 `_retrieve`；去重后按 `S{n+1}` 追加，总数 ≤ 20；有发布授权时用 `authorize_selection` 重算 `publication`（同 flowchart 规则合并分支）；返回新 bundle 与新增条目。
  - `async def analyse_relations(self, bundle: EvidenceBundle, query: str, *, authorize=None, turn_id: str | None = None) -> tuple[EvidenceBundle, tuple[RelationEvidence, ...]]`：`self._relation_analysis is None` 抛 `RelationAnalysisUnavailable(RuntimeError)`；对当前池证据运行分析，`merge_relation_contexts(bundle.relation, new)`；`self._retrieval_augment` 为真时复用 `_augment_from_relations` 追加支撑片段（同样接续 S 标签、≤ 20）。
- Produces（relation_analysis）：`def merge_relation_contexts(current: RelationContext | None, new: RelationContext) -> RelationContext`：`current is None` 返回 `new`；`graph_version` 不同抛 `RelationGraphChanged(ValueError)`；按边 ID 去重，新关系接续 `R{max+1}`，≤ 20；`seeds`、`paths`、`augment_chunks` 合并去重并按新标签重绑。
- Produces（端口）：`ConversationAnswerStep` 新增 `kind` 取值 `"tool_calls"`、字段 `transcript: list[dict[str, object]]`、`pending_tool_calls: list[dict[str, str]]`（`callId`/`name`/`argumentsJson`）。`ConversationAnswerSteps` 新增：
  - `async def run_conversation_tool(self, step, frozen_input, call: AdmittedToolCall, *, answer_plan=None, authorize=None) -> ConversationToolResult`
  - `class ConversationToolResult(TypedDict): step: ConversationAnswerStep; status: Literal["completed", "failed"]; summary: str; added_labels: list[str]; retrieval_hits: dict[str, object] | None`
  - `KnowledgeHttpService.relations_available: bool`（底层 `AuthorizedRetrieval` 配有关系分析时为真）。
- `run_conversation_tool` 规则：只处理 `knowledge.search` / `graph.relations`（其他 tool_id 抛 `ValueError`）；先 `_authorized_frozen_context` 复检；`SearchUnavailable` / `ModelUnavailable` 原样抛出（瞬时，PR 2 重试）；`RelationAnalysisUnavailable`、`RelationGraphChanged`、关系结果非 `APPLIED` → `status="failed"`、池不变；成功时 `summary` 形如 `"knowledge.search “<query 前 60 字>” → +3 条证据（S5–S7）"`（池满或无新增时 `"→ 无新证据"`），`retrieval_hits` 用 `_retrieval_hits_payload` 只含新增标签；返回的 step 为 `kind="prepared"`、`bundle` 为新编码（超 512KB 抛 `EvidenceBundleTooLarge`）、`transcript` 原样保留。
- 图路径约定：`ChatTurnNodes.seed_search` 调 `prepare_conversation_answer(..., include_relations=False)`（Task 9 接线）；`prepared` step 带 `transcript: []`。

- [ ] **Step 1: 写失败测试**
  - `test_retrieval_answer_phases.py::test_prepare_without_relations_skips_relation_analysis`：替身 `RelationAnalysis` 计数为 0，`bundle.relation is None`。
  - `test_search_more_never_relabels_and_caps_pool`：初始 18 条（S1–S18），新检索返回 5 条（其中 1 条与 S3 同 chunk）→ 新增恰好 2 条，标签 `S19`、`S20`，原 S1–S18 内容与标签不变；再次调用新增 `()`。
  - `test_search_more_restricts_to_requested_sources`：`source_ids=("src-b",)` 时传给 `SearchPort` 的资源只含 `src-b`。
  - `test_analyse_relations_merges_and_continues_r_labels`：已有 R1–R2，新分析返回含与 R2 同边 ID 的 3 条 → 合并后 `R1..R4`，R1/R2 不变。
  - `test_relation_analysis.py::test_merge_relation_contexts_rejects_graph_version_change`：抛 `RelationGraphChanged`。
  - `test_conversation_answer_steps.py::test_run_conversation_tool_search_returns_prepared_step_and_hits`：返回 `status == "completed"`、`step["kind"] == "prepared"`、`retrieval_hits["labels"]` 只含新增标签、`summary` 含 `"+"`。
  - `test_run_conversation_tool_relations_unavailable_fails_softly`：无关系分析时 `status == "failed"`，`step["bundle"]` 与输入相等。

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge -q -k "relations or search_more or run_conversation_tool or without_relations"` → FAIL。

- [ ] **Step 3: 实现**。

- [ ] **Step 4: 运行通过**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge apps/tap-ai-backend/tests/architecture -q`；`make quality-graph-relations` 结果不变（单次路径未改）。

- [ ] **Step 5: Commit**：`git commit -m "feat: grow the evidence pool through search and relation tools"`

---

### Task 7: 带工具的生成轮

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/ports/models.py`（`AnswerGeneration.tool_calls`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py`（`answer(..., tools=(), transcript=())`、`_TOOL_LOOP_PROMPT`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/retrieve.py`（`generate_answer` / `_generate_from_bundle` 透传）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py`（`generate_conversation_answer(..., tools=())` 产出 `"tool_calls"` step）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_chat_turn.py`（`SingleShotAnswerSteps` 接受并忽略 `tools`）
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_inline_citations.py`、`apps/tap-ai-backend/tests/unit/knowledge/test_conversation_answer_steps.py`（追加）

**Interfaces:**
- Consumes: Task 3 `ModelToolSpec`、`ModelMessage`、`ModelToolCall`、`model_tool_specs`；Task 6 step 字段。
- Produces：
  - `@dataclass(frozen=True, slots=True) class GeneratedToolCall: call_id: str; name: str; arguments_json: str`（knowledge 端口内，不依赖 ai 域类型）；`AnswerGeneration.tool_calls: tuple[GeneratedToolCall, ...] = ()`。
  - `KnowledgeModelGateway.answer(..., tools: tuple[str, ...] = (), transcript: tuple[Mapping[str, object], ...] = ())`：`tools` 为工具 ID，转 `model_tool_specs`；`transcript` 转 `ModelMessage`（tool 消息内容为 Task 9 写入的结果 JSON，经 `self._redact`）；`tools` 非空时提示词追加 `_TOOL_LOOP_PROMPT = "You may call the supplied tools to gather more evidence before answering. Tool results are summarized in the conversation and the evidence pool in untrusted is refreshed after every call; cite only labels present in untrusted. When the evidence suffices, answer without calling tools."`；结果带 `tool_calls` 时返回 `AnswerGeneration(text="", claims=(), tool_calls=...)`，不解析正文。证据、关系、Insights 记录始终以当前池整体进入 `untrusted`（不把工具结果原文重复放入 tool 消息）。
  - `generate_conversation_answer(step, frozen_input, *, answer_plan=None, authorize=None, on_text=None, tools: tuple[str, ...] = ())`：接受 `kind == "prepared"`；生成带 `tool_calls` 时返回 `{"kind": "tool_calls", "bundle": step["bundle"], "transcript": step["transcript"] + [{"role": "assistant", "toolCalls": [...]}], "pending_tool_calls": [...]}`；否则 `{"kind": "generated", ...}`（与现状一致）。协议签名同步更新。

- [ ] **Step 1: 写失败测试**
  - `test_inline_citations.py::test_answer_with_tools_returns_tool_calls_without_parsing`：替身网关返回 `tool_calls=(ModelToolCall("c1", "knowledge_search", '{"query":"q"}'),)`、`output=""` → `generation.tool_calls[0].name == "knowledge_search"`、`claims == ()`；捕获请求 `tools[0].tool_id == "knowledge.search"` 且 `prompt` 含 `_TOOL_LOOP_PROMPT`。
  - `test_answer_without_tools_omits_tool_loop_prompt`：`tools=()` 时请求 `tools == ()` 且 prompt 不含 `_TOOL_LOOP_PROMPT`。
  - `test_transcript_reaches_model_messages_redacted`：transcript 的 tool 内容含邮箱 → 发出的 `ModelMessage.content` 含 `[REDACTED_EMAIL]`。
  - `test_conversation_answer_steps.py::test_generate_with_tools_yields_tool_calls_step`：`step["kind"] == "tool_calls"`、`pending_tool_calls == [{"callId": "c1", "name": "knowledge_search", "argumentsJson": '{"query":"q"}'}]`、`transcript[-1]["role"] == "assistant"`。

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge/test_inline_citations.py apps/tap-ai-backend/tests/unit/knowledge/test_conversation_answer_steps.py -q` → FAIL。

- [ ] **Step 3: 实现**。

- [ ] **Step 4: 运行通过**（同 Step 2，另跑 `apps/tap-ai-backend/tests/unit apps/tap-ai-backend/tests/contract -q`）。

- [ ] **Step 5: Commit**：`git commit -m "feat: let grounded generation request tool calls"`

---

### Task 8: `insights.query` 联合分析（fake）

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/domain/conversations.py`（放宽“报告引用必须配历史查询”；`material()` 只在两者都为空时去掉键）
- Modify: `apps/tap-ai-backend/src/tap/contracts/http.py`（`ConversationCreateRequest.insights_report_refs`、`InsightsFactView`、`RetrievalAnswerResponse.insights_facts`）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/routes/conversations.py`（`_input` 冻结 refs；`_matches_replay` 比较 refs）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/insights_explanation_runtime.py`（`query_metrics`）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py`（`add_insights_records`）、`apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py`（`untrusted.insights`、I 标签）、`apps/tap-ai-backend/src/tap/modules/knowledge/application/retrieve.py`（`verify_answer` 处理 I 标签）
- Regenerate: `make contracts`
- Test: `apps/tap-ai-backend/tests/contract/test_insights_explanation_runtime.py`、`apps/tap-ai-backend/tests/unit/knowledge/test_retrieval_answer_phases.py`、`apps/tap-ai-backend/tests/contract/test_conversation_http.py`（均追加）

**Interfaces:**
- Produces：
  - `TurnInput`：`insights_query_id is None` 时允许 `insights_report_refs`（仍 ≤ 20、唯一、非空白）；`ConversationCreateRequest.insights_report_refs: Annotated[list[str], Field(max_length=20)] = []`；Insights 运行时未配置而请求带 refs → 503（沿用 `KnowledgeRuntimeUnavailable`）。
  - `ConfiguredInsightsExplanation.query_metrics(context: ProjectScopeContext, report_refs: tuple[str, ...], arguments: Mapping[str, object]) -> tuple[dict[str, object], ...]`：以 `authorized_resource_refs=report_refs` 构造 `AuthorizedInsightsScope`，经 `StructuredInsightsTool(self._insights).invoke` 执行；任一 fact 的 `evidence_refs` 不 ⊆ `report_refs` 抛 `InsightsAuthorizationChanged`；返回每个 fact 一条记录 `{"queryId", "metricVersion", "metricId", "value", "asOf"}`（字段取 `MetricResult` / `MetricFact` 现有属性）。30 秒总超时转 `InsightsBudgetExceeded`。
  - `KnowledgeHttpService.add_insights_records(step, records: Sequence[Mapping[str, object]]) -> tuple[ConversationAnswerStep, list[str]]`：按 `(queryId, metricId)` 去重，接续 `I{n}`（≤ 20），写入 `bundle.insights_records`（每条带 `label`）；同签名加入 `ConversationAnswerSteps` 协议。`SingleShotAnswerSteps` 的 `run_conversation_tool`（Task 6）与 `add_insights_records` 抛 `NotImplementedError`（其生成永不产出 `tool_calls`，不可达）。
  - 知识网关：`bundle.insights_records` 非空时作为 `untrusted["insights"]` 进入上下文；`parse_inline_answer(extra_labels=R 标签 ∪ I 标签)`。
  - `verify_answer`：在关系对账前，把 `generation.claims` 中的 I 标签剥离：只引 I 的 claim 不再作为 claim（句子保留）；混引的 claim 去掉 I 标签；正文删除 `[I<n>]` 标记；被引用的 I 记录写入 `AnswerResponse.insights_facts`（按标签顺序）。若剥离后 `generation.text` 非空且没有任何 S/R claim，但有被引用的 I 记录，则不弃答。
  - `class InsightsFactView(ContractModel): label: str; query_id: str; metric_id: str; value: float | int | None; as_of: str`；`RetrievalAnswerResponse.insights_facts: list[InsightsFactView] | None = None`。
  - 工具执行在 `ChatTurnNodes.tools` 中（Task 9）：`InsightsAuthorizationChanged` / `InsightsQueryUnavailable` / `InsightsBudgetExceeded` / `ValueError`（契约校验）一律转 `status="failed"`，摘要 `"insights.query → 不可用"`，池不变。
- 可见性：`insights_available = bool(frozen.insights_report_refs) and frozen.insights_query_id is None and callable(getattr(insights_runtime, "query_metrics", None))`。

- [ ] **Step 1: 写失败测试**
  - `test_insights_explanation_runtime.py::test_query_metrics_scopes_to_turn_report_refs`：fake `InsightsPort` 返回的 fact `evidence_refs` ⊆ refs → 返回记录 `metricId == "first_pass_rate"`；含 refs 外引用 → `InsightsAuthorizationChanged`。
  - `test_retrieval_answer_phases.py::test_insights_labels_become_facts_not_citations`：池含 `I1`，生成 `"首次通过率为 92%[I1]。理赔时限为 10 个工作日[S1]。"` → `claims` 只有 S1 句、`citations` 只含 S1、正文无 `[I1]`、`insights_facts[0].label == "I1"`、`abstained is False`。
  - `test_conversation_http.py::test_create_turn_freezes_insights_report_refs_without_query`：请求带 `insightsReportRefs: ["report-1"]` → 冻结输入 refs 相等、`insights_query_id is None`；Insights 未配置时 503。

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_insights_explanation_runtime.py apps/tap-ai-backend/tests/unit/knowledge/test_retrieval_answer_phases.py apps/tap-ai-backend/tests/contract/test_conversation_http.py -q` → FAIL。

- [ ] **Step 3: 实现**，然后运行 `make contracts`。

- [ ] **Step 4: 运行通过**（同 Step 2；另跑 `TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_insights_generation_persistence.py -q` 确认解释路径不变）。

- [ ] **Step 5: Commit**：`git commit -m "feat: add insights metrics to the agent evidence pool"`

---

### Task 9: 图内 agent 循环：`ingress` 载入角色与 Agent、`model ⇄ tools`、`task.step`

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/application/chat_graph.py`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_chat_turn.py`（`ingress`、`seed_search`、`model`、新 `tools`、`_event_key`）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_draft_batcher.py`（`discard`）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py`（向 `ChatTurnNodes` 传 Insights 运行时用作工具）
- Modify: `apps/tap-ai-backend/src/tap/platform/budget.py`（`TurnBudgetExhausted.reason` 增加 `"tool-rounds"`）
- Modify: `apps/tap-ai-backend/src/tap/contracts/chat_stream.py`（`TaskStepEvent`、`RetrievalHitsReadyPayload.step_id`）
- Regenerate: `make contracts`
- Test: `apps/tap-ai-backend/tests/unit/ai/test_chat_graph.py`、`apps/tap-ai-backend/tests/unit/entrypoints/test_chat_turn_graph.py`、`apps/tap-ai-backend/tests/unit/entrypoints/test_draft_batcher.py`（均追加）

**Interfaces:**
- Consumes: Task 1 `persona_for`；Task 2 `visible_tool_ids`、`admit_tool_calls`、`RequestedToolCall`、`step_id`、`MAX_TOOL_CALLS_PER_ROUND`；Task 6 `run_conversation_tool`、`relations_available`；Task 7 `generate_conversation_answer(tools=...)`；Task 8 `query_metrics`、`add_insights_records`。
- Produces（`chat_graph.py`）：
  - `CHAT_STATE_SCHEMA_VERSION = 3`；`ChatGraphState` 新增 `persona_version: str`、`agent: dict[str, object] | None`（`{"revisionId", "toolAllowlist"}`）、`tools: list[str]`；`ChatGraphNodes` 新增 `tools: GraphNode`（必填，位于 `verify` 之前）。
  - 边：`model → _route_after_model`：`failure` → `finalize`；`state["step"]["kind"] == "tool_calls"` → `tools`；否则 `verify`。`tools → _route_on_failure`：`finalize` / `model`。
  - `_run_budgeted`：`toolRounds = tool_rounds_used + int(result.pop("tool_rounds_delta", 0))`；节点 span 设 `tap.budget.tool_rounds_used`。
  - 运行时 `recursion_limit = 2 * maxToolRounds + 12`（经 `VersionedGraphRunner` 的 config 传入；没有入口则为其 `start` / `resume` 增加 `recursion_limit: int | None = None` 参数）。
- Produces（`ChatTurnNodes`）：
  - `ingress`：写 `persona_version = persona_for(frozen.persona_version).version`、`agent`（无 Agent 为 `None`）、`tools = list(visible_tool_ids(agent_allowlist=None if 无 Agent else frozenset(frozen.agent_tool_allowlist), relations_available=getattr(knowledge, "relations_available", False), insights_available=<Task 8 规则>))`；span 属性 `tap.persona.version`、`tap.agent.revision_id`（有 Agent 时）、`tap.agent.tools`（逗号连接）。已有 `startedAtMs` 的续跑不改写这些键。
  - `seed_search`：调用 `prepare_conversation_answer(..., include_relations=False)`。
  - `model`：`offered = tuple(state["tools"]) if budget_used.toolRounds < budget.maxToolRounds else ()`；调用 `generate_conversation_answer(..., tools=offered)`；结果 `kind == "tool_calls"` 时 `await batcher.discard()`，否则 `await batcher.close()`；`offered == ()` 且 `state["tools"]` 非空时设 span `tap.agent.tool_rounds_exhausted = True`。
  - `tools(state) -> dict[str, object]`：`round = toolRounds + 1`；`round > maxToolRounds` 抛 `TurnBudgetExhausted("tool-rounds")`；`admit_tool_calls(pending, visible=tuple(state["tools"]), selected_source_ids=冻结选择)`；逐个：`_emit_committed(state, "task.step", {stepId, tool, status: "running", summary, durationMs: 0, toolRound: round})` → 执行（知识工具走 `self._steps.run_conversation_tool`；`insights.query` 走 `query_metrics` + `add_insights_records`；被拒的不执行）→ 发终态 `task.step` → 有 `retrieval_hits` 时发带 `stepId` 的 `retrieval.hits_ready`（标签经 `_redact_labels`）。每个调用向 transcript 追加 `{"role": "tool", "toolCallId", "toolId", "content": json.dumps({"status", "summary", "addedLabels"} 或 {"status": "rejected", "reason"})}`。返回 `{"step": {...kind: "prepared", 去掉 pending_tool_calls}, "tool_rounds_delta": 1, "committed_stream_events": ...}`。`AuthorizationDenied`、`PermissionError` 不吞。
  - `_event_key`：载荷含 `stepId` 时键为 `f"{type}:{stepId}:{status}"`（`status` 缺省时省略），否则维持现规则。
- Produces（`DraftBatcher.discard() -> None`）：已向 Redis 发出过文本时追加一条 `reset=True` 空块；清空 `_raw`、`_sent_text`、`_last_redacted`；之后的 `push`/`close` 从零开始。
- Produces（SSE 契约）：`class TaskStepPayload(StreamContractModel): step_id: str; tool: str; status: Literal["running", "completed", "rejected", "failed"]; summary: str = Field(max_length=200); duration_ms: int = Field(ge=0); tool_round: int = Field(ge=1)`；`class TaskStepEvent(StreamContractModel): type: Literal["task.step"]; payload: TaskStepPayload`，并入 `ChatEventEnvelope` 联合；`RetrievalHitsReadyPayload.step_id: str | None = None`。

- [ ] **Step 1: 写失败测试**
  - `test_chat_graph.py`（替身节点 + `InMemorySaver`）：
    - `test_model_tool_calls_route_through_tools_and_back`：替身 `model` 第一次返回 `step.kind="tool_calls"`、第二次 `"generated"` → 调用顺序 `model, tools, model, verify, finalize`，`budget_used["toolRounds"] == 1`。
    - `test_tools_failure_short_circuits_to_finalize`：替身 `tools` 抛 `ValueError` → `failure["node"] == "tools"`，`verify` 未调用。
    - `test_ten_rounds_fit_recursion_limit`：`TurnBudget(max_tool_rounds=10, max_model_calls=50)`，替身 `model` 前 10 次返回 tool_calls → 正常结束，无 `GraphRecursionError`。
  - `test_chat_turn_graph.py`（真实 `ChatTurnNodes` + 替身 steps）：
    - `test_ingress_loads_persona_agent_and_visible_tools`：Agent 白名单 `{"knowledge.search", "knowledge.answer"}`、`relations_available=True` → `state["tools"] == ["knowledge.search"]`、`state["persona_version"] == "tapper-persona-v1"`、span `tap.agent.tools == "knowledge.search"`。
    - `test_seed_search_does_not_run_relation_analysis`：`prepare_conversation_answer` 收到 `include_relations=False`。
    - `test_tools_node_emits_running_and_completed_task_steps`：一次 `knowledge_search` 调用 → `conversations.emit` 依次收到 `task.step(running)`、`task.step(completed)`、`retrieval.hits_ready(stepId=...:r1:c0)`；返回 `tool_rounds_delta == 1`、`step["kind"] == "prepared"`、transcript 末条 `role == "tool"`。
    - `test_disallowed_tool_call_is_rejected_without_execution`：`sourceIds` 越界 → `run_conversation_tool` 未调用，`task.step` 终态 `rejected`，transcript 内容 `{"status": "rejected", "reason": "source-out-of-scope"}`。
    - `test_final_round_offers_no_tools_after_max_rounds`：`maxToolRounds=1`、`toolRounds=1` → `generate_conversation_answer` 收到 `tools=()`。
    - `test_resume_after_tools_node_does_not_repeat_tool_calls_or_steps`：`tools` 完成后 `model` 抛可重试异常，`resume` 后 `run_conversation_tool` 总调用次数 1、`task.step` 事件各 1 条。
    - `test_tool_round_preamble_is_discarded_from_draft`：替身生成在 tool 轮推送 60 字符文本后返回 tool_calls，第二轮推送最终回答 → Redis 草稿替身先见 `reset=True`，最终拼接只含第二轮文本。
    - `test_insights_tool_failure_is_soft`：`query_metrics` 抛 `InsightsAuthorizationChanged` → `task.step` 终态 `failed`，Turn 继续到 `verify`。
  - `test_draft_batcher.py::test_discard_resets_sent_text`：发送后 `discard()` → 追加 `reset=True`；再 `push("新")`、`close()` 返回 `"新"`。

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_chat_graph.py apps/tap-ai-backend/tests/unit/entrypoints -q` → FAIL。

- [ ] **Step 3: 实现**，然后运行 `make contracts`；`tapper_turn_failures.py` 无需新增分类（`TurnBudgetExhausted` 已为确定性）。

- [ ] **Step 4: 运行通过**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit apps/tap-ai-backend/tests/contract apps/tap-ai-backend/tests/architecture -q`；`TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py -q` 与 `.../test_turn_retry_mysql.py -q`（分两次）。

- [ ] **Step 5: Commit**：`git commit -m "feat: run a bounded tool-calling loop in the chat graph"`

---

### Task 10: 前端：步骤列表、Agent 工具可勾选、澄清与 Insights 指标

**Files:**
- Modify: `apps/tap-ai-frontend/src/features/conversations/model/stream.ts`（`StreamTurnState.steps`、`task.step` 归约、带 `stepId` 的 `retrieval.hits_ready` 合并标签）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.tsx`（`AnswerActivity` 步骤行；澄清与 `insightsFacts` 渲染）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/agents/AgentForm.tsx`、`apps/tap-ai-frontend/src/widgets/tap/workspace/copy.ts`
- Test: `apps/tap-ai-frontend/src/features/conversations/model/stream.test.ts`、`apps/tap-ai-frontend/src/widgets/tap/AnswerActivity.test.tsx`、`apps/tap-ai-frontend/src/widgets/tap/workspace/agents/AgentForm.test.tsx`（均追加）

**Interfaces:**
- Consumes: Task 9 SSE 契约、Task 2 `ToolDescriptor.defaultEnabled`、Task 5 `answerKind`、Task 8 `insightsFacts`（生成类型）。
- Produces：
  - `StreamTurnState.steps: readonly TaskStepView[]`，`TaskStepView = { stepId: string; tool: string; status: "running" | "completed" | "rejected" | "failed"; summary: string; durationMs: number; toolRound: number }`；同 `stepId` 后到的终态覆盖 `running`；更大 `attempt` 的 `turn.started` 清空 `steps`。
  - `AnswerActivity` 每步一行：zh `"第 {toolRound} 轮 · {工具中文名} · {summary} · {durationMs} ms"`、状态 `运行中 / 完成 / 已拒绝 / 失败`；en `"Round {toolRound} · {tool name} · {summary} · {durationMs} ms"`、`Running / Done / Rejected / Failed`。工具中文名：知识检索、关系分析、Insights 查询。
  - `answerKind === "clarification"`：回答以普通正文显示，标签 zh `"需要补充信息"` / en `"Needs more information"`，不显示“无引用/格式错误”提示。`insightsFacts` 非空时在回答下方列出 `"{label} · {metricId} = {value}（截至 {asOf}）"`，标题 zh `"Insights 指标"` / en `"Insights metrics"`。
  - Agent 表单工具区：`GET /ai/tools` 每项一个复选框；`required` 勾选且禁用；非必需项新建时按 `defaultEnabled` 初始勾选，编辑时按修订的 `toolAllowlist`；提交 `toolIds`。原只读文案 `form.toolsBuiltIn` 删除。

- [ ] **Step 1: 写失败测试**
  - `stream.test.ts::reduces task.step running then completed into one step`、`::steps reset on a newer attempt`、`::hits_ready with stepId merges labels`。
  - `AnswerActivity.test.tsx::renders a tool step row with round, tool name and duration`（zh 与 en 各一）；`::clarification answer shows needs-more-information label without citation warning`。
  - `AgentForm.test.tsx::required tools are checked and disabled; optional tools default on and toggle into toolIds`：取消 `graph.relations` 后提交体 `toolIds` 为 `["knowledge.search", "knowledge.answer", "insights.query"]`。

- [ ] **Step 2: 运行确认失败**：`pnpm --filter @tap/ai-frontend exec vitest run src/features/conversations/model/stream.test.ts src/widgets/tap/AnswerActivity.test.tsx src/widgets/tap/workspace/agents/AgentForm.test.tsx` → FAIL。

- [ ] **Step 3: 实现**。

- [ ] **Step 4: 运行通过**（同 Step 2，另跑 `pnpm --filter @tap/ai-frontend exec vitest run src/widgets/tap`）。

- [ ] **Step 5: Commit**：`git commit -m "feat: show agent tool steps and checkable agent tools"`

---

### Task 11: 意图 golden 子集与门禁

**Files:**
- Create: `apps/tap-ai-backend/tests/fixtures/quality/intents/golden-v1.json`
- Create: `apps/tap-ai-backend/src/tap/quality/intent_golden.py`
- Create: `scripts/run-intent-golden.py`
- Modify: `Makefile`（`quality-intents` 并入 `check`；`quality-intents-real` opt-in；ruff/mypy 列表加新脚本）
- Test: `apps/tap-ai-backend/tests/unit/quality/test_intent_golden.py`

**Interfaces:**
- Produces：
  - 夹具 `schemaVersion: "intent-golden-v1"`、`labeledBy`、`labeledAt: "2026-10-19"`、`cases: [{id, question, locale, expectedIntent}]`；六类各 ≥ 5 条（共 ≥ 30，中英混合，不含客户公司名，`out_of_scope` 至少 2 条英文）。
  - `intent_golden.py`：`validate_intent_golden(value) -> IntentGoldenSet`（每类 ≥ 5，否则 `ValueError`）、`compute_intent_metrics(golden, observations) -> dict[str, float]`（键 `accuracy` 与 `recall.<intent>`）、`fake_gate_passed(golden, observations) -> bool`（全覆盖、每条 `plannerSource == "model"` 且 `intent ∈ AGENT_INTENTS`）、`real_gate_passed(metrics) -> bool`（`accuracy >= 0.8` 且每类 `recall >= 0.6`）。
  - 脚本 `--mode fake|real --golden <path> --observations <path> --report <path>`：fake 用 `ModelGatewayPlanner` + `DeterministicModelGateway`；real 需 `TAP_RUN_QUALITY_INTENTS=1` 与 `TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION=approved:*`（缺失时退出码 2，沿用 `quality-answers-real` 模式）。
  - 确定性网关（与 Task 12 同文件，先加这一部分）：`request.schema.get("title") == "AnswerPlan"` 时按上下文 `question`/`sourceIds` 返回合法规划：`intent`/`route` 为 `relation`/`graph`（`_is_relation_question` 为真）否则 `factual_lookup`/`retrieve`，`standalone_query` 与唯一查询 `q1` 的 `text`、`evidence_goal` 均为原问题；`agent_intent`：含 `[e2e-out-of-scope]` → `out_of_scope`（此时 `intent="out_of_scope"`、`route="direct"`、`queries=[]`），含 `[e2e-tool:` → `multi_step_qa`，否则 `knowledge_qa`；`intent_reason = "deterministic fake"`。

- [ ] **Step 1: 写失败测试**：`test_intent_golden.py::test_fixture_has_five_cases_per_intent`、`::test_fake_gate_requires_model_source_for_every_case`、`::test_real_gate_thresholds`（`accuracy=0.79` 失败、`0.8` 且各类 `0.6` 通过）。
- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/quality/test_intent_golden.py -q` → FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行通过**（同 Step 2）并运行 `make quality-intents` → 退出码 0、报告 `fakeGatePassed: true`。
- [ ] **Step 5: Commit**：`git commit -m "test: gate planner intent wiring with an intent golden subset"`

---

### Task 12: 确定性假网关 tool calling、隔离集成与 E2E 覆盖

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/testing/deterministic_model_gateway.py`
- Create: `apps/tap-ai-backend/tests/integration/test_agent_loop_mysql.py`
- Modify: `apps/tap-ai-frontend/tests/e2e/knowledge-conversation.spec.ts`（追加两个用例）
- Test: `apps/tap-ai-backend/tests/unit/testing/test_deterministic_tool_calls.py`（目录不存在则放 `tests/unit/entrypoints/`）

**Interfaces:**
- Produces（假网关，仅在请求带 `tools` 时生效；纯函数式地由请求决定，不保存状态）：
  1. 从 `untrusted_context` 的顶层 `query` 中按出现顺序取标记 `[e2e-tool:<toolId>:<参数文本>]`；`request.messages` 中已出现的 `(toolId, 参数文本)` 视为已消费；第一个未消费的标记产出一个调用：`knowledge.search` → `{"query": <参数文本>}`、`graph.relations` → `{"query": <参数文本>}`、`insights.query` → 固定合法参数（`metricIds: ["first_pass_rate"]`，`filters: {}`，`from`/`to`/`timezone`/`asOf` 取固定常量）。`call_id = f"fake-{len(prior tool messages)}"`，`finish_reason = "tool_calls"`。
  2. 无未消费标记、`graph_relations` 在提供的工具中、问题满足 `_is_relation_question`、且尚未调用过 `graph.relations` → 产出 `graph.relations {"query": <问题>}`（保持既有关系回答 E2E 不回退）。
  3. 否则走现有最终回答逻辑；问题含 `[e2e-clarify]` 时输出 `"CLARIFY: 请说明你指的是哪个产品或版本？"`。
- `test_agent_loop_mysql.py`（`TAP_RUN_MYSQL_INTEGRATION=1`；沿用 `test_answer_plan_execution.py::test_worker_routes_in_graph_persists_plan_and_binds_completion` 与 `owned_project_mysql` 的组装方式：真实 MySQL 会话与 checkpointer、`tests.contract.test_knowledge_api` 的替身检索端口、`DeterministicModelGateway` 支撑的 `KnowledgeModelGateway`）：
  - `test_multi_step_search_persists_steps_and_cites_tool_evidence`：问题带 `[e2e-tool:knowledge.search:<第二个文档关键词>]` → 事件依次含 `task.step(running)`、`task.step(completed)`、`retrieval.hits_ready(stepId)`、`turn.completed`；终态答案至少一条 citation 的标签为第二次检索新增的 S 标签。
  - `test_relation_analysis_runs_only_as_a_tool`：替身 `RelationAnalysis` 返回固定 `APPLIED` 上下文；关系类问题 → 替身被调用恰好 1 次且发生在 `tools` 节点（`task.step.tool == "graph.relations"`），终态答案含 `kind == "edge"` 的 citation。
  - `test_insights_joint_analysis_with_fake_port`：Turn 冻结 `insights_report_refs=("report-1",)`，假 `InsightsPort` 返回 `first_pass_rate` → `task.step.tool == "insights.query"` 终态 `completed`，终态答案 `insightsFacts[0]["metricId"] == "first_pass_rate"`。
  - `test_clarification_reply_completes_without_citations`：问题带 `[e2e-clarify]` → `turn.completed`，`answer.answerKind == "clarification"`、`citations == []`。
  - `test_worker_crash_after_tools_resumes_without_repeating_tool`：在 `model` 第二次进入前注入一次可重试失败（`tap.testing.failure_injection`），重新领取后 `task.step(completed)` 只有 1 条、检索替身对第二个查询只被调用 1 次。
- E2E（`knowledge-conversation.spec.ts`，确定性 profile）：
  - `test("multi-step tool calls show steps before the cited answer", ...)`：发送带 `[e2e-tool:knowledge.search:...]` 的问题 → 回答活动区出现“第 1 轮 · 知识检索”行并变为完成，随后定稿答案出现引用徽标。
  - `test("a clarification reply renders without citations", ...)`：带 `[e2e-clarify]` → 显示“需要补充信息”与澄清问题，无引用警告。

- [ ] **Step 1: 写失败测试**：`test_deterministic_tool_calls.py::test_markers_are_consumed_in_order_then_answer`（两个标记 → 两轮各一个调用，第三次请求为最终回答）、`::test_relation_question_calls_graph_relations_once`、`::test_clarify_marker_produces_clarification`；以及上面的集成与 E2E 用例。
- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/testing/test_deterministic_tool_calls.py -q` 与 `TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_agent_loop_mysql.py -q` → FAIL。
- [ ] **Step 3: 实现假网关与测试组装**。
- [ ] **Step 4: 运行通过**（同 Step 2）；E2E 由**主会话**运行 `make demo-e2e`，全部用例（含既有 `knowledge-graph.spec.ts` 关系引用旅程与 `skills-agents.spec.ts`）通过。
- [ ] **Step 5: Commit**：`git commit -m "test: cover the agent loop with fake tool calling end to end"`

---

### Task 13: 全量验证与文档

**Files:**
- Modify: `docs/architecture.md`（图节点：`ingress → guard_in → seed_search → model ⇄ tools → verify → finalize`；工具注册表四项、可见性与参数校验、`task.step`、澄清 `answerKind`、关系分析只经工具、`TapperPersona` 版本冻结、`CHAT_STATE_SCHEMA_VERSION = 3`）
- Modify: `AGENTS.md`（“Chat turns run on …”一句改为含 `model ⇄ tools` 循环）
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md`：能力 6 ③ 行与 ⑤ 行写入 PR 5 交付内容与剩余项（PR 7 长任务、意图真实门禁待用户运行）；④ 行把“工具参数范围校验留待 PR 5”改为已交付；能力 3 首条“初版不经 LangGraph 工具循环……”改为“关系分析以 `graph.relations` 工具在 agent 循环中执行，不再按问题意图触发”；能力 4 “V1 两项内置”改为“PR 5 起四项内置（`graph.relations`、`insights.query` 可勾选）”
- Modify: `docs/superpowers/plans/2026-10-01-product-roadmap.md`（子项目 7 行：PR 5 交付）

- [ ] **Step 1: 全量验证**（子代理执行部分）：`make check`；`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit apps/tap-ai-backend/tests/contract apps/tap-ai-backend/tests/architecture -q`；MySQL 集成逐个文件：`test_agent_loop_mysql.py`、`test_graph_run_recovery.py`、`test_turn_retry_mysql.py`、`test_conversation_persistence.py`、`test_insights_generation_persistence.py`、`test_answer_plan_execution.py`、`test_chat_sse_resume.py`；`pnpm --filter @tap/ai-frontend exec vitest run`。Expected：除既有记录的环境失败外全部通过；`make contracts` 后无新差异。`make demo-e2e` 由主会话运行。
- [ ] **Step 2: 更新文档**，预览改动的 Markdown 与 Mermaid；`git diff --check`。
- [ ] **Step 3: Commit**：`git commit -m "docs: record langgraph agent loop delivery"`
