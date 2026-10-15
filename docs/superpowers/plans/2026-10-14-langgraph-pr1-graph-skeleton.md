# LangGraph PR 1：图骨架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对话回答与 Insights 解释运行在新的版本化图 `tapper-chat-v2` 上，检索、生成、校验、定稿各为独立节点并各自 checkpoint，生成失败后续跑不重复检索；对用户可见的行为与事件不变。

**Architecture:** 把 `AuthorizedRetrieval.answer` 拆成 prepare / generate / verify 三段，段间状态是可 JSON 序列化的 `EvidenceBundle`；三段经 `KnowledgeAPI` → `AnswerService` → `KnowledgeHttpService` 逐层暴露为“对话回答步骤”，原一次性方法改为顺序组合三段，行为不变。`modules/ai/application/chat_graph.py` 定义图；节点胶水从 worker 拆到 `entrypoints/tapper_chat_turn.py`；worker 只负责领取、续租、运行图与写结果。

**Tech Stack:** Python 3.13、LangGraph 1.2.12（`StateGraph`、MySQL checkpointer）、pytest、OpenTelemetry（现有 `tap.platform.telemetry`）。

**Spec:** [Tapper LangGraph 编排设计](../specs/2026-10-14-tapper-langgraph-orchestration-design.md) 第 1 节、第 6.3 节、第 9 节 PR 1。

## Global Constraints

- 图版本 `tapper-chat-v2`，`state_schema_version = 1`；对话与 Insights 解释共用。
- 证据包编码后 UTF-8 字节数上限 `524_288`（512KB）；超限抛确定性错误，不截断。
- PR 1 不改提示词、模型输出格式（仍为 JSON `{answer, claims}`）、SSE 事件集合与载荷、HTTP 契约；`make contracts` 不应产生差异。
- 测试设计生成（`test-design-generation-v1`、`modules/test_management/`）代码不动，继续用 `InteractionGraph`。
- 不新增依赖；Ruff 与类型检查通过；遵守 `tests/architecture/` 的模块边界。
- 提交信息为简短小写祈使句 Conventional Commit，结尾附 `Co-Authored-By` 行。
- 分支从 `origin/develop` 新建 `feat/langgraph-chat-graph`，在独立 worktree 实施；子代理不得运行 `make demo-up` / `demo-down` / `demo-reset`。
- 后端测试命令统一为 `uv run --project apps/tap-ai-backend pytest <path> -q`（仓库根目录执行）。

## Review Focus

1. 部署时仍在途的旧 `fast-chat-v1` checkpoint：领取后应丢弃旧 checkpoint、用新图从头跑完，而不是抛 `GraphVersionConflict` 让 worker 循环崩溃。→ Task 6 `test_legacy_fast_chat_checkpoint_restarts_on_new_graph`。
2. 证据包接近上限且含中文与关系片段：按 UTF-8 字节计数，超限时 Turn 以 `turn.failed` 结束，不截断。→ Task 1 `test_bundle_limit_counts_utf8_bytes`、Task 6 `test_oversized_bundle_fails_turn`。
3. 跨节点续跑时 plan 截止时间：每个步骤按持久化截止时间计算剩余时间，不因续跑重置。→ Task 4 `test_steps_share_persisted_plan_deadline`。
4. seed_search 之后授权被撤销（续跑间隔很久）：generate 前复检失败，不发生模型调用。→ Task 4 `test_generate_rechecks_authority_before_model_call`。
5. prepare 阶段即弃答（无证据）：仍持久化答案快照并产生与改造前相同的 `turn.abstained` 事件序列。→ Task 4 `test_prepare_abstain_is_persisted_by_finalize`、Task 6 既有契约测试。

---

### Task 1: 通用 dataclass JSON 编解码与 `EvidenceBundle`

**Files:**
- Create: `apps/tap-ai-backend/src/tap/platform/serialization/__init__.py`
- Create: `apps/tap-ai-backend/src/tap/platform/serialization/dataclass_json.py`
- Create: `apps/tap-ai-backend/src/tap/modules/knowledge/application/evidence_bundle.py`
- Test: `apps/tap-ai-backend/tests/unit/platform/test_dataclass_json.py`
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_evidence_bundle.py`

**Interfaces:**
- Produces:
  - `to_json(value: object) -> JsonValue`、`from_json(tp: type[T], data: JsonValue) -> T`（`dataclass_json.py`）。支持：`None`、`bool/int/float/str`、`Enum`（按 value）、`datetime`（ISO 8601）、`tuple[X, ...]` 与定长 tuple、`list`、`dict[str, X]`、`Mapping[str, X]`（解码为 `MappingProxyType`）、`X | None`、嵌套 dataclass。dataclass 编码为带 `"__type__": <类名>` 的对象；解码多 dataclass 联合类型时按 `__type__` 选择。遇到 `init=False` 字段或不支持的类型抛 `TypeError`。
  - `EvidenceBundle`（frozen、slots dataclass）：字段为 `AuthorizedRetrieval.answer` 第 380–559 行产生、并被第 562–809 行读取的全部局部量，`policy` / `current`（`RetrievalPolicyContext`）除外，由调用方重新推导后传入。已知至少包括：`request: AnswerRequest`、`generation_query: str`、`response: SearchResponse`、`plan: QueryPlan`、`context_snapshot: ContextSnapshot`、`publication: PublicationBinding | None`、`relation: RelationContext | None`、`answer_input: AssembledAnswer | None`、`evidence_map: Mapping[str, tuple[str, ...]]`、`graph_context: tuple[Mapping[str, object], ...]`、`flow_context: Mapping[str, object]`、`missing: bool`、`relation_first: bool`。
  - `EVIDENCE_BUNDLE_MAX_BYTES = 524_288`；`class EvidenceBundleTooLarge(ValueError)`；`encode_bundle(bundle: EvidenceBundle) -> dict[str, object]`（超限抛 `EvidenceBundleTooLarge`）；`decode_bundle(data: Mapping[str, object]) -> EvidenceBundle`。

- [ ] **Step 1: 写编解码失败测试**

`test_dataclass_json.py`：
- `test_round_trips_nested_frozen_dataclasses_with_enums_tuples_and_mappings`：自定义 dataclass，含 StrEnum、`tuple[Inner, ...]`、`Mapping[str, int]`、`datetime`、`Inner | None`；`from_json(T, json.loads(json.dumps(to_json(v)))) == v`，并断言 Mapping 字段解码后 `isinstance(..., MappingProxyType)`。
- `test_union_of_dataclasses_uses_type_tag`：`A | B` 字段分别存 A、B 实例，往返后类型正确。
- `test_init_false_field_is_rejected`：含 `field(init=False)` 的 dataclass，`to_json` 抛 `TypeError`。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/platform/test_dataclass_json.py -q`
Expected: FAIL（`ModuleNotFoundError: tap.platform.serialization`）

- [ ] **Step 3: 实现 `dataclass_json.py`**

用 `typing.get_type_hints(cls)` 与 `get_origin/get_args` 递归；类名映射在解码时由联合成员的 `__name__` 匹配。

- [ ] **Step 4: 写证据包失败测试**

`test_evidence_bundle.py`：
- `test_bundle_round_trip_preserves_retrieval_artifacts`：用 `tests/unit/knowledge/test_answer_service.py` 既有夹具构造一个含 `RelationContext`、`AssembledAnswer`、`PublicationBinding` 的 bundle；`decode_bundle(json.loads(json.dumps(encode_bundle(b)))) == b`。
- `test_bundle_limit_counts_utf8_bytes`：构造 `generation_query` 为中文、编码后 UTF-8 字节数刚超过 `524_288` 而字符数低于该值的 bundle，`encode_bundle` 抛 `EvidenceBundleTooLarge`；比上限少 1KB 的同类 bundle 正常编码。

- [ ] **Step 5: 实现 `evidence_bundle.py` 并运行两份测试**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/platform/test_dataclass_json.py apps/tap-ai-backend/tests/unit/knowledge/test_evidence_bundle.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/tap-ai-backend/src/tap/platform/serialization apps/tap-ai-backend/src/tap/modules/knowledge/application/evidence_bundle.py apps/tap-ai-backend/tests/unit/platform/test_dataclass_json.py apps/tap-ai-backend/tests/unit/knowledge/test_evidence_bundle.py
git commit -m "feat: add a json codec for retrieval evidence bundles"
```

### Task 2: 拆分 `AuthorizedRetrieval.answer`

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/retrieve.py:368-809`
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_retrieval_answer_phases.py`

**Interfaces:**
- Consumes: `EvidenceBundle`（Task 1）。
- Produces（`AuthorizedRetrieval` 方法）：
  - `async def prepare_answer(self, request: AnswerRequest, policy: RetrievalPolicyContext, *, frozen_policy: bool = False, governance=None, graph_context: tuple[Mapping[str, object], ...] = (), answer_execution: AuthorizedAnswerExecution | None = None, authorize=None) -> AnswerResponse | EvidenceBundle`：第 380–559 行；任何弃答路径返回 `AnswerResponse`。
  - `async def generate_answer(self, bundle: EvidenceBundle, policy: RetrievalPolicyContext, *, governance=None, model_alias: str | None = None, authorize=None) -> AnswerGeneration`：先执行与第 527–532 行相同的当前性复检与 `authorize()`，再执行第 562–591 行。
  - `async def verify_answer(self, bundle: EvidenceBundle, generation: AnswerGeneration, policy: RetrievalPolicyContext, *, authorize=None) -> AnswerResponse`：第 592–809 行。
  - `answer(...)` 签名不变，顺序调用三段。

- [ ] **Step 1: 写失败测试**

`test_retrieval_answer_phases.py`（复用 `test_answer_service.py` 的夹具与 `DeterministicTapperModel`）：
- `test_phased_answer_equals_single_shot_answer`：同一输入，`answer()` 与 `verify_answer(decode(encode(prepare)), generate(...))` 的 `AnswerResponse` 在注入同一个确定性 id 工厂后完全相等。
- `test_prepare_returns_abstain_response_without_evidence`：无命中时 `prepare_answer` 返回 `AnswerResponse` 且 `abstained is True`，生成端口未被调用。
- `test_generate_rechecks_current_revision_before_model_call`：prepare 后令修订失效，`generate_answer` 抛 `DocumentStateChanged`，生成端口调用次数为 0。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge/test_retrieval_answer_phases.py -q`
Expected: FAIL（`AttributeError: 'AuthorizedRetrieval' object has no attribute 'prepare_answer'`）

- [ ] **Step 3: 按行号区间移动代码到三个方法，`answer()` 改为组合**

只移动、不改写逻辑；段间局部量全部经 `EvidenceBundle` 传递。

- [ ] **Step 4: 运行新测试与既有知识测试**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge apps/tap-ai-backend/tests/contract/test_knowledge_api.py apps/tap-ai-backend/tests/contract/test_resource_modes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/modules/knowledge/application/retrieve.py apps/tap-ai-backend/tests/unit/knowledge/test_retrieval_answer_phases.py
git commit -m "refactor: split grounded answering into prepare, generate and verify"
```

### Task 3: `KnowledgeAPI` 与 `AnswerService` 分段方法

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/api.py:166`（`answer_frozen` 附近）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/answers.py:147-241`
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_answer_service_phases.py`

**Interfaces:**
- Consumes: Task 2 的三个方法。
- Produces:
  - `KnowledgeAPI.prepare_frozen / generate_frozen / verify_frozen`：参数与 Task 2 对应方法一致；每个方法在 `asyncio.timeout(answer_execution.remaining_seconds)` 内执行（`answer_execution` 为 `None` 时不加超时），与现有 `answer_frozen` 相同。
  - `AnswerService.prepare_frozen(request, revisions, policy, *, governance, graph_context=(), answer_execution=None, authorize=None) -> AnswerResponse | EvidenceBundle`：包含现有第 159–178 行的修订与授权校验。
  - `AnswerService.generate_frozen(bundle, revisions, policy, *, governance, model_alias=None, answer_execution=None, authorize=None) -> AnswerGeneration`。
  - `AnswerService.verify_frozen(bundle, generation, revisions, policy, *, answer_execution=None, authorize=None) -> AnswerResponse`。
  - `AnswerService.save_frozen(response: AnswerResponse, query: str, revisions) -> None`：现有第 220–234 行的快照保存，含前后发布复检。
  - `answer_frozen(...)` 签名不变，组合以上四步。

- [ ] **Step 1: 写失败测试**

- `test_save_frozen_persists_snapshot_once`：`prepare → generate → verify → save_frozen` 后仓库恰有一条快照，内容与 `answer_frozen` 产物一致。
- `test_prepare_abstain_is_persisted_by_finalize`：prepare 返回弃答 `AnswerResponse`，`save_frozen` 保存后快照 `abstained` 为真。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge/test_answer_service_phases.py -q`
Expected: FAIL（`AttributeError`）

- [ ] **Step 3: 实现上述方法**

- [ ] **Step 4: 运行**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge apps/tap-ai-backend/tests/unit/telemetry/test_pipeline_spans.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/modules/knowledge/api.py apps/tap-ai-backend/src/tap/modules/knowledge/application/answers.py apps/tap-ai-backend/tests/unit/knowledge/test_answer_service_phases.py
git commit -m "feat: expose frozen answering as resumable phases"
```

### Task 4: `KnowledgeHttpService` 对话回答步骤

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py:214-466`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/ports.py`（新增协议与步骤类型）
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_conversation_answer_steps.py`

**Interfaces:**
- Consumes: Task 3 的 `AnswerService` 分段方法；`encode_bundle` / `decode_bundle`；`to_json` / `from_json`。
- Produces（`ports.py`）：

```python
class ConversationAnswerStep(TypedDict, total=False):
    kind: Literal["prepared", "generated", "verified", "final"]
    bundle: dict[str, object]      # encode_bundle 结果；prepared / generated
    generation: dict[str, object]  # to_json(AnswerGeneration)；generated
    response: dict[str, object]    # verified: to_json(AnswerResponse)，待持久化
                                   # final: RetrievalAnswerResponse.model_dump(mode="json", by_alias=True)，已完成

class ConversationAnswerSteps(Protocol):
    async def prepare_conversation_answer(self, request, frozen_input, *, answer_plan=None, authorize=None) -> ConversationAnswerStep: ...
    async def generate_conversation_answer(self, step: ConversationAnswerStep, frozen_input, *, answer_plan=None, authorize=None) -> ConversationAnswerStep: ...
    async def verify_conversation_answer(self, step: ConversationAnswerStep, frozen_input, *, answer_plan=None, authorize=None) -> ConversationAnswerStep: ...
    async def finalize_conversation_answer(self, step: ConversationAnswerStep, frozen_input, *, answer_plan=None, authorize=None) -> RetrievalAnswerResponse: ...
```

- 语义：
  - `prepare`：不走检索的路由（`assemble_answer`、`_stopped_answer`、仅模型回答）返回 `final`；`AnswerService.prepare_frozen` 返回弃答时为 `verified`；否则为 `prepared`。
  - `generate`：只处理 `prepared`，其余原样返回。
  - `verify`：只处理 `generated`，其余原样返回。
  - `finalize`：`verified` 经 `save_frozen` 持久化后转 HTTP 响应；`final` 直接 `model_validate`。
  - 每一步都从 `frozen_input` 重新推导修订、治理与 `RetrievalPolicyContext` 并校验摘要（复用 `_answer_conversation` 现有代码），在 plan 持久化截止时间的剩余时间内执行。
- `answer_conversation(...)` 签名不变，顺序组合四步。

- [ ] **Step 1: 写失败测试**

- `test_steps_compose_to_answer_conversation`：同一输入，四步结果与 `answer_conversation` 结果相等（`model_dump` 比较）。
- `test_each_step_survives_json_round_trip`：每步之间对 step 做 `json.loads(json.dumps(step))` 后继续，结果不变。
- `test_steps_share_persisted_plan_deadline`：plan 截止时间设为 prepare 之后已过期，`generate_conversation_answer` 抛 `TimeoutError`，生成端口未被调用。
- `test_generate_rechecks_authority_before_model_call`：prepare 后撤销所选来源授权，`generate_conversation_answer` 抛出的异常类型与同条件下 `answer_conversation` 抛出的相同，生成端口调用次数为 0。
- `test_non_retrieval_route_returns_final_step`：一个 direct plan 的 prepare 返回 `kind == "final"`，generate / verify 原样返回。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge/test_conversation_answer_steps.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge apps/tap-ai-backend/tests/integration/test_answer_plan_execution.py apps/tap-ai-backend/tests/integration/test_knowledge_answer_http.py -q`
Expected: PASS（无 MySQL 时持久化集成测试按现有规则报告跳过原因）

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py apps/tap-ai-backend/src/tap/modules/chat/application/ports.py apps/tap-ai-backend/tests/unit/knowledge/test_conversation_answer_steps.py
git commit -m "feat: split conversation answering into resumable steps"
```

### Task 5: 版本化图运行器与 `ChatGraph`

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/application/interaction_graph.py`
- Create: `apps/tap-ai-backend/src/tap/modules/ai/application/chat_graph.py`
- Test: `apps/tap-ai-backend/tests/unit/ai/test_chat_graph.py`

**Interfaces:**
- Produces:
  - `VersionedGraphRunner(compiled, *, graph_version: str, state_schema_version: int, authorize: AuthorizationCheck)`，方法 `start(*, run_id, state) -> dict`、`resume(*, run_id) -> dict`、`has_checkpoint(*, run_id) -> bool`、`stored_version(*, run_id) -> tuple[str | None, int | None] | None`。从 `InteractionGraph` 现有 start / resume / has_checkpoint / `_config` 提取；`InteractionGraph` 改为委托给它，对外行为不变。
  - `chat_graph.py`：

```python
CHAT_GRAPH_VERSION = "tapper-chat-v2"
CHAT_STATE_SCHEMA_VERSION = 1

class ChatGraphState(TypedDict, total=False):
    graph_version: str
    state_schema_version: int
    execution_mode: Literal["inline", "durable"]
    reasoning_mode: Literal["direct", "workflow", "agentic"]
    payload: dict[str, object]
    route: Literal["chat", "insights"]
    answer_plan: dict[str, Any] | None
    step: dict[str, object] | None
    started_at_ms: int
    failure: dict[str, str] | None   # {"node": ..., "error": <异常类名>}
    result: dict[str, object]

@dataclass(frozen=True, slots=True)
class ChatGraphNodes:
    guard_in: GraphNode
    insights_explain: GraphNode
    seed_search: GraphNode
    model: GraphNode
    verify: GraphNode
    finalize: GraphNode

class ChatGraph:
    def __init__(self, *, checkpointer: BaseCheckpointSaver, nodes: ChatGraphNodes,
                 authorize: AuthorizationCheck,
                 propagate: tuple[type[BaseException], ...] = ()) -> None: ...
    async def start(self, *, run_id: str, payload: Mapping[str, object],
                    route: Literal["chat", "insights"]) -> ChatGraphState: ...
    async def resume(self, *, run_id: str) -> ChatGraphState: ...
    async def has_checkpoint(self, *, run_id: str) -> bool: ...
    async def stored_version(self, *, run_id: str) -> tuple[str | None, int | None] | None: ...
    async def discard(self, *, run_id: str) -> None: ...   # checkpointer.adelete_thread(run_id)
```

- 边：`START → guard_in`；`guard_in →` `insights_explain`（`route == "insights"`）/ `finalize`（有 `failure`）/ `seed_search`；`seed_search → model → verify → finalize`，三者任一产生 `failure` 时直接转 `finalize`；`insights_explain → finalize → END`。
- 节点包装：进入前调用 `authorize()`，返回 False 抛 `PermissionError`；在 `span(f"graph.node.{name}", {"tap.graph.version": CHAT_GRAPH_VERSION})` 内执行；`PermissionError` 与 `propagate` 中的异常原样抛出；其他 `Exception` 转为返回 `{"failure": {"node": name, "error": type(exc).__name__}}`，`finalize` 节点自身的异常一律抛出。
- spec 第 1 节的 `ingress` 职责在 PR 1 由 `start` 承担（写入 `graph_version`、`state_schema_version`、`execution_mode="inline"`、`reasoning_mode="direct"`、`payload`、`route`）；预算写入属于 PR 4，届时再成为独立节点。范围外拒答分支同属 PR 4。

- [ ] **Step 1: 写失败测试**（用 `InMemorySaver` 与假节点）

- `test_chat_route_runs_nodes_in_order`：记录调用顺序为 `guard_in, seed_search, model, verify, finalize`。
- `test_insights_route_skips_answer_nodes`：顺序为 `guard_in, insights_explain, finalize`。
- `test_node_failure_routes_to_finalize_with_failure`：`model` 抛 `RuntimeError`，`verify` 未调用，`finalize` 收到 `failure == {"node": "model", "error": "RuntimeError"}`。
- `test_failed_model_node_resumes_without_repeating_seed_search`：`model` 首次抛 `propagate` 中的异常类型使运行中断；`resume` 后 `seed_search` 总调用次数为 1，`model` 为 2。
- `test_resume_rejects_other_graph_version`：用 `fast-chat-v1` 写入的 checkpoint 调 `resume` 抛 `GraphVersionConflict`；`stored_version` 返回 `("fast-chat-v1", 1)`。
- `test_nodes_emit_spans`（`span_recorder` 夹具）：一次对话运行产生 `graph.node.guard_in` 至 `graph.node.finalize` 五个 span。
- `test_authorization_loss_raises_permission_error`：`authorize` 在 `model` 前返回 False，抛 `PermissionError`。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_chat_graph.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `VersionedGraphRunner`、`ChatGraph`**

- [ ] **Step 4: 运行新测试与既有图测试**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_chat_graph.py apps/tap-ai-backend/tests/unit/test_graph_runs.py apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/modules/ai/application/interaction_graph.py apps/tap-ai-backend/src/tap/modules/ai/application/chat_graph.py apps/tap-ai-backend/tests/unit/ai/test_chat_graph.py
git commit -m "feat: add the versioned tapper chat graph"
```

### Task 6: worker 迁到 `ChatGraph`

**Files:**
- Create: `apps/tap-ai-backend/src/tap/entrypoints/tapper_chat_turn.py`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py:162-672`
- Test: `apps/tap-ai-backend/tests/unit/entrypoints/test_chat_turn_graph.py`

**Interfaces:**
- Consumes: `ChatGraph`、`ChatGraphNodes`、`CHAT_GRAPH_VERSION`（Task 5）；`ConversationAnswerSteps`、`ConversationAnswerStep`（Task 4）。
- Produces（`tapper_chat_turn.py`）：
  - `def answer_steps(knowledge: object) -> ConversationAnswerSteps`：`knowledge` 实现了四个步骤方法时直接返回；否则返回 `SingleShotAnswerSteps(knowledge)`，其 `prepare` 调用 `answer_conversation`（缺失时调用 `answer`）并返回 `final`，其余三步按 Task 4 语义透传。现有只实现 `answer` 的测试假对象因此无需修改。
  - `class ChatTurnNodes`：构造参数 `conversation_id`、`turn`、`conversations`、`steps: ConversationAnswerSteps`、`planner`、`knowledge`（用于 `authorize_planning`）、`insights_explanation`、`require_authorized`；属性 `nodes: ChatGraphNodes`。
    - `guard_in`：现有 `classify` 逻辑（第 311–326 行），并写入 `route`。
    - `seed_search` / `model` / `verify`：调用对应步骤方法，写回 `step`；`seed_search` 记录 `started_at_ms`。
    - `insights_explain`：现有第 333–451 行。
    - `finalize`：
      - 有 `failure` 时产出与现有 `fail_turn` 相同的失败结果（`turn.failed`、`answer-unavailable`）。
      - 否则调用 `finalize_conversation_answer`，再调用 `repository.resolve_citations`，构造 `ProviderResult`，经 `TurnProcessor` 得到 `AnswerEvidence`。
      - 用现有 `stream_events` 与 `_evidence_checkpoint` 产出与今天完全相同的 `{"result": {"evidence", "terminalEvent", "streamEvents"}}`。
  - `stream_events`、`_evidence_checkpoint`、`_evidence_from_checkpoint` 移入本模块，worker 从此处导入。
- worker 变更：
  - 用 `ChatGraph(checkpointer=..., nodes=ChatTurnNodes(...).nodes, authorize=authorized, propagate=(ConversationConflict, ConversationNotFound, GraphCheckpointRetryable, GraphCheckpointUnavailable))` 替换 `InteractionGraph`。
  - `run_graph`：如果 `stored_version(run_id)` 不为 `None` 且不等于 `(CHAT_GRAPH_VERSION, 1)`，先 `discard`，再 `start`；否则按 `has_checkpoint` 选择 `resume` 或 `start`。
  - 续租循环、图后授权、`complete_evidence`、推荐问题刷新、异常映射保持不变。

- [ ] **Step 1: 写失败测试**

- `test_model_failure_resumes_without_repeating_retrieval`：假步骤对象 `generate_conversation_answer` 首次抛 `GraphCheckpointRetryable`；第一次 `run_once` 不完成 Turn；第二次 `run_once` 完成，`prepare` 总调用 1 次。
- `test_legacy_fast_chat_checkpoint_restarts_on_new_graph`：预先以 `InteractionGraph(graph_version="fast-chat-v1")` 为该 Turn 写入 checkpoint；`run_once` 后 Turn 完成，事件序列与无旧 checkpoint 时相同。
- `test_oversized_bundle_fails_turn`：`prepare` 抛 `EvidenceBundleTooLarge`，Turn 以 `turn.failed`（`answer-unavailable`）结束。
- `test_single_shot_knowledge_still_answers`：只实现 `answer(request)` 的假知识对象，Turn 完成且事件与改造前相同。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/entrypoints/test_chat_turn_graph.py -q`
Expected: FAIL

- [ ] **Step 3: 实现 `tapper_chat_turn.py` 并改造 worker**

- [ ] **Step 4: 运行新测试与全部既有 worker 测试**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/entrypoints apps/tap-ai-backend/tests/unit/chat/test_turn_processor.py apps/tap-ai-backend/tests/contract/test_worker_stream_events.py apps/tap-ai-backend/tests/integration/test_answer_plan_execution.py apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py apps/tap-ai-backend/tests/integration/test_insights_generation_persistence.py apps/tap-ai-backend/tests/integration/test_turn_trace_propagation.py -q`
Expected: PASS；既有测试只允许改动断言 graph 版本字符串的位置（`fast-chat-v1` / `insights-explanation-v1` 改为 `tapper-chat-v2`），其他断言不变。

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/entrypoints/tapper_chat_turn.py apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py apps/tap-ai-backend/tests
git commit -m "feat: run chat turns on the tapper chat graph"
```

### Task 7: 已知差距回归测试、文档与全量验证

**Files:**
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_flowchart_gate_regressions.py`
- Modify: `docs/architecture.md`（对话链路与已知差距表）
- Modify: `AGENTS.md`（“The LangGraph interaction graph is currently a fixed `classify → admit → execute` pipeline.”）
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 3 已知局限段落；能力 6 ② 进度）
- Modify: `docs/superpowers/plans/2026-10-01-product-roadmap.md`（子项目 6 行）

- [ ] **Step 1: 写回归测试**（行为已存在，预期直接通过；用于锁定）

- `test_unapproved_flowchart_region_is_not_retrieval_evidence`：带 `bbox` 的命中，其 `inventory_item_id` 不在当前发布的 `approved_item_ids` 中，不进入 `prepare_answer` 的证据。
- `test_unapproved_flowchart_region_cannot_support_a_relation`：同类锚点的关系支撑被 `_flowchart_support_allowed` 过滤，关系没有剩余支撑时整体丢弃。
- `test_completed_answer_rechecks_each_citation`：`authorize_completed_answer` 在任一引用的修订已非当前时抛出，Turn 不以 completed 结束。

- [ ] **Step 2: 运行**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge/test_flowchart_gate_regressions.py -q`
Expected: PASS。任一失败说明行为与 spec 6.3 的核对结论不符：停止并报告，不在本任务内修复。

- [ ] **Step 3: 更新文档**

- `docs/architecture.md`：对话链路改为 `tapper-chat-v2` 的节点序列（`guard_in → seed_search → model → verify → finalize`，Insights 走 `insights_explain`），已知差距表删除 `publication_authority` 一行，按 spec 6.3 写明设计原因。
- `AGENTS.md`：该句改为 “Chat turns run on the versioned `tapper-chat-v2` LangGraph (`guard_in → seed_search → model → verify → finalize`); test-design generation still uses the fixed `classify → admit → execute` graph.”
- V1 总纲：能力 3 已知局限中“当前运行时 `publication_authority` 为 `None`……可能引用未经审阅的流程图切片内容”一句，按 spec 6.3 改写为“切片按 ADR-030 不经发布；流程图图片区域经发布闸门，回归测试见 `test_flowchart_gate_regressions.py`”。能力 6 ② 下注明“PR 1 图骨架已合入（#PR号）”，合并后回填。
- 产品路线图子项目 6 行：状态改为“进行中（PR 1 …）”。

- [ ] **Step 4: 全量验证**

Run: `make check && make test`
Expected: 全部通过；`git diff --exit-code contracts/` 无差异（PR 1 不改契约）。

Run: `make demo-e2e`（由主会话执行，不交给子代理）
Expected: 通过。

- [ ] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/tests/unit/knowledge/test_flowchart_gate_regressions.py docs/architecture.md AGENTS.md docs/superpowers/plans/2026-09-29-v1-roadmap.md docs/superpowers/plans/2026-10-01-product-roadmap.md
git commit -m "docs: describe the tapper chat graph and lock flowchart gate behaviour"
```
