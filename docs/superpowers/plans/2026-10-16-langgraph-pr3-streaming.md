# LangGraph PR 3：流式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 回答逐 token 流式下发：模型输出带内联引用标记的 Markdown 正文，草稿经脱敏后写 Redis 草稿流，SSE 以阻塞读取替代 100ms 轮询并在重连时回放草稿，前端即时渲染草稿与引用徽标，定稿后整体替换；用户可立即停止生成；切换输出格式前后用新建的回答 golden set 对比。

**Architecture:** 先补能力 1 的回答 golden set 格式、运行器与评测命令（格式无关，CI 用确定性模型），使切换前的提交可跑出真实模型基线。`ModelGateway` 新增流式 chat；知识网关改为内联引用格式并在流式过程中通过回调交出文本增量；`model` 节点把增量经合批与脱敏写入 Redis Stream `tap:turn:{turnId}:draft`，权威事件提交后向 `tap:conv:{conversationId}:events` 写唤醒。SSE 路由先补齐 MySQL 事件，再 `XREAD BLOCK` 同时等待两条流；Redis 不可用时退回 1s 读 MySQL。取消接口在写 `turn.canceled` 时带上部分草稿并设置 Redis 取消标记，worker 续租循环检测到标记即取消图任务。

**Tech Stack:** Python 3.13、httpx（LiteLLM SSE）、redis.asyncio（已有依赖）、LangGraph 1.2.12、FastAPI StreamingResponse、React/TypeScript/Vitest、Playwright（`make demo-e2e`）。

**Spec:** [Tapper LangGraph 编排设计](../specs/2026-10-14-tapper-langgraph-orchestration-design.md) 第 2 节、第 7 节、第 8.1 节（流式、SSE、停止生成、质量对比条目）、第 9 节 PR 3。

## Global Constraints

- 分支 `feat/langgraph-pr3`（worktree `.worktrees/lg-pr3`），**在 PR 4（`feat/langgraph-pr4`）完成后 rebase 到其上实施**；使用 PR 2 的失败分类与 PR 4 的 `CallMeter`、`PatternEgressRedactor.redact_with_report`、`untrusted_context`。
- 内联引用标记沿用现有证据标签体系：代码中块证据标签为 `S<n>`、关系证据为 `R<n>`（spec 中的 `[E3]` 即 `[S3]`）。标记形如 `[S3]`、`[R1]`，可连写 `[S1][S2]`，放在句末标点之前或之后均可。
- 草稿：合批阈值约 50ms 或 32 字符（先到先发）；Redis Stream 键 `tap:turn:{turnId}:draft`，条目字段 `{attempt, seq, text}`，`XADD MAXLEN ~ 4096`，键 TTL 24h（86_400s）；写入前经输出脱敏。
- 唤醒流键 `tap:conv:{conversationId}:events`，条目只含新权威事件的 `sequence`，`MAXLEN ~ 1024`，TTL 24h。
- SSE：新增 `answer.draft {attempt, seq, text}`，不占 MySQL sequence，SSE 帧不带 `id`，Last-Event-ID 语义不变；删除 `answer.delta`；`turn.canceled` 载荷增加可选 `partialAnswer: str`。Redis 不可用时不下发草稿，SSE 退回每 1s 读 MySQL，trace 记录降级（`tap.stream.degraded = true`）。
- 停止生成：`turn.canceled` 中部分草稿显示为“已停止 · 未核对依据”（英文 “Stopped · not verified”），不进入引用体系；Redis 不可用时 worker 在下一个节点边界检查取消状态。
- 质量：切换输出格式前，用 Task 1 的 golden set 工具在 Task 1 提交上跑出真实模型基线（opt-in，需真实模型环境，由用户执行）；切换后分数不得低于基线。CI 只跑确定性模型模式。
- 只有 `ModelOperation.CHAT` / `STRUCTURED` 计入预算；流式 chat 计为 1 次调用，费用与 token 用量口径与非流式一致（`include_usage`、`x-litellm-response-cost`、`x-litellm-model-id`）。
- 图版本 `tapper-chat-v2`；状态 schema 若新增键须保持 `total=False` 兼容，不必再升版本。测试设计生成、Insights 结构化解释的输出格式不变。
- 不新增依赖；Ruff、类型检查、`tests/architecture/`、前端 lint/typecheck 通过；改契约后运行 `make contracts` 并提交生成物。
- 后端测试命令：`uv run --project apps/tap-ai-backend pytest <path> -q`；前端：`pnpm --filter @tap/ai-frontend exec vitest run <path>`。
- 子代理不得运行 `make demo-up` / `demo-down` / `demo-reset` / `demo-e2e`，不得使用 `git stash`；`make demo-e2e` 由主会话执行。
- 提交信息为简短小写祈使句 Conventional Commit，结尾附 `Co-Authored-By` 行。不在仓库文档或代码中写入客户公司名；golden set 语料为自拟或公开资料。

## Review Focus

1. 手机号被拆在两个草稿批次之间（`138123` | `45678`）：Redis 中任何时刻都不出现完整原号码。→ Task 5 `test_redaction_holds_back_patterns_split_across_batches`。
2. 第 2 次 attempt 续跑时重新生成：前端收到更大 `attempt` 的草稿即清空旧草稿；SSE 重连只回放当前 attempt。→ Task 6 `test_reconnect_replays_only_current_attempt`、Task 8 `test_higher_attempt_draft_resets_text`。
3. 模型输出引用了不存在的标签 `[S9]` 或标记写在 Markdown 代码块里：该标记不形成 claim，也不在草稿中渲染为可点击徽标；全部无效时按现有规则弃答。→ Task 3 `test_unknown_and_code_span_markers_are_not_claims`、Task 8 `test_unknown_label_renders_as_plain_text`。
4. 停止发生在 `seed_search` 期间（尚无草稿）：`turn.canceled` 无 `partialAnswer`，前端显示已停止而非空白草稿。→ Task 7 `test_cancel_before_any_draft_has_no_partial_answer`。
5. Redis 在生成中途不可用：草稿停止下发，答案照常定稿并经 MySQL 1s 读取送达，SSE 不报错断开。→ Task 6 `test_stream_degrades_to_mysql_polling_when_redis_fails`。

---

### Task 1: 回答 golden set 格式、运行器与评测命令

**Files:**
- Create: `apps/tap-ai-backend/tests/fixtures/quality/answers/`（3 份自拟 Markdown 语料：`claims-handling.md`、`policy-servicing.md`、`test-environment-guide.md`，每份 600–1200 字，中文为主，含可被提问的具体事实、数字与步骤）
- Create: `apps/tap-ai-backend/tests/fixtures/quality/answers/golden-v1.json`
- Create: `scripts/run-answer-golden.py`、`scripts/evaluate-answer-golden.py`
- Modify: `Makefile`（`.PHONY`、`check` 中 ruff/mypy 的脚本列表、新目标 `quality-answers`、`quality-answers-real`；`check` 末尾追加 `$(MAKE) quality-answers`，与 `quality-graph-relations` 并列）
- Test: `apps/tap-ai-backend/tests/unit/quality/test_answer_golden.py`

**Interfaces:**
- `golden-v1.json`：`{"schemaVersion": "answer-golden-v1", "labeledBy": "generated-fixture", "labeledAt": "2026-10-16", "cases": [...]}`；每题 `{"id", "question", "locale", "kind": "answerable" | "abstain" | "false_premise", "expectedSources": [文件名去扩展名], "expectedFacts": [必须出现在答案中的短语], "forbiddenFacts": [不得出现的短语]}`。共 30 题：answerable 20（其中 5 题需跨两份文档）、abstain 6（语料中无答案）、false_premise 4（问题含错误前提，期望弃答或答案含 `expectedFacts` 中的纠正短语）。
- 观察文件 `answer-golden-observations-v1`：`{"schemaVersion", "mode": "fake" | "real", "build": git 提交短 SHA, "observations": [{"id", "abstained": bool, "answer": str, "citedSources": [str], "claimCount": int}]}`。
- `run-answer-golden.py --mode fake|real --golden PATH --corpus-dir DIR --observations PATH`：
  - fake：把语料按空行切段为 `Evidence`（`S1..Sn` 标签，`DocumentAnchor` 指向文件名），按问题与段落的字符二元组重叠取前 6 段，调用 `KnowledgeModelGateway.answer`（底层为 `DeterministicModelGateway`），记录观察。用于验证接线、引用形状与评测脚本，不代表回答质量。
  - real：参照 `scripts/run-graph-relations-candidate.py` 的 real 模式拒绝条件与 `ConversationGroundingCheck.dry_run_answer` 调用方式，经运行中的 Tapper API 栈走真实对话管线；需 `TAP_RUN_QUALITY_ANSWERS=1`。
- `evaluate-answer-golden.py --golden PATH --observations PATH --report PATH [--baseline-observations PATH] [--real]`：指标 `citationSourcePrecision`（被引来源中属于 `expectedSources` 的比例，answerable 题）、`expectedSourceRecall`、`factRecall`（`expectedFacts` 命中率）、`abstainAccuracy`（abstain 题弃答比例）、`falsePremiseHandled`（false_premise 题弃答或命中纠正短语的比例）、`forbiddenFactRate`。fake 模式门槛：observations 覆盖全部题目、报告结构有效、`citationSourcePrecision == 1.0`。`--real` 且给出 `--baseline-observations` 时：每项指标不得低于基线（`forbiddenFactRate` 不得高于基线），否则退出码 1。
- Make：`quality-answers`（fake，写入 `.local/quality-answers/`）；`quality-answers-real`（检查 `TAP_RUN_QUALITY_ANSWERS=1`、`TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION=approved:*`，`TAP_QUALITY_ANSWERS_BASELINE` 默认 `.local/quality-answers/baseline-observations.json`）。

- [ ] **Step 1: 写失败测试**（`test_answer_golden.py`，直接 import 两个脚本的纯函数，参照 `tests/unit/quality/` 中 graph-relations 评测测试的写法）
  - `test_golden_fixture_is_valid`：30 题，kind 计数为 20/6/4，`expectedSources` 均对应语料文件，`id` 唯一。
  - `test_metrics_on_handmade_observations`：3 题手工观察，断言各指标数值。
  - `test_real_gate_fails_below_baseline`：基线 `factRecall` 0.8、候选 0.7，评测返回失败；相等时通过。
- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/quality/test_answer_golden.py -q` → FAIL
- [ ] **Step 3: 编写语料、golden set 与两个脚本**
- [ ] **Step 4: 运行通过**：同上 PASS；`make quality-answers` 退出码 0。
- [ ] **Step 5: Commit**：`git commit -m "feat: add the answer golden set and regression commands"`（此提交即真实模型 JSON 格式基线的运行点，在 Task 10 记录其 SHA）

---

### Task 2: `ModelGateway` 流式 chat

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/ports/gateway.py`、`apps/tap-ai-backend/src/tap/modules/ai/domain/models.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/adapters/litellm.py`
- Modify: `apps/tap-ai-backend/src/tap/testing/deterministic_model_gateway.py`
- Test: `apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py`（追加）

**Interfaces:**
- Produces：
  - `@dataclass(frozen=True, slots=True) class ModelTextDelta: text: str`；`ModelStreamEvent = ModelTextDelta | ModelResult`（最后一个事件为完整 `ModelResult`，`text` 为全文，usage、`actual_model`、`cost_usd`、`gateway_call_id` 与非流式一致）。
  - 端口：`def chat_stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]`。
  - LiteLLM：payload 加 `"stream": True, "stream_options": {"include_usage": True}`；逐行解析 `data: {...}` / `data: [DONE]`，累计 `choices[0].delta.content`；最后一个带 `usage` 的块提供 token 数；响应头提供费用与上游模型；超出现有响应大小上限抛 `ModelGatewayUnavailable`；流中途断开抛 `ModelGatewayUnavailable`；`finally` 中照常写 `ModelCallRecord`（`response_text` 为全文）并调用 PR 4 的 `CallMeter.after_call`；调用前 `before_call`。
  - 确定性网关：`chat_stream` 把其非流式答案按 8 字符切块逐个产出，块间 `await asyncio.sleep(0.02)`（E2E 可观察草稿）。
- [ ] **Step 1: 写失败测试**（MockTransport 返回 `text/event-stream` 体）
  - `test_chat_stream_yields_deltas_then_result_with_usage_and_upstream_model`：三个内容块 + 一个 usage 块；产出 3 个 `ModelTextDelta` 与 1 个 `ModelResult`，`result.text` 为拼接全文，usage 与 `x-litellm-model-id` 映射的上游模型正确，recorder 记录一次。
  - `test_chat_stream_interrupted_raises_unavailable_and_still_records`：流在第二块后断开，抛 `ModelGatewayUnavailable`，recorder 记录 `status` 为失败。
  - `test_chat_stream_is_metered_once`：`metered(CallMeter(1, None))` 下完成一次流式调用后 `meter.calls == 1`。
- [ ] **Step 2–4: 确认失败 → 实现 → 通过**（`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py -q`）
- [ ] **Step 5: Commit**：`git commit -m "feat: stream chat completions through the model gateway"`

---

### Task 3: 内联引用输出格式与解析

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/grounded_output.py`（新增内联解析）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py`（`_ANSWER_PROMPT`、`_RELATION_ANSWER_PROMPT`、`_FLOWCHART_ANSWER_PROMPT`、`_GOVERNED_ANSWER_PROMPT` 改为内联格式；`answer()` 改走 `chat_stream`）
- Modify: `apps/tap-ai-backend/src/tap/testing/deterministic_model_gateway.py`（答案改为内联格式正文）
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_inline_citations.py`、`apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py`（更新断言 JSON `{answer, claims}` 的既有用例）

**Interfaces:**
- Produces：
  - `parse_inline_answer(text: str, evidence: Sequence[Evidence], *, extra_labels: frozenset[str] = frozenset(), max_answer_chars: int, max_claims: int, max_claim_chars: int, max_labels_per_claim: int) -> tuple[str, tuple[GeneratedClaim, ...]]`：返回去掉全部标记后的正文与 claim 元组。claim 为“含至少一个有效标记的句子”：句子文本（去标记、首尾空白规整）、标签（按出现顺序去重，属于证据标签 ∪ `extra_labels`）；句子按 `。！？.!?` 与换行切分；代码块与行内代码中的标记不算；未知标签被丢弃，丢弃后无有效标签的句子不是 claim；沿用 `_borrows_another_document` 规则；句子在正文中不能唯一定位时丢弃该 claim。全部 claim 都无效时返回 `("", ())`（调用方按现有规则弃答）。
  - 提示词：要求 Markdown 正文、每个事实句末用 `[S1]` / `[R1]` 标记引用、只用给出的标签、证据不足时只输出 `INSUFFICIENT_EVIDENCE`（解析为弃答，原因 `insufficient_evidence`）。保留 PR 4 的 `untrusted` 说明句。
  - `KnowledgeModelGateway.answer(..., on_text: Callable[[str], Awaitable[None]] | None = None)`：经 `chat_stream` 生成；每个 `ModelTextDelta` 调用 `on_text`（原始文本，含标记）；结束后用 `parse_inline_answer` 产出与原来相同类型的 `AnswerGeneration`（`answer`、`claims` 语义不变，下游校验、快照与前端定稿渲染不改）。`retrieval_profile_id` / 生成器版本标识由 `grounded-answer-v1` 改为 `grounded-answer-v2`。
- [ ] **Step 1: 写失败测试**（`test_inline_citations.py`）
  - `test_sentences_with_markers_become_claims`：`"理赔需在 30 日内提交[S1]。材料包括身份证明[S2][S1]。"` → 正文无标记，两条 claim，第二条标签 `("S2", "S1")`。
  - `test_unknown_and_code_span_markers_are_not_claims`：`"见 `[S1]` 示例。结论成立[S9]。"`（证据只有 S1）→ `("", ())`。
  - `test_relation_labels_are_accepted_via_extra_labels`：`[R1]` 在 `extra_labels={"R1"}` 时成为 claim。
  - `test_insufficient_evidence_sentinel_abstains`：经 `KnowledgeModelGateway.answer` 且网关返回 `INSUFFICIENT_EVIDENCE` 时 `abstained` 结果原因 `insufficient_evidence`。
  - `test_on_text_receives_every_delta`：替身流式网关产出三块，`on_text` 依次收到三块。
- [ ] **Step 2–4: 确认失败 → 实现 → 通过**；另跑 `apps/tap-ai-backend/tests/contract apps/tap-ai-backend/tests/unit/knowledge` 与 `make quality-answers`（fake 门槛仍通过）。
- [ ] **Step 5: Commit**：`git commit -m "feat: generate answers with inline citation markers"`

---

### Task 4: 草稿流与唤醒流（Redis 适配器）

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/chat/adapters/redis_drafts.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/ports.py`（`DraftStream`、`EventWakeups` 协议）
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/conversations.py`（权威事件提交成功后发唤醒）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py`（组合：API 与 generation worker 都注入同一 Redis 客户端构造的适配器）
- Test: `apps/tap-ai-backend/tests/unit/chat/test_redis_drafts.py`（fake Redis 替身）、`apps/tap-ai-backend/tests/integration/test_redis_drafts_integration.py`（真实 Redis，沿用现有 Redis 集成测试的启用变量与夹具）

**Interfaces:**
- Produces：
  - `class DraftStream(Protocol)`: `async def append(self, turn_id: str, *, attempt: int, seq: int, text: str) -> None`；`async def replay(self, turn_id: str, *, attempt: int) -> tuple[DraftChunk, ...]`；`async def read(self, keys: Mapping[str, str], *, block_ms: int) -> tuple[StreamEntry, ...]`（`keys` 为 Redis 键 → 起始 id，返回含键名、条目 id 与字段的条目；同时等待草稿键与唤醒键）；`async def partial_text(self, turn_id: str) -> tuple[int, str] | None`（最大 attempt 及其拼接文本）。
  - `@dataclass(frozen=True, slots=True) class DraftChunk: attempt: int; seq: int; text: str`。
  - `class EventWakeups(Protocol)`: `async def notify(self, conversation_id: str, sequence: int) -> None`。
  - `RedisDraftStream`、`RedisEventWakeups`：键名、`MAXLEN ~`、TTL 取 Global Constraints；任何 Redis 异常包装为 `DraftStreamUnavailable`（调用方据此降级，不使 Turn 失败）。
  - `ConversationService.append_stream_event` 与 `complete_evidence` 在仓储提交成功后调用 `wakeups.notify(...)`（`wakeups` 可为 `None`；通知失败只记 warning 与 span 属性 `tap.stream.wakeup_failed`）。
- [ ] **Step 1: 写失败测试**
  - `test_append_sets_maxlen_and_ttl`、`test_replay_filters_attempt_and_orders_by_seq`、`test_partial_text_uses_latest_attempt`、`test_redis_errors_raise_draft_stream_unavailable`（fake Redis）。
  - `test_notify_after_commit_only`：仓储 `append_stream_event` 抛异常时不发唤醒；成功时发一次，`sequence` 等于新事件序号。
  - 集成：`test_read_blocks_until_draft_or_wakeup_arrives`（真实 Redis，阻塞读在另一协程 `append` 后 1s 内返回）。
- [ ] **Step 2–4: 确认失败 → 实现 → 通过**
- [ ] **Step 5: Commit**：`git commit -m "feat: add redis draft and wakeup streams for conversations"`

---

### Task 5: `model` 节点流式写草稿，进度事件即时提交

**Files:**
- Create: `apps/tap-ai-backend/src/tap/entrypoints/tapper_draft_batcher.py`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_chat_turn.py`（`seed_search`、`model`、`verify`、`finalize`、`stream_events`）
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/ports.py` 与 `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py`（`generate_conversation_answer(..., on_text=None)` 透传到知识网关）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py`（构造 batcher 并注入）
- Test: `apps/tap-ai-backend/tests/unit/entrypoints/test_draft_batcher.py`、`apps/tap-ai-backend/tests/unit/entrypoints/test_chat_turn_graph.py`（追加）、`apps/tap-ai-backend/tests/contract/test_worker_stream_events.py`（更新）

**Interfaces:**
- Produces：
  - `class DraftBatcher`: `__init__(self, *, turn_id: str, attempt: int, drafts: DraftStream | None, redact: Callable[[str], Awaitable[str]], max_delay_s: float = 0.05, max_chars: int = 32, holdback_chars: int = 48)`；`async def push(self, text: str) -> None`；`async def close(self) -> str`（冲刷剩余并返回脱敏后全文）。算法：累计原始全文 `raw`；达到阈值时 `redacted = await redact(raw)`，可发送的上界为 `len(redacted) - holdback_chars`（`close` 时为全长），只发送上次已发位置到上界之间的新文本（若脱敏导致已发前缀变化，则以 `seq` 递增发送一条 `text` 为空、字段 `reset: true` 的条目后从头重发——不预期发生，测试覆盖）；`drafts` 为 `None` 或抛 `DraftStreamUnavailable` 时转为只累计不发送，并在当前 span 设 `tap.stream.degraded = true`。
  - `model` 节点：用 batcher 作为 `on_text` 回调；节点结束前 `close()`。
  - `seed_search` 完成时立即经 `conversations.append_stream_event(..., lease_token=turn.lease_token)` 提交 `retrieval.hits_ready`；`verify` 开始时提交 `stage.started {"stage": "verify"}`；状态记录已提交的事件类型，`finalize` 的 `stream_events` 不再重复它们，并且不再产生 `answer.delta`。
- [ ] **Step 1: 写失败测试**
  - `test_draft_batcher.py`：`test_flushes_on_32_chars_or_50ms`；`test_redaction_holds_back_patterns_split_across_batches`（依次 push `"联系 138123"`、`"45678 处理"`，所有已 `append` 文本拼接后不含 `13812345678`，`close()` 返回含 `[REDACTED_PHONE]`）；`test_degrades_silently_when_drafts_unavailable`；`test_seq_is_monotonic_per_attempt`。
  - `test_chat_turn_graph.py`：`test_hits_ready_is_committed_before_generation_starts`（替身生成步骤断言调用时仓储已有 `retrieval.hits_ready`）；`test_no_answer_delta_event_is_emitted`。
  - `test_worker_stream_events.py`：更新期望事件序列（去掉 `answer.delta`，`retrieval.hits_ready` 提前）。
- [ ] **Step 2–4: 确认失败 → 实现 → 通过**（含 `apps/tap-ai-backend/tests/unit apps/tap-ai-backend/tests/contract/test_worker_stream_events.py`）
- [ ] **Step 5: Commit**：`git commit -m "feat: stream redacted answer drafts from the model node"`

---

### Task 6: SSE 改为阻塞读取，`answer.draft` 契约

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/routes/conversations.py`（`stream`）
- Modify: `apps/tap-ai-backend/src/tap/contracts/chat_stream.py`（新增 `AnswerDraftPayload`/`AnswerDraftEvent`，删除 `AnswerDeltaPayload`/`AnswerDeltaEvent`，`TurnCanceledPayload.partial_answer: str | None`（别名 `partialAnswer`））
- Regenerate: `make contracts`
- Test: `apps/tap-ai-backend/tests/contract/test_chat_stream_contract.py`（或现有 SSE 契约测试文件，追加）、`apps/tap-ai-backend/tests/integration/test_chat_sse_resume.py`（追加，真实 Redis + MySQL）

**Interfaces:**
- 路由行为：
  1. 补齐 `sequence > Last-Event-ID` 的 MySQL 事件（同现有编码）。
  2. 对未结束的 Turn：`replay(turn_id, attempt=当前 attempt)` 回放草稿为 `answer.draft` 帧（无 `id`）。
  3. 循环 `read({draft_key: last_draft_id, wake_key: last_wake_id}, block_ms=15_000)`：草稿条目编码为 `answer.draft`；唤醒条目触发回 MySQL 读取并发送新权威事件；`block` 超时发送 SSE 注释行 `: keepalive`。
  4. 会话中所有 Turn 都已终止且已发送完毕后关闭流（保持前端现有重连语义）。
  5. `DraftStreamUnavailable` 时转入每 1s 读 MySQL 的降级循环（不发草稿），当前 span 设 `tap.stream.degraded = true`。
- `answer.draft` 帧仍使用 `ChatEventEnvelope` 外层（`sequence` 取最近一次权威事件序号，前端以 `attempt`/`seq` 排序草稿，不以 `sequence` 去重草稿）。
- [ ] **Step 1: 写失败测试**
  - 契约：`test_answer_draft_event_schema_and_no_answer_delta`；`test_turn_canceled_accepts_partial_answer`。
  - 集成：`test_draft_frames_arrive_before_terminal_event`；`test_reconnect_replays_only_current_attempt`（写入 attempt 1 与 2 的草稿，带 Last-Event-ID 重连只收到 attempt 2）；`test_stream_degrades_to_mysql_polling_when_redis_fails`（读取抛 `DraftStreamUnavailable` 后终止事件在约 1s 内送达，流不报错）；`test_stream_does_not_poll_when_idle`（替身计数：阻塞期间 MySQL 读取次数不随时间增长）。
- [ ] **Step 2–4: 确认失败 → 实现 → 通过**；运行 `make contracts` 并确认生成物只含上述契约变化。
- [ ] **Step 5: Commit**：`git commit -m "feat: replace sse polling with blocking reads and answer drafts"`

---

### Task 7: 立即停止生成

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/application/conversations.py`（`cancel`）
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/adapters/redis_drafts.py`（取消标记）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py`（续租等待循环检测取消）
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/application/chat_graph.py`（节点边界检查取消，Redis 不可用时的兜底）
- Test: `apps/tap-ai-backend/tests/unit/chat/test_turn_processor.py`、`apps/tap-ai-backend/tests/unit/chat/test_conversation_service.py`（追加）、`apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py`（追加）

**Interfaces:**
- Produces：
  - `DraftStream.request_cancel(turn_id: str) -> None`（`SET tap:turn:{turnId}:cancel 1 EX 86400`）与 `cancel_requested(turn_id: str) -> bool`。
  - `ConversationService.cancel`：先读 `partial_text(turn_id)`（失败忽略），写 `turn.canceled` 时载荷加 `partialAnswer`（仅当非空；文本已是脱敏后的草稿）；提交后 `request_cancel`（失败忽略）。
  - worker：等待图任务时以 `min(renew_interval_seconds, 0.25)` 为周期检查 `cancel_requested`，为真则取消图任务、静默返回（终止事件已由 API 写入）；续租仍按 `renew_interval_seconds`。
  - `ChatGraph`：每个节点前除授权外检查一个可选的 `canceled: Callable[[], Awaitable[bool]]`（worker 传入读取 Turn 状态的检查），为真时抛 `PermissionError`（按所有权丢失静默退出）。
- [ ] **Step 1: 写失败测试**
  - `test_cancel_includes_partial_draft`、`test_cancel_before_any_draft_has_no_partial_answer`（服务层，fake drafts）。
  - `test_worker_cancels_running_graph_within_half_a_second`（替身生成步骤 `await asyncio.sleep(10)`，设置取消标记后 worker 在 0.5s 内返回且未调用 `complete_evidence`）。
  - `test_cancel_without_redis_stops_at_next_node_boundary`（`cancel_requested` 抛 `DraftStreamUnavailable`，Turn 状态已为 canceled，下一个节点不执行）。
- [ ] **Step 2–4: 确认失败 → 实现 → 通过**
- [ ] **Step 5: Commit**：`git commit -m "feat: stop answer generation immediately on cancel"`

---

### Task 8: 前端草稿、引用徽标、核对依据与已停止状态

**Files:**
- Modify: `apps/tap-ai-frontend/src/features/conversations/model/stream.ts`（reducer）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.tsx` 与相关回答组件（草稿渲染）
- Create: `apps/tap-ai-frontend/src/features/conversations/components/DraftAnswer.tsx`（含测试）
- Test: `apps/tap-ai-frontend/src/features/conversations/model/stream.test.ts`、`DraftAnswer.test.tsx`

**Interfaces:**
- `StreamTurnState` 新增：`draft: { attempt: number; seq: number; text: string } | null`、`citationLabels: Record<string, { title: string; location: string | null }>`（来自 `retrieval.hits_ready` 载荷）、`stage: "verify" | null`、`partialAnswer: string | null`。
- reducer：`answer.draft` 按 `(attempt, seq)` 追加（更大 attempt 清空重来，重复或更小 seq 忽略，`reset: true` 清空当前 attempt 文本）；`retrieval.hits_ready` 填 `citationLabels`；`stage.started {stage: "verify"}` 设 `stage`；`turn.completed` / `turn.abstained` 清空 `draft` 并用权威答案替换；`turn.canceled` 设 `partialAnswer`；删除 `answer.delta` 分支。`answer.draft` 不更新 `lastSequence`。
- `DraftAnswer`：以“生成中”样式渲染 `draft.text` 的 Markdown（沿用现有安全 Markdown 渲染），把 `[S3]`、`[R1]` 中存在于 `citationLabels` 的标记渲染为带来源标题的徽标，不存在的标记按纯文本显示；`stage === "verify"` 时显示“正在核对依据” / “Checking sources”；取消后显示 `partialAnswer` 与“已停止 · 未核对依据” / “Stopped · not verified”，无部分草稿时只显示已停止状态。
- [ ] **Step 1: 写失败测试**：`test_higher_attempt_draft_resets_text`、`test_out_of_order_seq_is_ignored`、`test_terminal_answer_replaces_draft`、`test_canceled_keeps_partial_answer`（stream.test.ts）；`test_known_label_renders_badge_with_source_title`、`test_unknown_label_renders_as_plain_text`、`test_verify_stage_shows_checking_sources`、`test_stopped_shows_unverified_notice`（DraftAnswer.test.tsx）。
- [ ] **Step 2–4: 确认失败 → 实现 → 通过**（`pnpm --filter @tap/ai-frontend exec vitest run src/features/conversations`，并跑前端 lint 与 typecheck）
- [ ] **Step 5: Commit**：`git commit -m "feat: render streaming drafts with citation badges"`

---

### Task 9: E2E 旅程

**Files:**
- Modify: `apps/tap-ai-frontend/tests/e2e/knowledge-conversation.spec.ts`

**Interfaces:**
- Consumes: 确定性网关分块流式输出（Task 2）、`[e2e-cancel]` 延迟钩子（已有）。
- [ ] **Step 1: 更新/新增断言**
  - 事件序列不再含 `answer.delta`；重放断言改用 `retrieval.hits_ready` 的序号作为 Last-Event-ID。
  - `draft appears with citation badges before the final answer replaces it`：提交问题后先可见“生成中”草稿与至少一个引用徽标，随后可见定稿与“打开来源引用 1”按钮，草稿消失。
  - `abstained answer replaces the draft`：选择无相关内容的问题，弃答提示替换草稿。
  - `stop shows the partial draft as not verified`：`[e2e-cancel]` 问题，草稿出现后点击停止，显示“已停止 · 未核对依据”，Turn 状态 `canceled`。
- [ ] **Step 2: 子代理只运行 `pnpm --filter @tap/ai-frontend exec playwright test --list` 确认用例可收集；`make demo-e2e` 由主会话执行**
- [ ] **Step 3: Commit**：`git commit -m "test: cover streaming drafts and stop in the conversation journey"`

---

### Task 10: 全量验证、质量对比记录与文档

**Files:**
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 6 ② 中流式、逐 token 下发、立即停止的完成情况；能力 1 “golden set 格式与回归命令”完成情况，真实基线对比状态）
- Modify: `docs/superpowers/plans/2026-10-01-product-roadmap.md`（子项目 7 行：PR 3；子项目 3 行：golden set 格式与命令已就绪）
- Modify: `docs/architecture.md`（SSE 不再轮询、Redis 草稿流；可靠问答缺口行中“仓库内质量用例为空”“SSE 为数据库轮询”两项的更新）

- [ ] **Step 1: 全量验证**：`make check`（含 `quality-answers`）；`make test`（对照既有失败，不得新增）；相关 MySQL 与 Redis 集成测试；`make contracts` 无新差异。
- [ ] **Step 2: 记录质量对比运行方式**：在 V1 总纲能力 1 处写明真实基线运行点为 Task 1 提交（短 SHA），切换后在本分支 HEAD 运行 `make quality-answers-real` 并给出 `TAP_QUALITY_ANSWERS_BASELINE`；未运行前该验收项保持未勾选。
- [ ] **Step 3: Commit**：`git commit -m "docs: record langgraph streaming delivery"`
