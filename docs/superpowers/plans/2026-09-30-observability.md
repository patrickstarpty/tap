# 可观测性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个会话 Turn 与后台任务写出一条 OpenTelemetry trace，每次模型调用持久化元数据与原文到 MySQL，前端在回答下展示调用链、token 与成本面板。

**Architecture:** 新增 `tap.platform.telemetry`：进程级 TracerProvider，经 `BatchSpanProcessor` 写入 MySQL `trace_span`（可选追加 OTLP → Phoenix）。归属信息（项目作用域、turn、job、attempt）放在 contextvar `TraceBinding` 中，由 `TraceBindingProcessor` 在 span 开始时写成属性，因此各埋点只需开 span。`LiteLLMModelGateway._execute` 是三种模型调用的唯一入口，在此开 GenAI span 并经新端口 `ModelCallRecorder` 写 `model_call` / `model_call_content`。Turn 的 trace 通过 `chat_turn.traceparent` 从 API 传到 worker。前端只读新增的 trace 与 model-call 接口。

**顺序说明：** 与 spec 第 6 节不同，原型同步（Task 10）排在前端实现（Task 11）之前，前端按原型实现。SSE 契约修正（Task 8）依赖 Task 4 的用量累计。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy Core、Alembic、OpenTelemetry Python SDK、pytest、uv；React/TypeScript/antd/TanStack Query/Vitest/Playwright；Docker Compose；Phoenix。

**Spec:** `docs/superpowers/specs/2026-09-30-observability-design.md`

## Global Constraints

- 后端代码根 `apps/tap-ai-backend/src/tap/`（下文省略）；AI 模块在 `modules/ai/`（spec 中的 `ai/ports/...` 即 `modules/ai/ports/...`）。
- 新依赖只有 `opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`，在 `apps/tap-ai-backend/pyproject.toml` 精确锁版本（`uv add` 时的最新发布版），更新仓库根 `uv.lock`。
- 迁移唯一新文件 `migrations/versions/0026_observability.py`，`down_revision = "0025_managed_purge_obligation"`。
- 新表 `trace_span`、`model_call`、`model_call_content` 定义在共享 `metadata` 上并调用 `augment_project_table`，加入 `platform/db/registry.py` 的 `BUSINESS_TABLES` 与 `tests/architecture/test_migration_metadata.py` 的 `EXPECTED_TABLES`。
- `trace_span.attributes` 序列化上限 65536 字节；超出按"先删最大属性"截断并置 `tap.truncated=true`。
- `BatchSpanProcessor(schedule_delay_millis=5000, max_export_batch_size=512)`。
- `cost_usd` 取响应头 `x-litellm-response-cost`，`DECIMAL(18,8)`，缺失或非法为 NULL。
- 原文：`request_json` 是实际发送的 payload，图片 data URL 替换为 `sha256:<image_digest>`，不存图片字节；embedding 的 `response_text` 为 `{"dimension": <int>, "count": <int>}`，不存向量；`reasoning_text` 取 `choices[0].message.reasoning_content`，无则 NULL。
- 追踪数据永久保留，无清理任务、无保留期配置；删除文档或软删除会话不改写追踪行。
- 可观测性任何失败不影响业务：exporter / recorder 异常只记 `logging.warning`，不向调用方抛出。
- 服务名：`tap-ai-api`、`tap-ai-worker-generation`、`tap-ai-worker-ingestion`、`tap-ai-worker-graph`、`tap-ai-worker-test-design`。Relay、parser worker、一次性 CLI 不初始化追踪。
- span 名与属性键按 spec 第 4 节表格；本计划自有属性一律 `tap.` 前缀。
- 新配置只有 `OTEL_EXPORTER_OTLP_ENDPOINT`（可选，必须是 loopback URL）、`PHOENIX_IMAGE`、`PHOENIX_PORT=26006`，写入 `.env.example`。Phoenix 只绑定 `127.0.0.1`。
- 前端文案沿用 `locale === "zh" ? … : …` 三元；`features/conversations` 不得 import `features/knowledge`，跨特性连线放在 `widgets/tap`。
- 产品界面不出现演示开关或实现说明（`AGENTS.md`）。
- 仓库根目录执行命令；后端测试前缀 `uv run --project apps/tap-ai-backend pytest`（下文简写 `PYTEST`，路径相对 `apps/tap-ai-backend/`）；MySQL 集成测试需 `TAP_RUN_MYSQL_INTEGRATION=1`，使用 `owned_project_mysql` fixture。
- `git add` 只用显式路径。提交：小写祈使 Conventional Commit，结尾 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`；分支 `claude/v1-observability`。

## Review Focus

- 被脱敏检查拒绝的请求（`ModelGatewayRejected`）：`model_call` 记录一条 error，但 `request_json` 不得含原始 prompt/context，否则追踪表成为敏感信息旁路 —— Task 4 `test_rejected_request_records_error_without_prompt`。
- API 进程的 `turn.request` span 尚未导出（批处理 5 秒）时前端已拉取 trace：`turn.execute` 的父 span 缺失，瀑布图把孤儿 span 作为根展示而不是丢弃或报错 —— Task 11 `buildSpanTree keeps orphan spans as roots`。
- Turn 已进终态但 worker 的 span 尚未写入：前端首拉缺少最新 attempt 的 `turn.execute`，最多再拉 3 次（间隔 2 秒），之后按现有数据展示 —— Task 11 `refetches trace until latest attempt execute span appears`。
- 迁移前的历史 Turn（`traceparent` 为 NULL）：`traceId` 为 null，trace 接口 404，回答下保留原"执行记录"而不是报错 —— Task 9 `test_turn_without_traceparent_returns_404`，Task 11 `falls back to activity when traceId is null`。
- 用其他项目的 `call_id` 请求 model-call 详情：404，不泄露原文 —— Task 9 `test_model_call_from_other_project_is_not_found`。

---

### Task 1: 依赖与追踪核心

**Files:**
- Modify: `apps/tap-ai-backend/pyproject.toml`、`uv.lock`
- Create: `platform/telemetry/__init__.py`、`platform/telemetry/tracing.py`
- Modify: `apps/tap-ai-backend/tests/conftest.py`（新增 fixture）
- Test: `apps/tap-ai-backend/tests/unit/telemetry/test_tracing.py`

**Interfaces:**
- Produces（均由 `tap.platform.telemetry` 导出）：
  - `@dataclass(slots=True) class UsageTally: input_tokens: int = 0; output_tokens: int = 0`
  - `@dataclass(frozen=True, slots=True) class TraceBinding: scope: ProjectScopeContext | None = None; turn_id: str | None = None; job_id: str | None = None; job_kind: str | None = None; attempt: int | None = None; usage: UsageTally = field(default_factory=UsageTally)`
  - `def current_binding() -> TraceBinding`
  - `@contextmanager def bind_trace(*, scope=None, turn_id=None, job_id=None, job_kind=None, attempt=None, fresh_usage: bool = False) -> Iterator[TraceBinding]`：与外层绑定合并（未给的字段继承）；`fresh_usage=True` 时新建 `UsageTally`。
  - `class TraceBindingProcessor(SpanProcessor)`：`on_start` 写 `tap.scope.enterprise_id`、`tap.scope.project_id`、`tap.scope.actor_id`、`tap.scope.identity_mode`（取 `scope_values(scope)` 对应值）、`tap.turn_id`、`tap.job_id`、`tap.job_kind`、`tap.attempt`，值为 None 的不写。
  - `@contextmanager def span(name: str, attributes: Mapping[str, AttributeValue] | None = None, *, context: Context | None = None) -> Iterator[Span]`：异常时 `set_status(ERROR, type(exc).__name__)`、`record_exception` 后重新抛出。
  - `def inject_traceparent() -> str | None`（当前 span 无效时返回 None）；`def extract_traceparent(value: str | None) -> Context | None`（非法值返回 None）；`def trace_id_of(traceparent: str | None) -> str | None`。
  - `def configure_tracing(service_name: str, exporters: Sequence[SpanExporter]) -> TracerProvider`：仅当全局仍是默认 Proxy provider 时注册（含 `TraceBindingProcessor` 与每个 exporter 的 `BatchSpanProcessor`），否则返回现有 provider。
  - `async def flush_traces(timeout_ms: int = 5000) -> None`：`asyncio.to_thread(provider.force_flush, timeout_ms)`，异常只告警。
- Test fixture：`span_recorder`（session 级注册 `TraceBindingProcessor` + `SimpleSpanProcessor(InMemorySpanExporter)`，每个测试前 `clear()`，返回 exporter）。

- [ ] **Step 1: 添加依赖** `uv add --project apps/tap-ai-backend opentelemetry-api==<x> opentelemetry-sdk==<x> opentelemetry-exporter-otlp-proto-http==<y>`（api/sdk 同版本，exporter 取与之配套的版本），确认 `pyproject.toml` 为精确版本。
- [ ] **Step 2: 写失败测试** `tests/unit/telemetry/test_tracing.py`：
  - `test_binding_attributes_are_stamped_on_every_span`：`bind_trace(scope=s, turn_id="t1", attempt=2)` 内嵌套两个 `span`，两者都有 `tap.scope.project_id == s.project_id`、`tap.turn_id == "t1"`、`tap.attempt == 2`，且无 `tap.job_id` 键。
  - `test_nested_binding_inherits_outer_fields`：外层 `turn_id`，内层只给 `attempt`，`current_binding().turn_id` 仍为外层值。
  - `test_span_marks_error_and_reraises`：`span` 内抛 `ValueError`，异常外传，导出 span 状态为 ERROR。
  - `test_traceparent_round_trip`：`span("a")` 内 `inject_traceparent()` 得到 55 字符值；`extract_traceparent(value)` 作为 `context` 开的新 span 与原 span 同 `trace_id`，父为原 span；`trace_id_of(value)` 为 32 位十六进制。
  - `test_invalid_traceparent_yields_none`：`extract_traceparent("garbage")`、`extract_traceparent(None)` 均为 None。
- [ ] **Step 3: 运行确认失败** `PYTEST tests/unit/telemetry/test_tracing.py -v` → FAIL（`ModuleNotFoundError: tap.platform.telemetry`）。
- [ ] **Step 4: 实现** `platform/telemetry/tracing.py` 与 `__init__.py`；traceparent 用 `opentelemetry.propagate` 的 `TraceContextTextMapPropagator`。
- [ ] **Step 5: 运行确认通过**，同一命令 → PASS；`uv run --project apps/tap-ai-backend mypy src/tap/platform/telemetry` 无错误。
- [ ] **Step 6: 提交** `feat: add telemetry tracing core`。

---

### Task 2: 迁移 0026 与 `MysqlSpanExporter`

**Files:**
- Create: `migrations/versions/0026_observability.py`、`platform/telemetry/schema.py`、`platform/telemetry/mysql_exporter.py`
- Modify: `modules/chat/adapters/mysql.py`（`chat_turn` 新增列）、`platform/db/registry.py`、`tests/architecture/test_migration_metadata.py`
- Test: `apps/tap-ai-backend/tests/integration/test_mysql_span_exporter.py`

**Interfaces:**
- Consumes: Task 1 的 `span`、`bind_trace`、`TraceBindingProcessor`。
- Produces:
  - `platform/telemetry/schema.py`：`trace_span`、`model_call`、`model_call_content` 三个 `Table`，列名、类型、可空性、主键、索引严格按 spec 第 3 节；`trace_span` 索引 `ix_trace_span_project_turn (project_id, turn_id)`，`model_call` 索引 `ix_model_call_trace (trace_id)`，`model_call_content.call_id` 外键到 `model_call.call_id`。若 `test_migration_metadata` 要求 `project_id` 进主键，按 `0019_test_design_model_calls.py` 先例并入。
  - `chat_turn.traceparent = Column(String(55), nullable=True)`。
  - `def sync_database_url(url: str) -> str`：把 `mysql+asyncmy` 改为 `mysql+pymysql`。
  - `class MysqlSpanExporter(SpanExporter): def __init__(self, engine: sqlalchemy.Engine) -> None`；`export(spans) -> SpanExportResult`。
- 写入规则：
  - 无 `tap.scope.project_id` 的 span 跳过（仍会进 OTLP 导出器）。
  - 作用域五列取自 `tap.scope.*` 属性（`identity_origin = identity_mode.upper()`，同 `scope_values`），这些 `tap.scope.*` 键不写进 `attributes` JSON；`turn_id`、`job_id` 列取自 `tap.turn_id`、`tap.job_id`。
  - `service_name` 取 `span.resource.attributes["service.name"]`；`status` 为 `error`（ERROR）或 `ok`；`started_at` 为 UTC `DATETIME(6)`；`duration_ms = (end_time - start_time) // 1_000_000`。
  - 截断：JSON 序列化超 65536 字节时，按单个属性序列化长度从大到小删除，直到加上 `"tap.truncated": true` 后不超上限。
  - 用 `insert(...).prefix_with("IGNORE")` 批量写一次事务；任何异常 `logging.warning` 并返回 `SpanExportResult.FAILURE`，不抛出。

- [ ] **Step 1: 写失败测试** `tests/integration/test_mysql_span_exporter.py`（`owned_project_mysql`，用 `sync_database_url(owned_project_database_url(...))` 建同步 engine；provider 用 `SimpleSpanProcessor(MysqlSpanExporter(engine))` 在测试内局部创建 tracer，不动全局）：
  - `test_scoped_span_is_written_with_binding_columns`：绑定 scope、`turn_id="t1"`、`attempt=1` 的父子两个 span → 两行，`parent_span_id` 正确，`turn_id == "t1"`，`attributes` 中无 `tap.scope.project_id`，`service_name` 为 provider resource 值。
  - `test_error_span_records_status_message`：异常 span → `status == "error"`，`status_message == "ValueError"`。
  - `test_oversized_attributes_are_truncated`：属性 `big` 为 70000 字符、`small` 为 `"x"` → 存储 JSON 字节数 ≤ 65536，无 `big`，`small == "x"`，`tap.truncated is True`。
  - `test_unscoped_span_is_skipped`：无绑定的 span → 0 行，返回 `SUCCESS`。
  - `test_database_failure_returns_failure_without_raising`：engine 指向已关闭端口 → `export` 返回 `FAILURE`，`caplog` 有 warning。
- [ ] **Step 2: 更新架构测试** 在 `EXPECTED_TABLES` 加三张表。
- [ ] **Step 3: 运行确认失败** `TAP_RUN_MYSQL_INTEGRATION=1 PYTEST tests/integration/test_mysql_span_exporter.py tests/architecture/test_migration_metadata.py -v` → FAIL。
- [ ] **Step 4: 实现** 表、`chat_turn.traceparent`、registry、迁移（`upgrade` 建三表并 `add_column`；`downgrade` 仿 0025：表中有数据时拒绝删除）、`MysqlSpanExporter`。
- [ ] **Step 5: 运行确认通过**，同一命令 → PASS；`make check` 中的 `scripts/check-schema-drift.py`、`scripts/check-migration.py` 通过。
- [ ] **Step 6: 提交** `feat: persist trace spans to mysql`。

---

### Task 3: `ModelCallRecorder` 端口与 MySQL 适配器

**Files:**
- Create: `modules/ai/domain/model_calls.py`、`modules/ai/ports/model_calls.py`、`modules/ai/adapters/mysql_model_calls.py`
- Test: `apps/tap-ai-backend/tests/integration/test_mysql_model_call_recorder.py`

**Interfaces:**
- Consumes: Task 2 的 `model_call`、`model_call_content` 表。
- Produces:
  - `@dataclass(frozen=True, slots=True) class ModelCallRecord`：`call_id: str`、`scope: ProjectScopeContext`、`trace_id: str | None`、`span_id: str | None`、`turn_id: str | None`、`job_id: str | None`、`operation: ModelOperation`、`model_name: str`、`upstream_model: str | None`、`provider: str | None`、`input_tokens: int | None`、`output_tokens: int | None`、`cost_usd: Decimal | None`、`latency_ms: int`、`attempts: int`、`status: Literal["ok", "error"]`、`error_code: str | None`、`gateway_call_id: str | None`、`provider_request_id: str | None`、`created_at: datetime`、`request_json: str`、`response_text: str | None`、`reasoning_text: str | None`。
  - `class ModelCallRecorder(Protocol): async def record(self, call: ModelCallRecord) -> None; def degraded(self) -> bool`
  - `class MysqlModelCallRecorder: def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None`：一次独立短事务 `async with self._sessions() as session, session.begin()` 写两张表（仿 `knowledge/adapters/mysql_audit.py`）；异常 `logging.warning`、不抛出，并置 `degraded()` 为 True；下一次成功写入复位为 False。

- [ ] **Step 1: 写失败测试**：
  - `test_record_writes_metadata_and_content`：写一条 ok 记录 → 两表各一行，`cost_usd == Decimal("0.00012345")`，`request_json` 原样。
  - `test_error_record_allows_null_usage_and_model`：`status="error"`、`upstream_model/provider/input_tokens/cost_usd` 为 None → 写入成功。
  - `test_failure_is_swallowed_and_marks_degraded`：先 `DROP TABLE model_call_content`（隔离库）→ `record` 不抛出，`degraded()` 为 True，`model_call` 也无残留行（同一事务回滚）。
  - `test_success_after_failure_clears_degraded`。
- [ ] **Step 2: 运行确认失败** `TAP_RUN_MYSQL_INTEGRATION=1 PYTEST tests/integration/test_mysql_model_call_recorder.py -v` → FAIL。
- [ ] **Step 3: 实现** 三个文件。
- [ ] **Step 4: 运行确认通过** → PASS。
- [ ] **Step 5: 提交** `feat: add model call recorder`。

---

### Task 4: Gateway 埋点与模型调用记录

**Files:**
- Modify: `modules/ai/adapters/litellm.py`、`modules/ai/domain/models.py`、`testing/deterministic_model_gateway.py`
- Test: `apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py`

**Interfaces:**
- Consumes: Task 1 的 `span`、`current_binding`；Task 3 的 `ModelCallRecord`、`ModelCallRecorder`。
- Produces:
  - `ModelResult` 追加 `call_id: str | None = None`、`cost_usd: Decimal | None = None`。
  - `LiteLLMModelGateway.__init__` 追加关键字参数 `recorder: ModelCallRecorder | None = None`。
  - `@dataclass(slots=True) class GatewayAttempts: count: int = 0`；`_post(self, request, payload, attempts: GatewayAttempts)` 每次发送前 `count += 1`；`DeterministicModelGateway._post` 同步改签名并置 `count = 1`。
  - `ModelHealth.notices` 在 `recorder.degraded()` 时包含 `"model-call-recording-degraded"`。
- 行为（全部在 `_execute` 内）：
  - 每次调用新建 `call_id = str(uuid4())`，开 span `chat {request.alias}`（chat、structured）或 `embeddings {request.alias}`（embed），属性 `gen_ai.operation.name`（`chat` / `embeddings`）、`gen_ai.request.model`、`tap.model_call_id`；成功后补 `gen_ai.response.model`、`gen_ai.provider.name`、`gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens`。
  - 成功时把 usage 累加到 `current_binding().usage`。
  - 结束时（成功或失败）调用一次 `recorder.record(...)`：`trace_id` / `span_id` 取该 span 上下文的十六进制值；`turn_id` / `job_id` 取 `current_binding()`；`latency_ms` 为单调时钟耗时；`error_code` 为异常的 message（`model-request-rejected` / `model-unavailable`）；`gateway_call_id`、`provider_request_id` 取 `_normalize` 已解析的值。
  - `request_json`：payload 构造完成时为 `json.dumps(payload)`，其中 `image_url.url` 替换为 `f"sha256:{image_digest}"`；payload 未构造（校验或脱敏拒绝）时只存 `{"model": alias, "operation": operation, "rejected": true}`，不含 prompt/context。
  - `response_text`：chat/structured 为 `choices[0].message.content` 原文；embed 为 `json.dumps({"dimension": len(vector), "count": len(body["data"])})`。
  - `cost_usd`：`Decimal(header)`，非有限或负数为 None。
  - recorder 调用包在 `try/except Exception` 中，异常只告警，不改变返回值或原异常。

- [ ] **Step 1: 写失败测试**（`configured_gateway(handler, ...)` 追加 `recorder=FakeRecorder()`，`FakeRecorder` 在测试文件内定义，收集记录；span 用 `span_recorder` fixture）：
  - `test_successful_chat_records_call_and_span`：mock 响应头含 `x-litellm-response-cost: 0.00012345`、`x-litellm-call-id`、`x-request-id` → 一条 ok 记录，`cost_usd == Decimal("0.00012345")`，`result.call_id == record.call_id`，span 名 `chat qwen-plus`，`tap.model_call_id == record.call_id`，`record.span_id` 等于该 span 的 id。
  - `test_missing_or_invalid_cost_header_is_null`：无头、`"abc"`、`"-1"`、`"NaN"` → `cost_usd is None`。
  - `test_request_json_matches_sent_payload`：handler 捕获请求体，`json.loads(record.request_json) == 捕获的请求体`。
  - `test_image_bytes_replaced_by_digest`：带图片请求 → `request_json` 不含 `base64,`，含 `sha256:<request 的 image_digest>`。
  - `test_embedding_records_dimension_and_count_only`：`json.loads(response_text) == {"dimension": <配置维度>, "count": 1}`。
  - `test_reasoning_content_is_recorded`：响应 message 含 `reasoning_content: "think"` → `reasoning_text == "think"`；无该字段 → None。
  - `test_retries_are_counted`：第一次 503、第二次 200 → `attempts == 2`。
  - `test_unavailable_call_records_error`：始终 503 → 抛 `ModelGatewayUnavailable`，记录 `status == "error"`、`error_code == "model-unavailable"`、`input_tokens is None`，span 状态 ERROR。
  - `test_rejected_request_records_error_without_prompt`：redact 函数改写输入 → 抛 `ModelGatewayRejected`，记录 `error_code == "model-request-rejected"`，`request_json` 不含 prompt 与 context 原文。
  - `test_recorder_failure_does_not_break_call`：recorder `record` 抛异常 → `chat` 正常返回。
  - `test_usage_accumulates_into_binding`：`bind_trace(fresh_usage=True)` 内两次 chat → `current_binding().usage` 为两次之和。
  - `test_health_reports_degraded_recorder_as_notice`：`degraded()` 为 True → `health().notices` 含 `"model-call-recording-degraded"`，`problems` 为空。
- [ ] **Step 2: 运行确认失败** `PYTEST tests/contract/test_litellm_model_gateway.py -v` → 新测试 FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**：同一命令 PASS；`PYTEST tests/contract/model_gateway_conformance.py tests/architecture -v` PASS（fake/litellm 一致性不回退）。
- [ ] **Step 5: 提交** `feat: trace and record every model gateway call`。

---

### Task 5: 进程初始化、配置与 Phoenix

**Files:**
- Modify: `entrypoints/tapper_runtime.py`（`TapperSettings`、`_create_embeddings`、各 runtime 工厂）、`entrypoints/tapper_api.py`、`entrypoints/tapper_generation_worker.py`、`entrypoints/tapper_ingestion_worker.py`、`entrypoints/tapper_graph_worker.py`、`entrypoints/tapper_test_design_worker.py`
- Create: `platform/telemetry/setup.py`
- Modify: `compose.yaml`、`.env.example`
- Test: `apps/tap-ai-backend/tests/unit/test_tapper_settings.py`（若已有设置测试文件则加在其中）、`apps/tap-ai-backend/tests/unit/telemetry/test_setup.py`

**Interfaces:**
- Consumes: Task 1 `configure_tracing`；Task 2 `MysqlSpanExporter`、`sync_database_url`；Task 3 `MysqlModelCallRecorder`；Task 4 `recorder=` 参数。
- Produces:
  - `TapperSettings.otel_exporter_otlp_endpoint: str | None = None`，由 `from_mapping` 从 `OTEL_EXPORTER_OTLP_ENDPOINT` 读取，非空时用 `_loopback_url` 校验。
  - `def start_tracing(service_name: str, settings: TapperSettings) -> TracerProvider`（`platform/telemetry/setup.py`；若因依赖方向不宜放在 platform，放 `entrypoints/tracing_setup.py`）：exporters = `[MysqlSpanExporter(create_engine(sync_database_url(settings.database_url), pool_pre_ping=True))]`，endpoint 非空时追加 `OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")`。
  - 每个 `main()` 在读取设置后调用一次 `start_tracing(<服务名>, settings)`。
  - `_create_embeddings` 与各 runtime 工厂把 `MysqlModelCallRecorder(sessions)` 传给 `LiteLLMModelGateway` / `DeterministicModelGateway`。

- [ ] **Step 1: 写失败测试**：
  - `test_otlp_endpoint_defaults_to_none`、`test_otlp_endpoint_must_be_loopback`（`http://10.0.0.5:6006` → `ValueError`）、`test_otlp_endpoint_accepts_loopback`。
  - `test_start_tracing_adds_otlp_exporter_only_when_configured`：monkeypatch `configure_tracing` 捕获 exporters，未配置时 1 个 `MysqlSpanExporter`，配置时第二个为 `OTLPSpanExporter`。
  - 在 `tests/architecture/test_model_gateway_composition.py` 追加 `test_runtime_gateway_has_model_call_recorder`：`_create_embeddings(e2e 设置).gateway._recorder` 是 `MysqlModelCallRecorder`。
- [ ] **Step 2: 运行确认失败** `PYTEST tests/unit/test_tapper_settings.py tests/unit/telemetry/test_setup.py tests/architecture/test_model_gateway_composition.py -v` → FAIL。
- [ ] **Step 3: 实现** 设置、`start_tracing`、入口调用与 recorder 连线。
- [ ] **Step 4: compose 与 `.env.example`**：`compose.yaml` 新增服务 `phoenix`，`image: ${PHOENIX_IMAGE}`，`profiles: [observability]`，`ports: ["127.0.0.1:${PHOENIX_PORT:-26006}:6006"]`，命名卷持久化 `/mnt/data`（`PHOENIX_WORKING_DIR=/mnt/data`）。`.env.example` 新增一组注释说明：`PHOENIX_IMAGE=arizephoenix/phoenix:<执行时最新发布 tag>`、`PHOENIX_PORT=26006`、`# OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:26006`（默认注释掉，说明需先 `docker compose --profile observability up -d phoenix`）。
- [ ] **Step 5: 运行确认通过**：Step 2 命令 PASS；`docker compose -f compose.yaml --profile observability config --quiet` 退出码 0；`bash -n .env.example` 通过。
- [ ] **Step 6: 提交** `feat: initialize tracing in tap ai processes`。

---

### Task 6: Turn 追踪传递

**Files:**
- Modify: `interfaces/http/routes/conversations.py`（`create`、`append`）、`modules/chat/adapters/mysql_conversations.py`（`_insert_turn`、`claim_queued`、Turn 读取）、`modules/chat/domain/conversations.py`（`ConversationTurn`）、`entrypoints/tapper_generation_worker.py`
- Test: `apps/tap-ai-backend/tests/integration/test_turn_trace_propagation.py`

**Interfaces:**
- Consumes: Task 1 `span`、`bind_trace`、`inject_traceparent`、`extract_traceparent`、`flush_traces`；Task 2 `chat_turn.traceparent`。
- Produces:
  - `ConversationTurn.traceparent: str | None = None`，所有从 `chat_turn` 构造 Turn 的读取路径都填充。
  - 路由 `create` / `append`：在 `bind_trace(scope=request.state.project_scope, turn_id=turn_id)` 内开 `turn.request`（属性 `tap.conversation_id`、`tap.turn_id`）包住 `service.create...` / `service.append(...)`；幂等 replay 分支不开 span。
  - `_insert_turn` 写 `traceparent=inject_traceparent()`。
  - worker 每个认领的 Turn：`bind_trace(scope=<该 Turn 的 ProjectScopeContext>, turn_id=turn.turn_id, attempt=turn.attempt, fresh_usage=True)` 内开 `span("turn.execute", context=extract_traceparent(turn.traceparent))`，结束前设 `tap.outcome`；Turn 处理结束（任何分支）后 `await flush_traces()`。`traceparent` 为 None 时 `turn.execute` 成为新 trace 的根。

- [ ] **Step 1: 写失败测试**（`owned_project_mysql` + `span_recorder`；按 `tests/integration` 现有 conversation 集成测试的方式构造 repository 与 service）：
  - `test_append_writes_traceparent_of_request_span`：在 `span("turn.request")` 内调用 `append_turn` → `chat_turn.traceparent` 的 trace id 等于该 span 的 trace id。
  - `test_claimed_turn_carries_traceparent`：`claim_queued` 返回的 Turn `traceparent` 与写入值相同。
  - `test_worker_execute_span_joins_request_trace`：以 `extract_traceparent` 开 `turn.execute` → 与 `turn.request` 同 trace，父为 `turn.request`，属性 `tap.attempt == 1`。
  - `test_reclaimed_turn_reuses_trace_with_new_attempt`：租约过期后重新认领 → 同一 `traceparent`，`attempt == 2`。
  - 在 `tests/contract/test_conversation_http.py` 追加 `test_append_route_opens_turn_request_span`：`span_recorder` 中有 `turn.request`，属性含 `tap.turn_id`。
- [ ] **Step 2: 运行确认失败** `TAP_RUN_MYSQL_INTEGRATION=1 PYTEST tests/integration/test_turn_trace_propagation.py tests/contract/test_conversation_http.py -v` → FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过** → PASS；`PYTEST tests/unit/chat tests/contract -q` 无回归。
- [ ] **Step 5: 提交** `feat: propagate turn traces from api to worker`。

---

### Task 7: 流程与后台任务 span

**Files:**
- Modify: `modules/chat/application/plan_answer.py`、`modules/knowledge/application/retrieve.py`、`modules/knowledge/application/graph_enrichment.py`、`modules/ai/application/insights_explanation.py`、`modules/chat/adapters/mysql_conversations.py`（`resolve_citations`）、`modules/knowledge/application/ingestion.py`、`modules/knowledge/adapters/flowchart_vision.py`、`modules/graph/application/worker.py`、`modules/test_management/application/generation.py`、`entrypoints/tapper_runtime.py`（`IngestionWorker` 传入 scope）
- Test: `apps/tap-ai-backend/tests/unit/telemetry/test_pipeline_spans.py`

**Interfaces:**
- Consumes: Task 1 `span`、`bind_trace`、`flush_traces`。
- Produces（span 名 → 位置 → 属性）：
  - `chat.plan` → `AnswerPlanner.plan` → `tap.plan.kind`（计划类型）。
  - `retrieval.search` → `AuthorizedRetrieval._retrieve`（每次调用一个 span，规划多路与流程图补充检索各自产生）→ `tap.retrieval.hit_count`、`tap.retrieval.chunk_ids`、`tap.retrieval.document_ids`、`tap.retrieval.scores`（三个同序列表，最多 32 项）、`tap.retrieval.exact_flowchart`。
  - `graph.enrich` → `GraphAnswerEnricher.enrich` → `tap.graph.snapshot_id`、`tap.graph.node_count`、`tap.graph.edge_count`。
  - `tool.insights.query` → `InsightsExplanationService.explain` 中每次 `get_insights` / `query_insights` → `tap.insights.query_id`、`tap.insights.metric_version`、`tap.insights.resource_refs`、`tap.insights.row_count`。
  - `citations.resolve` → `MysqlConversationRepository.resolve_citations` → `tap.citations.count`。
  - 任务根 span（同时 `bind_trace(scope=…, job_id=…, job_kind=…)`）：`job.ingestion` / `job.deletion`（`IngestionWorker._process_claimed`，`job_kind = job.kind.value`）、`job.graph_extraction`（graph worker 每个 claim）、`job.test_design`（test-design worker 每个 claim）；`run_once` 结束后 `await flush_traces()`。
  - `flowchart.recognize` → `ModelGatewayFlowchartVision.analyze`（摄取任务的子 span）。
  - `IngestionWorker.__init__` 追加 `scope: ProjectScopeContext | None = None`，`_assemble_worker_runtime` 传 `repository.scope`；graph / test-design 用已有 `self._scope`。

- [ ] **Step 1: 写失败测试**（`span_recorder`；复用各模块现有单元测试里的 fake 构造对象，只断言 span）：
  - `test_planner_emits_chat_plan_span`
  - `test_retrieve_emits_search_span_with_hits`：两个命中 → `tap.retrieval.hit_count == 2`，`chunk_ids` / `document_ids` / `scores` 长度均为 2。
  - `test_planned_retrieval_emits_one_span_per_query`：规划 3 路查询 → 3 个 `retrieval.search`。
  - `test_graph_enricher_emits_counts`
  - `test_insights_query_span_has_row_count`
  - `test_ingestion_job_root_span_binds_job`：处理一个 ingestion job → `job.ingestion` 为根，`tap.job_id` 为该 job，子 span（含其中的模型调用 span）都带同一 `tap.job_id` 与 `tap.scope.project_id`。
  - `test_graph_and_test_design_jobs_emit_root_spans`
- [ ] **Step 2: 运行确认失败** `PYTEST tests/unit/telemetry/test_pipeline_spans.py -v` → FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过** → PASS；`PYTEST tests/unit tests/contract -q` 无回归。
- [ ] **Step 5: 提交** `feat: add pipeline and background job spans`。

---

### Task 8: SSE 事件契约修正

**Files:**
- Modify: `entrypoints/tapper_generation_worker.py`（`stream_events` 183–198、Insights 分支 290–347）、`apps/tap-ai-frontend/src/widgets/tap/TapProductPrototype.tsx`（`AnswerActivity`）
- Test: `apps/tap-ai-backend/tests/unit/chat/test_turn_processor.py`、`apps/tap-ai-backend/tests/contract/test_worker_stream_events.py`（新建）、`apps/tap-ai-frontend/src/widgets/tap/AnswerActivity.test.tsx`

**Interfaces:**
- Consumes: Task 1 `current_binding().usage`；Task 6 的 attempt 绑定；Task 7 的 `tool.insights.query` / `retrieval.search` span（承接移出的审计字段）。
- Produces（发出的 payload 严格符合 `contracts/chat_stream.py`）：
  - `context.assembled`：`{"contextSnapshotId": turn.input_snapshot.snapshot_id, "tokenCount": current_binding().usage.input_tokens}`（本 attempt 模型调用的输入 token 合计）。
  - `stage.completed`：`{"stage": <名>, "durationMs": <该阶段单调时钟毫秒>}`；阶段名 `knowledge.answer`、`knowledge.search`、`insights.explanation`。原 Insights 审计字段与 `knowledge.search` 附带的 `sourceIds` 等改为写入对应 span 属性（`tap.insights.*`、`tap.retrieval.*`），不再进事件。
  - `retrieval.hits_ready`：`{"traceId": retrieval_summary.trace_id, "authorizedHitCount": …}`；`trace_id` 为 None 时不发该事件。
  - `AnswerActivity` 的来源数改用组件已有的 `sourceCount` prop，不再读 `payload.sourceCount`。

- [ ] **Step 1: 确认消费者**：`rg -n "sourceCount|knowledgeSearchPerformed|graphRunId|sourceIds" apps/tap-ai-frontend/src apps/tap-ai-backend/src`，记录除上述位置外的读取点；若有，在本任务内一并改为读取新字段或 span。
- [ ] **Step 2: 写失败测试**：
  - `tests/contract/test_worker_stream_events.py::test_knowledge_events_validate_against_contract`：知识问答路径产生的每个事件 `ChatEventEnvelope.model_validate(...)` 通过（按事件信封补齐必需字段）。
  - `test_insights_events_validate_against_contract`：Insights 路径同上，且 `stage.completed` 中无 `graphRunId`。
  - `test_hits_ready_omitted_without_trace_id`。
  - 改写 `test_turn_processor.py` 121–130 行的期望为新形状。
  - `AnswerActivity.test.tsx`：`shows source count from prop when context event has no sourceCount`。
- [ ] **Step 3: 运行确认失败** `PYTEST tests/contract/test_worker_stream_events.py tests/unit/chat/test_turn_processor.py -v`；`corepack pnpm --dir apps/tap-ai-frontend exec vitest run src/widgets/tap/AnswerActivity.test.tsx` → FAIL。
- [ ] **Step 4: 实现**。
- [ ] **Step 5: 运行确认通过** → 两条命令 PASS。
- [ ] **Step 6: 提交** `fix: emit chat stream events that match the contract`。

---

### Task 9: Trace 与 model-call 接口

**Files:**
- Modify: `contracts/http.py`、`interfaces/http/routes/conversations.py`（`_turn`、新增 trace 路由）、`interfaces/http/dependencies.py`、`interfaces/http/app.py`、`tests/conftest.py` 的 `validation_http_services`（或其所在处）
- Create: `interfaces/http/routes/model_calls.py`、`interfaces/http/trace_service.py`、`modules/ai/adapters/mysql_traces.py`
- Regenerate: `contracts/openapi/api.json`、`contracts/events/*.json`、`apps/tap-ai-frontend/src/shared/api/generated/schema.ts`、`apps/web` 生成物（`make contracts`）
- Test: `apps/tap-ai-backend/tests/contract/test_trace_http.py`、`apps/tap-ai-backend/tests/integration/test_mysql_trace_reader.py`

**Interfaces:**
- Consumes: Task 2/3 的表；Task 1 `trace_id_of`；Task 6 `ConversationTurn.traceparent`。
- Produces（契约模型沿用 `contracts/http.py` 现有 camelCase 基类）：
  - `ConversationTurnSummary.trace_id: str | None`（`trace_id_of(turn.traceparent)`）。
  - `TurnTraceSummary`：`total_duration_ms: int`（所有 span `max(end) - min(start)`）、`input_tokens: int`、`output_tokens: int`（ok 调用合计）、`cost_usd: Decimal | None`（有成本的 ok 调用合计，全部缺失为 None）、`cost_incomplete: bool`（任一 ok 调用成本为 NULL）、`requested_models: list[str]`、`upstream_models: list[str]`（按首次出现去重，upstream 去 NULL）、`attempt_count: int`。
  - `TraceSpanView`：`span_id`、`parent_span_id | None`、`name`、`status: Literal["ok","error"]`、`started_at: datetime`、`duration_ms: int`、`attributes: dict[str, JsonValue]`、`attempt: int | None`（取 `tap.attempt`）。
  - `ModelCallView`：`call_id`、`span_id | None`、`operation`、`model_name`、`upstream_model | None`、`provider | None`、`input_tokens | None`、`output_tokens | None`、`cost_usd | None`、`latency_ms`、`attempts`、`status`、`error_code | None`、`created_at`。
  - `TurnTrace`：`trace_id`、`summary: TurnTraceSummary`、`spans: list[TraceSpanView]`（按 `started_at` 升序）、`model_calls: list[ModelCallView]`。
  - `ModelCallDetail(ModelCallView)`：追加 `request: str`、`response: str | None`、`reasoning: str | None`。
  - `class TraceHttpService(Protocol)`：`async def turn_trace(self, scope: ProjectScopeContext, conversation_id: str, turn_id: str) -> TurnTrace | None`；`async def model_call(self, scope: ProjectScopeContext, call_id: str) -> ModelCallDetail | None`。MySQL 实现所有查询带 `scope_predicates`；turn 须属于该会话且会话未软删除，traceparent 为 NULL 返回 None。
  - 路由：`GET /conversations/{conversation_id}/turns/{turn_id}/trace`（`operation_id="conversation_turn_trace"`，沿用 conversations router 的 `project_authorization("knowledge.answer")`）；`GET /model-calls/{call_id}`（新 router，`operation_id="model_call_detail"`，同一授权，挂在 `/api/v1/projects/{project_id}` 下）。None → 复用 conversations 路由现有的 404 problem。

- [ ] **Step 1: 写失败测试**：
  - `tests/integration/test_mysql_trace_reader.py`：插入两个 attempt 的 span 与三条调用（ok 有成本、ok 无成本、error）→ `test_summary_aggregates_ok_calls`（token 只计 ok，`cost_incomplete is True`，`attempt_count == 2`，`upstream_models` 无 NULL）；`test_turn_without_traceparent_returns_none`；`test_turn_of_other_conversation_returns_none`；`test_soft_deleted_conversation_trace_rows_remain`（软删除后三表行数不变）。
  - `tests/contract/test_trace_http.py`（fake `TraceHttpService` 注入 `validation_http_services`）：`test_trace_route_returns_turn_trace`（camelCase 字段 `costIncomplete`、`modelCalls`）；`test_turn_without_traceparent_returns_404`；`test_model_call_detail_includes_content`；`test_model_call_from_other_project_is_not_found`；`test_trace_routes_require_knowledge_answer_authorization`（按现有授权测试方式拒绝时 403）；`test_turn_summary_exposes_trace_id`；`test_openapi_declares_trace_routes`。
- [ ] **Step 2: 运行确认失败** `TAP_RUN_MYSQL_INTEGRATION=1 PYTEST tests/integration/test_mysql_trace_reader.py tests/contract/test_trace_http.py -v` → FAIL。
- [ ] **Step 3: 实现** 并连线到 `create_api_runtime`。
- [ ] **Step 4: 生成契约** `make contracts`；`git diff --stat contracts apps/tap-ai-frontend/src/shared/api/generated` 只含新增路由与字段。
- [ ] **Step 5: 运行确认通过**：Step 2 命令 PASS；`PYTEST tests/contract/test_generated_contracts.py -v` PASS。
- [ ] **Step 6: 提交** `feat: expose turn trace and model call apis`。

---

### Task 10: 原型同步调用链面板

**Files:**
- Create: `apps/web/src/widgets/tap/prototype/AnswerTrace.tsx`、`apps/web/src/widgets/tap/prototype/sampleTrace.ts`
- Modify: `apps/web/src/widgets/tap/prototype/DocumentReview.tsx`（`KnowledgeAnswer` completed 分支）、相应 CSS
- Test: `apps/web/src/widgets/tap/prototype/AnswerTrace.test.tsx`
- Assets: `docs/assets/observability/2026-09-30-prototype-answer-{before,after}.png`、`…-trace-expanded.png`、`…-model-call-drawer.png`

**Interfaces:**
- Produces（仅原型内部，不与 `apps/tap-ai-frontend` 共享组件）：`AnswerTrace({ trace }: { trace: SampleTrace })`，展示与 Task 11 相同的汇总行、瀑布图、attempt 标签与"请求 / 返回 / 推理"抽屉；`sampleTrace.ts` 固定 fixture：2 个 attempt（第 1 个模型调用失败）、`chat.plan`、2 个 `retrieval.search`、`graph.enrich`、`citations.resolve`、`chat qwen-plus`、一条缺成本的调用。

- [ ] **Step 1: 改动前截图**：`corepack pnpm --dir apps/web dev --port 15176`，按 `docs/guides/2026-09-22-product-prototype-baseline.md` "截图与交互回归"要求（1280×720，2×）在 `/prototype` Tapper 回答处截 `before`。
- [ ] **Step 2: 写失败测试** `AnswerTrace.test.tsx`：`collapsed row shows duration tokens cost and models`（含 `qwen-plus → dashscope/qwen-plus`）；`expanding shows indented waterfall with failed span highlighted`；`attempt tabs switch visible spans`；`model call span opens drawer with request response reasoning tabs`。
- [ ] **Step 3: 运行确认失败** `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/prototype/AnswerTrace.test.tsx` → FAIL。
- [ ] **Step 4: 实现** 并接入 `KnowledgeAnswer`；不加演示开关或说明文字。
- [ ] **Step 5: 运行确认通过** → PASS；`corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.test.tsx` PASS（模块导航基线）。
- [ ] **Step 6: 改动后截图** `after`、展开瀑布图、抽屉三张；人工核对 Tapper / Test Management / Test Insights / Low Code Automation 导航与浮动助手仍可用。
- [ ] **Step 7: 提交** `feat: add answer trace panel to prototype`（含截图）。

---

### Task 11: 前端调用链面板

**Files:**
- Modify: `apps/tap-ai-frontend/src/features/conversations/api/client.ts`、`apps/tap-ai-frontend/src/features/conversations/api/queries.ts`、`apps/tap-ai-frontend/src/widgets/tap/TapProductPrototype.tsx`（`AssistantResponse` 与调用处）、`TapProductPrototype.css`
- Create: `apps/tap-ai-frontend/src/features/conversations/trace/buildSpanTree.ts`、`…/trace/TracePanel.tsx`、`…/trace/ModelCallDrawer.tsx`、`…/trace/formatTraceSummary.ts`
- Test: 以上每个文件旁的 `*.test.ts(x)`，`client.test.ts`、`queries.test.tsx`

**Interfaces:**
- Consumes: Task 9 生成的 `components["schemas"]["TurnTrace"]`、`["ModelCallDetail"]`、`ConversationTurnSummary.traceId`；Task 10 的原型布局。
- Produces:
  - `ConversationClient.turnTrace(conversationId: string, turnId: string): Promise<TurnTrace>`；`ConversationClient.modelCall(callId: string): Promise<ModelCallDetail>`（URL `${baseUrl}/api/v1/projects/${projectId}/model-calls/${callId}`）。
  - `useTurnTrace(conversationId: string | null, turnId: string, options: { enabled: boolean; latestAttempt: number })`：`staleTime: Infinity`；`refetchInterval` 仅当返回数据中没有 `name === "turn.execute" && attempt === latestAttempt` 的 span 且已拉取次数 < 4 时为 2000，否则 false。
  - `useModelCallDetail(callId: string | null)`：`callId` 为 null 时不请求。
  - `buildSpanTree(spans: readonly TraceSpanView[]): SpanNode[]`，`type SpanNode = { span: TraceSpanView; depth: number; children: SpanNode[] }`；父不存在的 span 作为根；同级按 `startedAt` 排序。
  - `formatTraceSummary(summary: TurnTraceSummary, locale: "en" | "zh"): string`：`总耗时 · 输入/输出 tokens · 成本 · 请求模型 → 实际上游模型`，成本全缺为"成本未知"/"cost unknown"，部分缺失为 `$x+（部分成本未知）` / `$x+ (partly unknown)`。
  - `TracePanel({ trace, locale, onOpenModelCall, onOpenDocument })`：折叠行 + 展开瀑布图（缩进、条长 ∝ `durationMs`、`status === "error"` 标红）、attempt 标签（多 attempt 时显示，默认最新）、`retrieval.search` 行列出命中切片，点击调用 `onOpenDocument(documentId)`；模型调用行（有 `tap.model_call_id`）点击调用 `onOpenModelCall(callId)`。
  - `ModelCallDrawer({ callId, locale, onClose })`：antd `Drawer`，标签"请求 / 返回 / 推理"，打开时才请求详情，每个标签有复制按钮。
  - `AssistantResponse`：turn 为终态且 `traceId` 非 null 时用 `TracePanel` 替换 `AnswerActivity`；否则保留 `AnswerActivity`。`onOpenDocument` 在 widget 中打开现有来源面板里该文档的原件/切片视图（与"管理切片"同一入口）。

- [ ] **Step 1: 写失败测试**：
  - `client.test.ts`：`turnTrace calls trace url`、`modelCall calls project model-calls url`。
  - `buildSpanTree.test.ts`：`nests children under parents with depth`、`buildSpanTree keeps orphan spans as roots`、`sorts siblings by start time`。
  - `formatTraceSummary.test.ts`：`formats full summary`、`shows cost unknown when all costs missing`、`marks partial cost`。
  - `queries.test.tsx`：`does not fetch trace before turn is terminal`、`refetches trace until latest attempt execute span appears`（前两次响应无该 span、第三次有 → 共 3 次请求后停止）、`stops refetching after four attempts`。
  - `TracePanel.test.tsx`：`switches attempts`、`retrieval hit opens document`、`model call row opens drawer`。
  - `ModelCallDrawer.test.tsx`：`loads detail only when opened`、`shows request response reasoning tabs`。
  - `TapProductPrototype.conversations.test.tsx`：`shows trace panel for terminal turn with traceId`、`falls back to activity when traceId is null`。
- [ ] **Step 2: 运行确认失败** `corepack pnpm --dir apps/tap-ai-frontend exec vitest run src/features/conversations src/widgets/tap` → FAIL。
- [ ] **Step 3: 实现**，布局与文案对照 Task 10 原型。
- [ ] **Step 4: 运行确认通过** → PASS；`corepack pnpm --dir apps/tap-ai-frontend run lint`（含 dependency-cruiser）与 `typecheck` 通过。
- [ ] **Step 5: 提交** `feat: show call chain panel under answers`。

---

### Task 12: E2E、文档与全量验证

**Files:**
- Modify: `apps/tap-ai-frontend/tests/e2e/knowledge-conversation.spec.ts`
- Create: `docs/guides/2026-09-30-observability.md`
- Modify: `docs/architecture.md`、`docs/superpowers/plans/2026-09-29-v1-roadmap.md`

- [ ] **Step 1: E2E 断言** 在 "durable Conversation uses approved context…" 测试中，第一条回答完成后：展开"调用链"，可见 `turn.execute`、`retrieval.search` 与 `chat ` 开头的模型调用行；用 `page.request` 取 `/trace`，断言 UI 汇总行的输入/输出 token 等于接口 `summary.inputTokens` / `summary.outputTokens` 且均 > 0（假模型按词数返回确定 usage），成本显示"成本未知"；点开模型调用行，抽屉"请求"标签非空。
- [ ] **Step 2: 运行** `make demo-e2e` → 全部通过。
- [ ] **Step 3: 文档**：
  - 指南：三张表与 `chat_turn.traceparent` 说明、启用 Phoenix（`.env` 设 `OTEL_EXPORTER_OTLP_ENDPOINT`，`docker compose --profile observability up -d phoenix`，浏览器打开 `http://127.0.0.1:26006`）、按 `trace_id` 排障的 SQL 示例（查 span 树、查某 Turn 的模型调用与原文）、DashScope 成本缺失时在 `deploy/local/litellm/config.yaml` 的 `model_info` 配 `input_cost_per_token` / `output_cost_per_token`、原文永久保留与增长风险。
  - `docs/architecture.md`：§3 服务表加 `phoenix`（可选，profile `observability`）；§4 增加可观测性数据流（API `turn.request` → `chat_turn.traceparent` → worker `turn.execute` → MySQL / OTLP）；§5 更新可观测性差距行。
  - 总纲能力 5 工程完成项：把"自托管 Langfuse 加入 compose"改为"OpenTelemetry 埋点写入 MySQL，可选 OTLP 导出到 Phoenix（compose profile `observability`）"，与 spec 一致；满足后勾选该项，业务验证项保持未勾。
- [ ] **Step 4: 全量** `make check`、`make test`、`git diff --check` 通过；预览改动的 Markdown。
- [ ] **Step 5: 提交** `docs: add observability guide and update architecture`（E2E 改动可单独提交 `test: cover call chain in conversation journey`）。
