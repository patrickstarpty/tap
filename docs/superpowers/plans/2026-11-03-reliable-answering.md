# 可靠问答 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 V1 能力 1 的工程完成项：golden set 回归、逐 token 流式回答、后台任务重试与 worker 韧性、集成测试跳过报告。

**Architecture:** 新增平台级 `RetryPolicy` 与 `run_worker_loop`；摄取与图谱任务在仓储层计数与退避；模型网关增加流式方法，回答适配器增量抽取 `answer` 字段，worker 批量写 `answer.delta` 事件并经通知器唤醒 SSE；golden set 运行器复用生产回答编排与确定性假网关。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、Alembic、Redis Pub/Sub、httpx、pytest；React/TypeScript/Vitest。

**Spec:** [可靠问答设计](../specs/2026-11-03-reliable-answering-design.md)

## Global Constraints

- 迁移编号从 `0028` 起，按任务顺序递增；迁移需通过 `scripts/check-migration.py` 与 `check-schema-drift.py`。
- `RetryPolicy` 默认 `max_attempts=5, base=2s, cap=300s`，全抖动。
- worker 循环连续失败退避上限 30s；`CancelledError` 不吞。
- `answer.delta` 批量阈值：150ms 或 200 字符；SSE 等待超时 15s、单连接最长 10 分钟、keepalive 为注释行 `: keepalive`。
- 首 token 前流式失败回退非流式；首 token 后失败抛 `ModelGatewayUnavailable`。
- golden 假模型回归零容忍；真实模型只报告。
- 契约变更后运行 `make contracts`，提交生成物。
- 每个任务结束运行相关测试、`uv run --project apps/tap-ai-backend ruff check` 与 `ruff format` 及 `mypy` 于改动文件，并提交（Conventional Commit，小写祈使句，带 `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`）。

---

### Task 1: RetryPolicy 与 worker 主循环

**Files:**
- Create: `apps/tap-ai-backend/src/tap/platform/retry.py`
- Create: `apps/tap-ai-backend/src/tap/platform/worker_loop.py`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_ingestion_worker.py`、`tapper_parser_worker.py`、`tapper_graph_worker.py`、`tapper_generation_worker.py`、`tapper_suggestion_worker.py`、`tapper_test_design_worker.py`（各自 `main` 中的 `while` 循环）
- Test: `apps/tap-ai-backend/tests/unit/platform/test_retry.py`、`tests/unit/platform/test_worker_loop.py`

**Interfaces:**
- Produces:
  - `RetryPolicy(max_attempts: int = 5, base: timedelta = 2s, cap: timedelta = 300s)`；`ceiling(attempt: int) -> timedelta`（`min(cap, base*2**(attempt-1))`）；`next_delay(attempt: int, *, rng: random.Random | None = None) -> timedelta`（`uniform(0, ceiling)`）；`is_exhausted(attempt: int) -> bool`（`attempt >= max_attempts`）。
  - `async def run_worker_loop(name: str, iteration: Callable[[], Awaitable[bool]], *, idle_wait: Callable[[], Awaitable[None]], stop: asyncio.Event | None = None, failure_cap_seconds: float = 30.0, sleep=asyncio.sleep) -> None`：`iteration()` 返回是否做了工作；未做工作时 `await idle_wait()`；异常（非 `CancelledError`）记录 `logger.exception` 与 span `worker.iteration.failed`（属性 `tap.worker`、`tap.consecutive_failures`），`await sleep(min(cap, 0.5*2**(n-1)))` 后继续；`stop` 置位后返回。

- [ ] **Step 1: 写失败测试**：`RetryPolicy` 的 ceiling 序列（2,4,8,…,300 封顶）、`next_delay` 在 `[0, ceiling]` 内（固定种子）、`is_exhausted(5)`；非法参数（`max_attempts<1`、`base<=0`、`cap<base`）抛 `ValueError`。`run_worker_loop`：迭代依次 抛错、抛错、返回 True、置 stop，断言 sleep 收到 `[0.5, 1.0]`、继续执行、`CancelledError` 向外传播、无工作时调用 `idle_wait`。
- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/platform -q`
- [ ] **Step 3: 实现两个模块。**
- [ ] **Step 4: 改造 6 个 worker `main`**：阅读每个 `main` 现有循环（等待 Redis 唤醒 + 轮询间隔 + `run_once`），把“`run_once` + 唤醒等待”封装为 `iteration`/`idle_wait` 交给 `run_worker_loop`，保持原有唤醒与轮询语义；不改变 `run_once`。
- [ ] **Step 5: 测试通过**：上述单测 + `pytest apps/tap-ai-backend/tests -q -k "worker"`。
- [ ] **Step 6: 提交** `feat: keep tap ai workers alive across iteration failures`

### Task 2: 摄取任务投递计数与瞬时失败退避

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0028_ingestion_delivery_count.py`（`knowledge_ingestion_job.delivery_count INT NOT NULL DEFAULT 0`）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/mysql_documents.py`（表定义约 300 行、`claim_jobs` 约 3488 行、`fail_job` 约 4278 行、用户重试路径约 3177 行处 `attempt + 1`）
- Modify: 摄取领域错误码定义所在文件（`grep -rn "SAFE_ERROR_SUMMARIES" apps/tap-ai-backend/src/tap/modules/knowledge/domain`）
- Test: `tests/unit/knowledge/test_transient_ingestion_errors.py`、`tests/integration/knowledge/test_ingestion_retry_mysql.py`

**Interfaces:**
- Consumes: `RetryPolicy`（Task 1）。
- Produces: 领域常量 `TRANSIENT_INGESTION_ERRORS: frozenset[str]`（从现有 `SAFE_ERROR_SUMMARIES` 键中选出表示模型/对象存储/索引/解析器不可用或超时的码，名称以实际代码为准）；`MysqlDocumentRepository(..., retry_policy: RetryPolicy = RetryPolicy())`；新错误码 `lease-exhausted`（加入 `SAFE_ERROR_SUMMARIES`，摘要“处理多次中断，已停止重试”）。

- [ ] **Step 1: 失败测试（单元）**：`TRANSIENT_INGESTION_ERRORS` 是 `SAFE_ERROR_SUMMARIES` 子集，且不含校验类错误（如不支持的格式、内容超限）。
- [ ] **Step 2: 失败测试（MySQL 集成，沿用 `owned_module_database_url` fixture 模式，参考同目录现有测试）**：
  - 认领 → 让租约过期 → 再认领：`delivery_count` 从 1 变 2；
  - `delivery_count` 达到 5 后再次可认领时：任务变 `FAILED`、`error_code="lease-exhausted"`，不返回给 worker；
  - `fail_job` 用瞬时错误码：任务回 `PENDING`，`next_attempt_at > now`，立即认领拿不到；
  - `fail_job` 用非瞬时错误码：行为与现状一致（`FAILED`）；
  - 用户重试（现有 retry 路径）后 `delivery_count == 0`。
- [ ] **Step 3: 运行确认失败**：`TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/knowledge/test_ingestion_retry_mysql.py -q`
- [ ] **Step 4: 实现**：迁移；`claim_jobs` 的 update `.values(delivery_count=knowledge_ingestion_job.c.delivery_count + 1)`，在 update 前对 `row["delivery_count"] >= policy.max_attempts` 的行执行耗尽失败（同 `fail_job` 的终态写法与文档状态同步）；`fail_job` 在瞬时且未耗尽时写 `status=PENDING, next_attempt_at=database_now + policy.next_delay(delivery_count), lease_*=None, error_code=failure.error_code`，文档公共状态保持处理中；重试路径置 `delivery_count=0`。
- [ ] **Step 5: 测试通过**，并跑 `pytest apps/tap-ai-backend/tests -q -k "ingestion"`。
- [ ] **Step 6: 提交** `feat: retry transient ingestion failures with bounded deliveries`

### Task 3: 图谱抽取任务上限与退避

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0029_graph_job_next_attempt.py`（`graph_extraction_job.next_attempt_at DATETIME(6) NULL`）
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql_jobs.py`（`claim` 128 行起、`fail` 约 255 行）、`modules/graph/application/worker.py`（失败分类调用处）、`modules/graph/domain/jobs.py`
- Test: `tests/integration/graph/test_graph_job_retry_mysql.py`、`tests/unit/graph/test_graph_job_transient.py`

**Interfaces:**
- Consumes: `RetryPolicy`。
- Produces: `TRANSIENT_GRAPH_FAILURES: frozenset[str]`（`graph/domain/jobs.py`，包含 worker 现有的模型不可用/超时失败码）；`fail(..., failure_code, now)` 对瞬时码自动退避；失败码 `attempts-exhausted`。

- [ ] **Step 1: 失败测试**：`attempt_count` 达 5 的过期任务被认领时变 `FAILED`/`attempts-exhausted` 且候选快照 `FAILED`；瞬时失败回 `PENDING` 带 `next_attempt_at`，到期前不可认领；非瞬时失败保持现状。
- [ ] **Step 2: 运行确认失败**（同上 MySQL 集成命令）。
- [ ] **Step 3: 实现**：迁移；认领条件 `PENDING & (next_attempt_at IS NULL OR next_attempt_at <= now)`；耗尽处理；`fail` 分支。
- [ ] **Step 4: 测试通过**；`pytest apps/tap-ai-backend/tests -q -k graph`。
- [ ] **Step 5: 提交** `feat: cap graph extraction attempts and back off transient failures`

### Task 4: MySQL 集成测试跳过报告

**Files:**
- Modify: `apps/tap-ai-backend/tests/conftest.py`（约 65–93 行两个 fixture 的 skip）
- Test: `apps/tap-ai-backend/tests/unit/test_mysql_skip_reporting.py`（用 `pytester`）

**Interfaces:**
- Produces: conftest 中 `_MYSQL_SKIPS: list[str]`；`pytest_terminal_summary` 打印 `MySQL persistence tests: {n} skipped (set TAP_RUN_MYSQL_INTEGRATION=1 with owned MySQL)`；`TAP_REQUIRE_MYSQL_INTEGRATION=1` 时 `pytest.fail("requires owned isolated MySQL (TAP_REQUIRE_MYSQL_INTEGRATION=1)")`。

- [ ] **Step 1: 失败测试**：`pytester` 在 `pyproject` 启用 `pytest_plugins = ["pytester"]`（若未启用，在测试模块内 `pytest_plugins = ("pytester",)`），生成一个使用同名 fixture 的最小 conftest 副本不可行时，改为直接对 conftest 中提取的辅助函数 `_mysql_gate(request)` 做单测：未设变量 → skip 并记录 nodeid；设 `TAP_REQUIRE_MYSQL_INTEGRATION=1` → fail；并测试 `_mysql_skip_summary(n)` 文本。
- [ ] **Step 2: 实现**：把两个 fixture 的判断抽为 `_mysql_gate`，加 `pytest_terminal_summary`（n=0 时不打印）。
- [ ] **Step 3: 运行** `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/test_mysql_skip_reporting.py -q`，再跑 `pytest apps/tap-ai-backend/tests -q | tail -3` 确认摘要行出现。
- [ ] **Step 4: 提交** `test: report skipped mysql persistence tests explicitly`

### Task 5: 网关流式输出

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/ports/gateway.py`、`modules/ai/domain/models.py`（新增流事件类型）、`modules/ai/adapters/litellm.py`（复用 519–550 行的 `httpx` stream 传输）、`src/tap/testing/deterministic_model_gateway.py`、任何实现 `ModelGateway` 的其他适配器（`grep -rn "def generate_structured" apps/tap-ai-backend/src`）
- Test: `tests/contract/test_litellm_model_gateway.py`（追加）、`tests/contract/model_gateway_conformance.py`（追加流式一致性）

**Interfaces:**
- Produces（`ai/domain/models.py`）：
  ```python
  @dataclass(frozen=True, slots=True)
  class TextDelta:
      text: str

  @dataclass(frozen=True, slots=True)
  class StreamCompleted:
      result: ModelResult

  ModelStreamEvent = TextDelta | StreamCompleted
  ```
  `ModelGateway.stream_structured(request: ModelRequest) -> AsyncIterator[ModelStreamEvent]`：恰好一个 `StreamCompleted` 且为最后一个；其 `result` 与同请求 `generate_structured` 的校验、记录（`model_call`、span）一致。

- [ ] **Step 1: 失败测试**：假 HTTP 传输返回 OpenAI 兼容 SSE（`data: {"choices":[{"delta":{"content":"{\"answer\":\"He"}}]}` … `data: {"usage":{...}}`、`data: [DONE]`），断言增量拼接等于全文、`StreamCompleted.result.output` 为解析后的 dict、usage 写入；首块前 HTTP 500 → 自动回退非流式且仍成功；首块后断开 → `ModelGatewayUnavailable`；确定性网关流式切块（每块 16 字符）拼接等于其 `generate_structured` 输出的 JSON 文本。
- [ ] **Step 2: 运行确认失败**：`pytest apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py -q -k stream`
- [ ] **Step 3: 实现**：LiteLLM 请求体同非流式，加 `stream: true`、`stream_options: {"include_usage": true}`；解析 `data:` 行；累积后走现有结构化输出解析与校验函数（抽出共享函数，不复制）；记录逻辑放在 `finally`。
- [ ] **Step 4: 测试通过**；跑 `pytest apps/tap-ai-backend/tests/contract -q -k "gateway"`。
- [ ] **Step 5: 提交** `feat: stream structured model output through the gateway`

### Task 6: 增量抽取 answer 字段并支持回调

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/knowledge/application/incremental_json.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py`（`answer` 约 299 行：新增 `on_delta`）、`modules/knowledge/ports/search.py`（端口签名）、调用链上的 `answer_conversation`（`modules/knowledge/application/retrieve.py`、`answers.py`、`api.py`）透传 `on_delta`
- Test: `tests/unit/knowledge/test_incremental_json.py`、`tests/contract/test_knowledge_answer_streaming.py`

**Interfaces:**
- Produces: `class IncrementalJsonField: def __init__(self, field: str); def feed(self, chunk: str) -> str`（返回本块新增的已解码字段文本；只处理顶层对象中该字段的字符串值；字段结束后返回空串）；`on_delta: Callable[[str], Awaitable[None]] | None = None` 参数贯穿 `answer_conversation(...)` 与 `KnowledgeModelGateway.answer(...)`。

- [ ] **Step 1: 失败测试**：`{"answer":"a\\nb \\u4e2d","claims":[]}` 任意切分（逐字符、随机切点 50 组固定种子）拼接输出恒等于 `"a\nb 中"`；`answer` 不是首个属性（`{"claims":[{"text":"x"}],"answer":"y"}`）仍得 `y`；嵌套对象中的同名键不被抽取；代理对 `\ud83d\ude00` 跨块正确。
- [ ] **Step 2: 实现解析器**（小状态机：跟踪深度、是否在字符串内、当前键、转义缓冲）。
- [ ] **Step 3: 失败测试（契约）**：知识回答在确定性网关下传入 `on_delta` 收集文本，拼接等于最终 `AnswerGeneration.answer`（未被校验剔除时）；不传时走原 `generate_structured`。
- [ ] **Step 4: 实现** `on_delta` 分支：`async for event in gateway.stream_structured(request)`，`TextDelta` 喂解析器并回调非空增量，`StreamCompleted` 取结果后走现有后处理。
- [ ] **Step 5: 测试通过**；`pytest apps/tap-ai-backend/tests -q -k "answer"`。
- [ ] **Step 6: 提交** `feat: surface answer text deltas while streaming`

### Task 7: worker 增量事件与 answer.reset

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_generation_worker.py`（`provider` 约 159 行、`stream_events` 约 211 行）
- Modify: `apps/tap-ai-backend/src/tap/modules/chat/adapters/mysql_conversations.py`（新增 `append_stream_event`；claim 约 1200 行处在 `processing_attempt > 0` 时于 `turn.started` 后追加 `answer.reset`）、内存仓储实现（`grep -rn "def claim_queued" apps/tap-ai-backend/src`）
- Modify: `apps/tap-ai-backend/src/tap/contracts/chat_stream.py`（新增 `answer.reset` 事件 `{"attempt": int}`）
- Run: `make contracts`
- Test: `tests/contract/test_conversation_http.py` 或新文件 `tests/contract/test_answer_streaming_events.py`、MySQL 集成 `tests/integration/chat/test_stream_events_mysql.py`

**Interfaces:**
- Consumes: `on_delta`（Task 6）。
- Produces: `ConversationRepository.append_stream_event(chat_id: str, turn_id: str, *, lease_token: str, event_type: str, payload: Mapping[str, object]) -> ConversationEvent`（租约不匹配抛现有租约丢失异常）；worker 内 `DeltaBatcher(flush: Callable[[str], Awaitable[None]], *, max_chars=200, max_interval=0.15, clock=time.monotonic)`，`add(text)`、`close()`。

- [ ] **Step 1: 失败测试**：确定性网关下完成一个 Turn，事件序列中 `answer.delta` 多于 1 条且拼接等于最终回答，`stream_events` 不再追加整段 `answer.delta`；重新认领（attempt 2）时出现 `answer.reset {"attempt":2}`；`DeltaBatcher` 单测（字符阈值、时间阈值、`close` 刷出残余）；`append_stream_event` 租约令牌错误被拒（MySQL 集成）。
- [ ] **Step 2: 实现**：`provider` 内创建 batcher，`on_delta=batcher.add`，回答返回后 `await batcher.close()`；`stream_events` 删除 `answer.delta` 追加；契约新增事件并 `make contracts`。
- [ ] **Step 3: 测试通过**；`pytest apps/tap-ai-backend/tests -q -k "conversation or stream"`；`make contracts` 后 `git diff --stat contracts apps/tap-ai-frontend/src/shared/api/generated`。
- [ ] **Step 4: 提交** `feat: emit incremental answer events from the generation worker`

### Task 8: 事件通知器与 SSE 重写

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/chat/application/notifier.py`（端口 + `InMemoryConversationEventNotifier`）
- Create: `apps/tap-ai-backend/src/tap/platform/messaging/redis_chat_notifier.py`
- Modify: `modules/chat/adapters/mysql_conversations.py`（事务提交后 `publish`；新增 `events_after(chat_id, sequence) -> tuple[ConversationEvent, ...]` 与 `has_active_turns(chat_id) -> bool`）、`modules/chat/application/conversations.py`（服务透传）、`interfaces/http/routes/conversations.py`（`stream`，约 513–555 行）、`entrypoints/tapper_runtime.py`（装配 Redis 通知器到 API 与生成 worker；假环境用内存版）
- Test: `tests/unit/chat/test_notifier.py`、`tests/contract/test_conversation_stream_http.py`

**Interfaces:**
- Produces:
  ```python
  class ConversationEventNotifier(Protocol):
      async def publish(self, scope: ProjectScopeContext, chat_id: str) -> None: ...
      async def wait(self, scope: ProjectScopeContext, chat_id: str, timeout: float) -> bool: ...
  ```
  频道 `tap:chat:{project_id}:{chat_id}`。SSE 循环常量 `_STREAM_WAIT_SECONDS = 15.0`、`_STREAM_MAX_SECONDS = 600.0`。

- [ ] **Step 1: 失败测试**：内存通知器 `wait` 在 `publish` 后返回 True、超时返回 False；SSE（`httpx.AsyncClient` + ASGI，内存仓储与通知器）：有运行中 Turn 时连接保持，worker 侧追加事件并 publish 后客户端立即收到（< 0.5s，无 100ms 轮询），Turn 终态且追平后流结束；等待超时输出 `: keepalive`（测试中把常量 monkeypatch 为 0.05）；`Last-Event-ID` 续接只收新事件。
- [ ] **Step 2: 实现**：SSE 循环 = `events_after` 下发 → `has_active_turns` 为 False 则返回 → `notifier.wait`；超时 `yield b": keepalive\n\n"`；超过最长时间返回。Redis 适配器：`publish` 用 `PUBLISH`；`wait` 用独立 pubsub 订阅并 `get_message(timeout=...)`，异常时返回 False（兜底读库）。
- [ ] **Step 3: 测试通过**；`pytest apps/tap-ai-backend/tests -q -k "stream or conversation"`。
- [ ] **Step 4: 提交** `feat: drive conversation sse from event notifications`

### Task 9: 前端流式渲染

**Files:**
- Modify: `apps/tap-ai-frontend/src/features/conversations/api/client.ts`（SSE 解析）、会话状态模型（`grep -rn "turn.completed" apps/tap-ai-frontend/src --include=*.ts*` 找到 reducer/hook）、回答渲染组件（显示临时文本与光标）
- Test: 同目录 `*.test.ts(x)`

**Interfaces:**
- Consumes: 事件 `answer.delta {text}`、`answer.reset {attempt}`、`turn.completed`。
- Produces: Turn 视图状态 `streamingText: string | null`。

- [ ] **Step 1: 失败测试**：reducer 依次处理 `turn.started`、3 个 `answer.delta`、`answer.reset`、2 个 `answer.delta`、`turn.completed`：中途 `streamingText` 正确累积与清空；`turn.completed` 后 `streamingText=null` 并触发拉取 Turn 详情；流结束但 Turn 仍运行时以最新 `Last-Event-ID` 重连；组件在流式期间不渲染引用、显示光标（`aria-busy="true"`）。
- [ ] **Step 2: 实现。**
- [ ] **Step 3: 测试**：`corepack pnpm --filter @tap/ai-frontend exec vitest run src/features/conversations` 与 `corepack pnpm --filter @tap/ai-frontend run check`。
- [ ] **Step 4: 提交** `feat: render streaming answers in tap ai`

### Task 10: golden set、运行器、命令与 CI

**Files:**
- Create: `apps/tap-ai-backend/tests/quality/golden/corpus/`（≥ 6 篇 Markdown：账户锁定策略、发布流程、数据保留、冲突的旧/新密码规则、审批流程、术语表）、`kb-golden.v1.jsonl`（≥ 20 条：fact ≥ 10、procedure ≥ 4、abstain ≥ 4）、`baseline.fake.json`
- Create: `apps/tap-ai-backend/src/tap/quality/golden.py`（加载、运行、打分、比较）与 `scripts/run-quality-golden.py`（CLI：`--real`、`--write-baseline PATH`、`--report-dir`）
- Create: `apps/tap-ai-backend/tests/quality/test_golden_regression.py`
- Modify: `src/tap/testing/deterministic_model_gateway.py`（回答模式：选证据中与问题词重叠最高的一句作为 claim，标签取该证据；无重叠 ≥ 2 个词则拒答）、`Makefile`（`quality-golden`、`quality-golden-real`、`check` 中 ruff/mypy 列表加入新脚本）、`.gitignore`（`apps/tap-ai-backend/.quality/`）
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class GoldenCase: id: str; category: str; question: str; sources: tuple[str, ...]; abstain: bool; cited_documents: tuple[str, ...]; must_contain: tuple[str, ...]; relations: tuple[str, ...]
  def load_cases(path: Path) -> tuple[GoldenCase, ...]
  async def run_golden(cases, *, corpus_dir: Path, gateway: ModelGateway) -> GoldenReport
  def compare(report: GoldenReport, baseline: Mapping[str, Mapping[str, float]]) -> list[str]  # 回退描述，空表示通过
  ```
  `GoldenReport.metrics: dict[category | "total", dict[metric, float]]`，指标 `citationRecall`、`keywordRecall`、`abstainAccuracy`、`answered`（子项目 5 将加 `relationRecall`）。
- 运行器装配：用契约测试使用的内存检索/文档实现（`grep -rn "class InMemory" apps/tap-ai-backend/src/tap/modules/knowledge apps/tap-ai-backend/src/tap/testing`）摄入语料，经知识回答服务的公开入口回答。

- [ ] **Step 1: 写语料与用例**（中文为主，含 2 条英文问题验证跨语言检索）。
- [ ] **Step 2: 失败测试**：`load_cases` 校验（未知类别、缺字段报错）；`compare` 对任一指标下降返回描述；`test_golden_regression`：假网关运行全部用例，`compare(report, baseline) == []`。
- [ ] **Step 3: 实现运行器与确定性回答模式**；首次运行后用 `--write-baseline apps/tap-ai-backend/tests/quality/golden/baseline.fake.json` 生成基线，人工检查分数合理（fact `citationRecall` 应 ≥ 0.8，abstain 应 1.0），不合理先修运行器或语料。
- [ ] **Step 4: Makefile**：
  ```make
  quality-golden: ## golden set regression with the deterministic model
  	uv run --project apps/tap-ai-backend python scripts/run-quality-golden.py
  quality-golden-real: ## golden set report with the configured real model
  	@set -eu; if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
  	TAP_GOLDEN_REAL_MODEL=1 uv run --project apps/tap-ai-backend python scripts/run-quality-golden.py --real
  ```
  并加入 `.PHONY`。
- [ ] **Step 5: CI** `.github/workflows/ci.yml`：`on: [push, pull_request]`；job `test`（`ubuntu-latest`）：`astral-sh/setup-uv@v6`、`actions/setup-node@v4`（node 22）、`corepack enable`、`uv sync --frozen --all-groups`、`corepack pnpm install --frozen-lockfile`、`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests -q`、`corepack pnpm --filter @tap/ai-frontend exec vitest run`。
- [ ] **Step 6: 运行** `make quality-golden` 与 `pytest apps/tap-ai-backend/tests/quality -q`。
- [ ] **Step 7: 提交** `feat: add golden set regression for knowledge answers`

### Task 11: 文档与总纲

**Files:**
- Modify: `docs/architecture.md`（流式与通知、重试策略、golden set 一节）、`docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 1 工程完成勾选、子项目 3 状态与 plan 链接）、`docs/superpowers/plans/2026-10-01-product-roadmap.md`（子项目 3 行）
- Create: `docs/guides/2026-11-03-golden-set.md`（格式、命令、写真实基线）

- [ ] **Step 1: 全量验证** `make check`、`make test`（记录 MySQL 跳过摘要）；能启动隔离环境时 `make demo-e2e`。
- [ ] **Step 2: 更新文档**，`git diff --check`。
- [ ] **Step 3: 提交** `docs: record reliable answering completion`
