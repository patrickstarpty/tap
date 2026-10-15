# Langfuse 运营可观测性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 自托管 Langfuse 作为运营看板，由同步 worker 从 MySQL 增量推送已脱敏的链路数据与新增的用户反馈。

**Architecture:** 请求链路不变：链路数据照常写 MySQL，反馈经新 API 写 MySQL。新进程 `tapper_langfuse_sync_worker` 按 `recorded_at`/`updated_at` 游标扫描，脱敏、映射后经 `LangfuseSink` 端口推送，失败入失败表重试。Langfuse 复用现有 ClickHouse/Redis/MinIO，仅新增 web、worker、Postgres 三个容器。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy/Alembic、MySQL 8.4、Langfuse 自托管 v3 + Python SDK、React/TypeScript/Vitest、Playwright、Docker Compose。

**Spec:** [Langfuse 运营可观测性设计](../specs/2026-10-14-langfuse-ops-observability-design.md)

## Global Constraints

- MySQL 是业务数据权威；链路数据在 MySQL 永久保留，不做清理；Langfuse 只是可重建的展示副本，不回写 MySQL。
- 链路数据与反馈不得丢失：推送失败入 `langfuse_sync_failure` 重试，无次数上限。
- 发往 Langfuse 的每段文本先经 `ExportRedactor`；MySQL 原文不改。
- Langfuse 全部端口只绑定 `127.0.0.1`；`LANGFUSE_HOST` 必须通过现有 `_loopback_url` 校验。
- 请求链路不得导入或调用 Langfuse；Langfuse SDK 只出现在 `tap/modules/ai/adapters/langfuse_sink.py`。
- 静默窗口 `TAP_LANGFUSE_SYNC_SETTLE_SECONDS` 默认 60；轮询间隔 5 秒；每批最多 200 个 trace。
- 单字段截断上限 32 KB（32768 字符），标记 `[TRUNCATED n chars]`；脱敏失败替换为 `[REDACTION_FAILED]`。
- score 名 `user_feedback`，`up`=1、`down`=0；评论上限 500 字符。
- 仓库中不得出现任何具体机构的号码格式或名称；自定义规则文件只提供空示例。
- 不运行 `make demo-up` / `demo-down` / `demo-reset`；worktree 无 `.env`。
- Commit 主题小写、祈使句、Conventional Commits。

## Review Focus

- 游标与提交可见性：`recorded_at` 在 `cutoff` 之前但事务尚未提交的行会被跳过 → 依赖 60 秒静默窗口远大于导出事务时长；Task 5 测试用“游标推进后插入更早 `recorded_at` 的行”断言不会被扫到，并在指南中写明此前提。
- 同一 trace 在扫描中途仍有新 span 写入 → 下一轮必须再次整条重推（Task 6 测试 `test_trace_rescanned_after_new_span`）。
- 用户问题或评论含中英混排、全角数字、带空格/横线的手机号与卡号 → 仍需被遮盖（Task 3 测试 `test_formatted_numbers_are_masked`）。
- 对非本人会话、未完成或拒答的 Turn 提交反馈 → 404/409，不得写入（Task 4 测试）。
- Langfuse 不可达时 worker 不崩溃、游标照常推进、恢复后从失败表补齐（Task 6 测试 `test_sink_outage_then_recovery_loses_nothing`）。

---

### Task 1: Langfuse 部署与 Phoenix 下线

**Files:**
- Modify: `compose.yaml`（删除 `phoenix` 服务与 `phoenix-data` 卷；新增 `langfuse-web`、`langfuse-worker`、`langfuse-postgres`，profile `langfuse`）
- Create: `deploy/local/langfuse/bootstrap.sh`（幂等：在 insights ClickHouse 创建 `langfuse` 库与用户，在 `tap-minio` 创建 `langfuse` bucket）
- Modify: `Makefile`（新增 `langfuse-up`、`langfuse-down`，加入 `.PHONY`）
- Modify: `.env.example`（删除 Phoenix 段；新增 Langfuse 段，见 spec §6）

**Interfaces:**
- Produces: 本地 Langfuse `http://127.0.0.1:${LANGFUSE_PORT:-23000}`，预建项目与 API key（`LANGFUSE_INIT_*`），供 Task 2 与手工验收使用。

- [x] **Step 1: 写 compose 服务**

  镜像固定 tag（以 Task 2 确认的版本为准，先取当前最新 v3 稳定版）。`langfuse-web` 端口 `127.0.0.1:${LANGFUSE_PORT:-23000}:3000`；`langfuse-worker` 不暴露端口；`langfuse-postgres` 用 `postgres:17-alpine`，`mem_limit: 256m`，卷 `langfuse-postgres-data`。三者沿用 `clickhouse` 服务的 `cap_drop`/`security_opt`/资源限制写法。环境：`CLICKHOUSE_URL=http://clickhouse:8123`、`CLICKHOUSE_MIGRATION_URL=clickhouse://clickhouse:9000`、`CLICKHOUSE_DB=langfuse`、`CLICKHOUSE_USER/PASSWORD=${LANGFUSE_CLICKHOUSE_USER:-tap_langfuse}/${LANGFUSE_CLICKHOUSE_PASSWORD:-tap-local-langfuse}`、`CLICKHOUSE_CLUSTER_ENABLED=false`、`REDIS_CONNECTION_STRING=redis://redis:6379/1`、`LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse`、`LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT=http://tap-minio:9000`、`LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE=true`、凭据取 `TAPPER_S3_ACCESS_KEY/SECRET_KEY`、`TELEMETRY_ENABLED=false`、`LANGFUSE_INIT_ORG_ID/PROJECT_ID/PROJECT_PUBLIC_KEY/PROJECT_SECRET_KEY/USER_EMAIL/USER_PASSWORD`（本地示例值，从 `.env` 覆盖）。

- [x] **Step 2: 写 `deploy/local/langfuse/bootstrap.sh`**

  `docker compose exec clickhouse clickhouse-client` 执行 `CREATE DATABASE IF NOT EXISTS langfuse`、`CREATE USER IF NOT EXISTS ... IDENTIFIED BY ...`、`GRANT ALL ON langfuse.* TO ...`；不授予 `tap_insights.*`。MinIO bucket 用一次性 `minio/mc` 容器（固定 tag，`--network` 为 compose 网络）执行 `mc mb --ignore-existing`。

- [x] **Step 3: Makefile 目标**

  `langfuse-up`：`docker compose --profile insights up -d clickhouse` → 等待 healthy → `bash deploy/local/langfuse/bootstrap.sh` → `docker compose --profile langfuse up -d`。`langfuse-down`：`docker compose --profile langfuse stop` 后 `rm -f` 三个容器，不加 `-v`。

- [x] **Step 4: 验证**

  Run: `docker compose config --profiles >/dev/null && docker compose --profile langfuse config | grep -c '127.0.0.1'`
  Expected: 配置合法；`phoenix` 不再出现（`docker compose config | grep -c phoenix` 输出 0）。
  在有 Docker 的本机执行 `make langfuse-up`，浏览器打开 `http://127.0.0.1:23000` 能用 `.env.example` 中的示例账号登录；`clickhouse-client --user tap_insights_reader ... -q 'SHOW TABLES FROM langfuse'` 报权限错误。

- [x] **Step 5: Commit**

```bash
git add compose.yaml deploy/local/langfuse Makefile .env.example
git commit -m "feat: add local langfuse on shared middleware and drop phoenix"
```

### Task 2: SDK 能力验证（spike）

**Files:**
- Create（不提交）：`/tmp/langfuse_spike.py`
- Modify: `apps/tap-ai-backend/pyproject.toml`、`uv.lock`（添加 `langfuse==<确认版本>`）
- Modify: `docs/superpowers/specs/2026-10-14-langfuse-ops-observability-design.md` §8（写入结论）

**Interfaces:**
- Produces: spec §8 结论，决定 Task 7 的 `LangfuseSdkSink` 用 SDK 还是 OTLP 推 trace。

- [x] **Step 1:** 对 Task 1 的 Langfuse 运行 spike 脚本，逐项验证并记录：
  1. 能否以指定 32 位 hex trace id、16 位 hex span id 与历史 `start_time`/`end_time` 创建 span 与 generation；
  2. 同 id 重推是否覆盖而不重复；
  3. generation 的 `usage_details`、`cost_details` 是否进入看板成本统计；
  4. 指定 id 创建、覆盖、删除 score；对尚未入库 trace 的 score 是否接受；
  5. `CLICKHOUSE_DB=langfuse`、Redis `/1`、MinIO path-style 运行正常（Task 1 已启动即证明）。
- [x] **Step 2:** 把结论、Langfuse 镜像 tag 与 SDK 版本写入 spec §8；若第 1 项不满足，写明“trace 走 OTLP `/api/public/otel/v1/traces`，SDK 只用于 score”。修正 Task 1 镜像 tag（如需）。
- [x] **Step 3:** `uv add --project apps/tap-ai-backend langfuse==<版本>`；`uv run --project apps/tap-ai-backend python -c "import langfuse"` 成功。
- [x] **Step 4: Commit**

```bash
git add apps/tap-ai-backend/pyproject.toml uv.lock docs/superpowers/specs/2026-10-14-langfuse-ops-observability-design.md compose.yaml
git commit -m "chore: pin langfuse sdk and record capability findings"
```

### Task 3: 导出脱敏器

**Files:**
- Create: `apps/tap-ai-backend/src/tap/platform/redaction_patterns.py`（从 `pattern_redaction.py` 移出 `PRIVATE_START`、`SECRET`、`ASSIGNMENT_CANDIDATE`、`EMAIL`，去掉前导下划线）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/pattern_redaction.py`（改为导入上述常量，行为不变）
- Create: `apps/tap-ai-backend/src/tap/modules/ai/application/export_redaction.py`
- Create: `deploy/local/redaction/custom-rules.example.yaml`（`rules: []`）
- Test: `apps/tap-ai-backend/tests/unit/ai/test_export_redaction.py`

**Interfaces:**
- Produces:
  - `REDACTION_VERSION: str = "tap-export-v1"`
  - `@dataclass(frozen=True) class CustomRule: name: str; pattern: re.Pattern[str]`
  - `def load_custom_rules(path: Path | None) -> tuple[CustomRule, ...]`（YAML `rules: [{name, pattern}]`；`name` 仅 `[a-z0-9_]+`，否则 `ValueError`）
  - `class ExportRedactor: __init__(self, custom_rules: Sequence[CustomRule] = ()); redact(self, text: str | None) -> str | None; redact_value(self, value: object) -> object`（递归处理 dict/list 中的字符串）

- [x] **Step 1: 写失败测试**

```python
def test_pii_is_masked():
    r = ExportRedactor()
    out = r.redact("联系 a.b@example.com 或 13812345678，卡 4111 1111 1111 1111，身份证 11010519491231002X")
    assert "[EMAIL]" in out and "[PHONE]" in out and "[CARD_NUMBER]" in out and "[ID_NUMBER]" in out
    assert "13812345678" not in out and "4111" not in out

def test_formatted_numbers_are_masked():
    out = ExportRedactor().redact("电话 +86 138-1234-5678，香港 +852 9123 4567，ＨＫ A123456(7)")
    assert "1234-5678" not in out and "9123 4567" not in out and "A123456" not in out

def test_luhn_and_checksum_reject_random_digits():
    out = ExportRedactor().redact("订单 4111111111111112，编号 110105194912310021")
    assert "4111111111111112" in out and "110105194912310021" in out

def test_credentials_masked_without_rejecting_whole_text():
    out = ExportRedactor().redact('ok password="hunter2" tail')
    assert "hunter2" not in out and out.startswith("ok ") and out.endswith(" tail")

def test_unterminated_private_key_fails_field():
    assert ExportRedactor().redact("-----BEGIN PRIVATE KEY-----\nabc") == "[REDACTION_FAILED]"

def test_truncates_after_redaction():
    out = ExportRedactor().redact("x" * 40000)
    assert out == "x" * 32768 + "[TRUNCATED 7232 chars]"

def test_custom_rule_and_nested_values(tmp_path):
    p = tmp_path / "r.yaml"; p.write_text("rules:\n  - name: policy\n    pattern: 'P\\d{8}'\n")
    r = ExportRedactor(load_custom_rules(p))
    assert r.redact_value({"a": ["P12345678", 3]}) == {"a": ["[CUSTOM:policy]", 3]}

def test_none_passthrough():
    assert ExportRedactor().redact(None) is None
```

- [x] **Step 2:** Run `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_export_redaction.py -q`；Expected: FAIL（模块不存在）。
- [x] **Step 3: 实现**

  先做全角→半角归一后匹配（只用于匹配，替换作用于原文对应区间：逐字符归一不改变长度，可直接复用下标）。顺序：私钥块 → 凭据赋值 → 邮箱 → 身份证（内地 18 位校验位 ISO 7064 MOD 11-2；香港 `[A-Z]{1,2}\d{6}\(?[0-9A]\)?`）→ 卡号（13–19 位数字，允许空格/横线分组，Luhn）→ 手机（内地 `(?:\+?86[\s-]?)?1[3-9]\d(?:[\s-]?\d{4}){2}`；香港 `(?:\+?852[\s-]?)?[2-9]\d{3}[\s-]?\d{4}`，仅在带 `+852` 或前后为非数字时）→ 自定义规则 → 截断。私钥起始无结束标记时返回 `[REDACTION_FAILED]`。

- [x] **Step 4:** 同 Step 2 命令，Expected: PASS；再跑 `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge -q -k redact` 确认原脱敏器不变。
- [x] **Step 5: Commit**

```bash
git add apps/tap-ai-backend/src/tap/platform/redaction_patterns.py apps/tap-ai-backend/src/tap/modules/knowledge/adapters/pattern_redaction.py apps/tap-ai-backend/src/tap/modules/ai/application/export_redaction.py apps/tap-ai-backend/tests/unit/ai/test_export_redaction.py deploy/local/redaction
git commit -m "feat: add trace export redaction with pii rules"
```

### Task 4: 反馈的存储、服务与 HTTP API

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0032_answer_feedback.py`
- Create: `apps/tap-ai-backend/src/tap/modules/chat/domain/feedback.py`
- Create: `apps/tap-ai-backend/src/tap/modules/chat/adapters/mysql_feedback.py`（含 `answer_feedback` Table，`augment_project_table`）
- Create: `apps/tap-ai-backend/src/tap/modules/chat/application/feedback.py`
- Modify: `apps/tap-ai-backend/src/tap/platform/db/registry.py`（注册表）
- Modify: `apps/tap-ai-backend/src/tap/contracts/http.py`（`AnswerFeedbackRequest`、`AnswerFeedbackView`；`ConversationTurnSummary.my_feedback: AnswerFeedbackView | None = None`）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/routes/conversations.py`（PUT/DELETE 路由；`_turn` 填充 `my_feedback`）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/dependencies.py`（`HttpServices.feedback`、`feedback_service()`）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py`（装配）
- Regenerate: `contracts/`、`apps/tap-ai-frontend/src/shared/api/generated/schema.ts`（`make contracts`）
- Test: `apps/tap-ai-backend/tests/unit/chat/test_feedback_service.py`、`apps/tap-ai-backend/tests/integration/test_answer_feedback_mysql.py`、`apps/tap-ai-backend/tests/integration/test_answer_feedback_http.py`

**Interfaces:**
- Produces:
  - `Rating = Literal["up", "down"]`
  - `@dataclass(frozen=True) class AnswerFeedback: feedback_id: str; project_id: str; conversation_id: str; turn_id: str; actor_id: str; trace_id: str | None; rating: Rating | None; comment: str | None; created_at: datetime; updated_at: datetime`
  - `class FeedbackRepository(Protocol): async upsert(self, feedback: AnswerFeedback) -> AnswerFeedback; async get(self, turn_id: str, actor_id: str) -> AnswerFeedback | None; async for_turns(self, turn_ids: Sequence[str], actor_id: str) -> dict[str, AnswerFeedback]`
  - `class FeedbackService: __init__(self, repository, conversations: ConversationService, *, scope); async submit(self, conversation_id, turn_id, rating: Rating, comment: str | None) -> AnswerFeedback; async retract(self, conversation_id, turn_id) -> None; async mine(self, turn_ids) -> dict[str, AnswerFeedback]`
  - HTTP：`PUT /api/v1/projects/{project_id}/conversations/{conversation_id}/turns/{turn_id}/feedback`（body `{rating, comment?}` → `AnswerFeedbackView{rating, comment}`，operation_id `conversation_put_turn_feedback`）；`DELETE` 同路径 → 204（`conversation_delete_turn_feedback`）。
- 表列见 spec §2，`chat_id` 列名改用 `conversation_id`，与 API 一致；`trace_id` 由 `trace_id_of(turn.traceparent)` 填充。

- [x] **Step 1: 写失败测试（服务层，内存仓库）**

```python
async def test_submit_requires_completed_owned_turn(...):
    # turn state "abstained" / "running" → FeedbackNotAllowed ; 他人会话 → AuthorizationDenied
async def test_comment_only_with_down_and_bounded(...):
    # rating="up", comment="x" → 存储 comment=None；down + 501 字 → ValueError
async def test_switch_and_retract(...):
    # up → down 保留同一 feedback_id、created_at 不变、updated_at 变大；retract → rating None、comment None
async def test_mine_returns_only_active_feedback(...):
    # 已撤销的不出现在 mine() 结果中
```

  集成测试（`owned_project_database_url`）：`upsert` 同 `(project_id, turn_id, actor_id)` 只一行；`for_turns` 批量查询。HTTP 测试：PUT 两次幂等返回同一结果；DELETE 后 GET 会话的 turn `myFeedback` 为 null；未完成 Turn → 409；他人会话 → 404；`comment` 501 字 → 422。

- [x] **Step 2:** Run `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/chat/test_feedback_service.py apps/tap-ai-backend/tests/integration/test_answer_feedback_mysql.py apps/tap-ai-backend/tests/integration/test_answer_feedback_http.py -q`；Expected: FAIL。
- [x] **Step 3:** 实现迁移（`down_revision = "0031_cost_usd_precision"`；唯一键 `(turn_id, actor_id)` 经 `augment_project_table` 前缀为 project；索引 `(project_id, updated_at)`）、领域、仓库、服务、路由与装配。会话归属沿用 `ConversationService._owned`；只允许 `state == "completed"`，否则抛 `FeedbackNotAllowed` → 409 problem。`_turn()` 增加可选参数 `feedback: AnswerFeedback | None`，会话详情路由先 `mine()` 再映射。
- [x] **Step 4:** 同 Step 2 命令 PASS；`make contracts` 后 `git diff --stat contracts apps/tap-ai-frontend/src/shared/api/generated` 只含新增反馈类型；`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/architecture apps/tap-ai-backend/tests/contract -q` PASS。
- [x] **Step 5: Commit**

```bash
git add apps/tap-ai-backend contracts apps/tap-ai-frontend/src/shared/api/generated
git commit -m "feat: record answer feedback per turn"
```

### Task 5: 同步存储（迁移与 MySQL 读取）

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0033_langfuse_sync.py`（`trace_span.recorded_at` 列 + 回填 `started_at` + 索引；`langfuse_sync_cursor`；`langfuse_sync_failure`）
- Modify: `apps/tap-ai-backend/src/tap/platform/telemetry/schema.py`（新列与两张表）
- Create: `apps/tap-ai-backend/src/tap/modules/ai/domain/langfuse_sync.py`
- Create: `apps/tap-ai-backend/src/tap/modules/ai/ports/langfuse_sync.py`
- Create: `apps/tap-ai-backend/src/tap/modules/ai/adapters/mysql_langfuse_sync.py`
- Test: `apps/tap-ai-backend/tests/integration/test_mysql_langfuse_sync_store.py`

**Interfaces:**
- Produces（`domain/langfuse_sync.py`）：
  - `SyncKind = Literal["trace", "feedback"]`
  - `SpanRecord(span_id: str, parent_span_id: str | None, name: str, service_name: str, status: str, status_message: str | None, started_at: datetime, duration_ms: int, attributes: Mapping[str, object])`
  - `CallRecord(call_id: str, span_id: str | None, operation: str, model_name: str, upstream_model: str | None, provider: str | None, input_tokens: int | None, output_tokens: int | None, cost_usd: Decimal | None, status: str, error_code: str | None, request_json: str | None, response_text: str | None, reasoning_text: str | None)`
  - `TraceBundle(trace_id: str, project_id: str, actor_id: str, conversation_id: str | None, turn_id: str | None, job_id: str | None, spans: tuple[SpanRecord, ...], calls: tuple[CallRecord, ...])`
  - `FeedbackRecord(feedback_id: str, trace_id: str | None, turn_id: str, actor_id: str, rating: Literal["up","down"] | None, comment: str | None, updated_at: datetime)`
- Produces（`ports/langfuse_sync.py`）`class LangfuseSyncStore(Protocol)`：
  - `async scan_traces(self, *, cutoff: datetime, limit: int) -> tuple[list[str], datetime | None]`（返回 `(cursor, cutoff]` 内按 `recorded_at` 升序的去重 trace_id 与本批最大 `recorded_at`；不推进游标）
  - `async scan_feedback(self, *, cutoff: datetime, limit: int) -> tuple[list[FeedbackRecord], datetime | None]`
  - `async advance(self, name: SyncKind, position: datetime) -> None`
  - `async load_trace(self, trace_id: str) -> TraceBundle | None`
  - `async load_feedback(self, feedback_id: str) -> FeedbackRecord | None`
  - `async failures(self, kind: SyncKind, limit: int) -> list[str]`
  - `async record_failure(self, kind: SyncKind, subject_id: str, error: str) -> None`（`failure_count += 1`，`last_error` 截断 1000）
  - `async clear_failure(self, kind: SyncKind, subject_id: str) -> None`

- [x] **Step 1: 写失败集成测试**

```python
async def test_scan_traces_respects_cursor_and_cutoff(store, insert_span):
    # 三条 span recorded_at = t0, t1, t2(> cutoff) → 返回 [trace(t0), trace(t1)], max=t1
async def test_scan_is_not_advanced_until_advance_called(...)
async def test_row_inserted_behind_cursor_is_not_rescanned(...)  # Review Focus 1：advance(t1) 后插入 recorded_at=t0.5 → 不返回
async def test_load_trace_joins_calls_content_and_conversation(...)
    # bundle.conversation_id 来自 chat_turn.chat_id；calls 带 request_json/response_text；job trace conversation_id None
async def test_failures_roundtrip(...)  # record 两次 → failure_count 2；clear 后 failures() 为空
async def test_migration_backfills_recorded_at_from_started_at(...)
```

- [x] **Step 2:** Run `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_mysql_langfuse_sync_store.py -q`；Expected: FAIL。
- [x] **Step 3:** 实现迁移、schema 与适配器。`recorded_at` 用 `server_default=sa.text("CURRENT_TIMESTAMP(6)")`；`MysqlSpanExporter` 不需改动。游标表不存在行时视为 `1970-01-01`。
- [x] **Step 4:** 同 Step 2 PASS；`make schema-drift migration-check`（或 `uv run ... pytest apps/tap-ai-backend/tests/architecture/test_migration_metadata.py -q`）PASS。
- [x] **Step 5: Commit**

```bash
git add apps/tap-ai-backend
git commit -m "feat: add langfuse sync cursor and trace store"
```

### Task 6: 映射与同步服务

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/ai/application/langfuse_mapping.py`
- Create: `apps/tap-ai-backend/src/tap/modules/ai/application/langfuse_sync.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/ports/langfuse_sync.py`（加 `LangfuseSink`）
- Test: `apps/tap-ai-backend/tests/unit/ai/test_langfuse_mapping.py`、`apps/tap-ai-backend/tests/unit/ai/test_langfuse_sync.py`

**Interfaces:**
- Consumes: Task 3 `ExportRedactor`、`REDACTION_VERSION`；Task 5 领域类型与 `LangfuseSyncStore`。
- Produces:
  - `LangfuseObservation(id: str, parent_id: str | None, kind: Literal["span","generation"], name: str, start_time: datetime, end_time: datetime, level: Literal["DEFAULT","ERROR"], status_message: str | None, input: object, output: object, model: str | None, usage_details: dict[str, int], cost_details: dict[str, float], metadata: dict[str, object])`
  - `LangfuseTrace(id: str, name: str, session_id: str | None, user_id: str, environment: str, tags: tuple[str, ...], metadata: dict[str, object], observations: tuple[LangfuseObservation, ...])`
  - `LangfuseScore(id: str, trace_id: str, name: str, value: float, comment: str | None)`
  - `def to_langfuse_trace(bundle: TraceBundle, redactor: ExportRedactor, *, environment: str) -> LangfuseTrace`
  - `def feedback_score_id(turn_id: str, actor_id: str) -> str`（`uuid5(NAMESPACE_URL, f"tap:user_feedback:{turn_id}:{actor_id}").hex`）
  - `class LangfuseSink(Protocol): async push_trace(self, trace: LangfuseTrace) -> None; async upsert_score(self, score: LangfuseScore) -> None; async delete_score(self, score_id: str) -> None`
  - `@dataclass class SyncReport: traces_ok: int; traces_failed: int; feedback_ok: int; feedback_failed: int`
  - `class LangfuseSyncService: __init__(self, store, sink, redactor, *, environment: str, settle_seconds: int = 60, batch_size: int = 200, clock: Callable[[], datetime]); async run_once(self) -> SyncReport`

- [x] **Step 1: 写失败测试（映射）**

```python
def test_trace_identity_and_tags():
    t = to_langfuse_trace(bundle(conversation_id="c1", actor_id="a1", project_id="p1"), ExportRedactor(), environment="local")
    assert (t.id, t.session_id, t.user_id, t.environment) == (TRACE_ID, "c1", "a1", "local")
    assert "project:p1" in t.tags and t.metadata["tap.redaction_version"] == REDACTION_VERSION

def test_model_call_span_becomes_generation_with_usage_cost_and_redacted_io():
    g = generation_of(to_langfuse_trace(bundle_with_call(cost=Decimal("0.0012"), request='{"q":"13812345678"}'), ExportRedactor(), environment="local"))
    assert g.kind == "generation" and g.model == "qwen-upstream"
    assert g.usage_details == {"input": 10, "output": 20} and g.cost_details == {"total": 0.0012}
    assert "13812345678" not in json.dumps(g.input)

def test_failed_call_is_error_level(): ...
def test_span_times_and_parents_preserved(): ...  # end_time = started_at + duration_ms
def test_string_attributes_are_redacted(): ...
```

- [x] **Step 2: 写失败测试（服务，FakeStore + FakeSink）**

```python
async def test_unsettled_traces_are_not_pushed(): ...
async def test_trace_rescanned_after_new_span(): ...            # Review Focus 2
async def test_sink_outage_then_recovery_loses_nothing():       # Review Focus 5
    sink.fail = True; r1 = await svc.run_once()
    assert r1.traces_failed == 2 and store.cursor["trace"] == T1 and store.failures_of("trace") == {"tr1","tr2"}
    sink.fail = False; await svc.run_once()
    assert sink.pushed_ids == {"tr1","tr2"} and store.failures_of("trace") == set()
async def test_feedback_up_down_and_retract_map_to_scores():
    # up → value 1.0；down + comment → value 0.0, comment 已脱敏；rating None → delete_score(feedback_score_id(...))
async def test_one_bad_trace_does_not_block_others(): ...
```

- [x] **Step 3:** Run `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/ai/test_langfuse_mapping.py apps/tap-ai-backend/tests/unit/ai/test_langfuse_sync.py -q`；Expected: FAIL。
- [x] **Step 4:** 实现。`run_once`：`cutoff = clock() - settle`；先重试 `failures("trace")`，再 `scan_traces` → 逐条 `load_trace` → 映射 → `push_trace`（成功 `clear_failure`，异常 `record_failure`）→ `advance("trace", max)`；反馈同理（`rating is None` 走 `delete_score`）。`load_trace` 返回 None（已不存在）时清除失败记录。`request_json` 能 `json.loads` 时以对象脱敏（`redact_value`），否则按字符串。
- [x] **Step 5:** 同 Step 3 PASS。
- [x] **Step 6: Commit**

```bash
git add apps/tap-ai-backend
git commit -m "feat: map mysql traces and feedback to langfuse"
```

### Task 7: Langfuse Sink、worker 进程与配置

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/ai/adapters/langfuse_sink.py`
- Create: `apps/tap-ai-backend/src/tap/entrypoints/tapper_langfuse_sync_worker.py`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py`（`TapperSettings` 新字段与解析）
- Modify: `scripts/run-tapper-dev.sh`（启动、清理、存活检查，仿 `tapper_dev_suggestion_pid`）
- Modify: `.env.example`
- Test: `apps/tap-ai-backend/tests/unit/entrypoints/test_langfuse_sync_worker.py`、`apps/tap-ai-backend/tests/unit/entrypoints/test_tapper_runtime.py`（新增用例）、`apps/tap-ai-backend/tests/unit/ai/test_langfuse_sink.py`

**Interfaces:**
- Consumes: Task 6 `LangfuseSink`、`LangfuseSyncService`；Task 5 `MysqlLangfuseSyncStore`；Task 2 结论。
- Produces:
  - `TapperSettings.langfuse_host: str | None`（`_loopback_url(..., schemes={"http"}, allow_userinfo=False, root_only=True)`）、`langfuse_public_key: str | None`、`langfuse_secret_key: str | None`（host 已设而任一 key 缺失 → `ValueError`）、`langfuse_environment: str = "local"`（`TAP_LANGFUSE_ENVIRONMENT`）、`langfuse_sync_settle_seconds: int = 60`（≥ 10）、`trace_redaction_rules: Path | None`（`TAP_TRACE_REDACTION_RULES`，文件须存在）
  - `class LangfuseSdkSink(LangfuseSink): __init__(self, *, host: str, public_key: str, secret_key: str)`
  - worker `main(environment: Mapping[str, str] | None = None) -> None`、`cli(...) -> int`，service name `tap-ai-worker-langfuse-sync`

- [x] **Step 1: 写失败测试**

```python
def test_settings_reject_non_loopback_langfuse_host(): ...      # http://10.0.0.5:23000 → ValueError
def test_settings_require_keys_when_host_set(): ...
async def test_worker_idles_without_langfuse_host(caplog):      # 不构造 sink、不调用 store.advance，日志一条 "langfuse sync disabled"
async def test_worker_loop_survives_run_once_exception(): ...   # 抛异常后下一轮仍执行
def test_sdk_sink_maps_trace_to_sdk_calls(fake_langfuse_client): ...  # 断言 id、时间戳、usage/cost、score id 透传
```

- [x] **Step 2:** Run `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/entrypoints/test_langfuse_sync_worker.py apps/tap-ai-backend/tests/unit/entrypoints/test_tapper_runtime.py apps/tap-ai-backend/tests/unit/ai/test_langfuse_sink.py -q`；Expected: FAIL。
- [x] **Step 3:** 实现。worker 循环：`run_once` → `sleep(5)`，异常记 `logger.exception` 后继续；SIGTERM 时退出。sink 按 Task 2 结论使用 SDK（或 OTLP + SDK score），每次 `push_trace` 后 `flush()` 以便把失败暴露为异常。`run-tapper-dev.sh` 以 `python -m tap.entrypoints.tapper_langfuse_sync_worker` 启动。
- [x] **Step 4:** 同 Step 2 PASS；`bash -n scripts/run-tapper-dev.sh` 无输出。
- [x] **Step 5: Commit**

```bash
git add apps/tap-ai-backend scripts/run-tapper-dev.sh .env.example
git commit -m "feat: run langfuse sync worker with loopback settings"
```

### Task 8: 原型加入反馈按钮

**Files:**
- Create: `apps/web/src/widgets/tap/prototype/answer/AnswerFeedback.tsx`
- Modify: `apps/web/src/widgets/tap/prototype/answer/KnowledgeAnswer.tsx`（在 `<AnswerTrace>` 前渲染）
- Test: `apps/web/src/widgets/tap/prototype/answer/AnswerFeedback.test.tsx`

**Interfaces:**
- Produces: `AnswerFeedback({ locale: "en" | "zh", value: FeedbackValue | null, onChange(next: FeedbackValue | null): void })`，`type FeedbackValue = { rating: "up" | "down"; comment: string | null }`。原型内用本地 state；同一组件结构供 Task 9 参照（不共享代码，见原型即设计稿约定）。

- [x] **Step 1: 写失败测试**：点 👍 → `aria-pressed="true"`，再点恢复；点 👎 → 出现 `textbox`（`maxLength=500`，label “补充说明（可选）”/“Add a comment (optional)”）与“提交”“跳过”；切换 👍 时评论框消失。按钮 `aria-label`：“有帮助”/“Helpful”、“没帮助”/“Not helpful”。
- [x] **Step 2:** Run `corepack pnpm --filter @tap/web exec vitest run src/widgets/tap/prototype/answer/AnswerFeedback.test.tsx`；Expected: FAIL。
- [x] **Step 3:** 实现，使用 antd `Button` 与 `@ant-design/icons` 的 `LikeOutlined`/`DislikeOutlined`（及 Filled 变体表示选中），无演示开关或说明文字。
- [x] **Step 4:** 同 Step 2 PASS；`corepack pnpm --filter @tap/web run test` PASS；启动 `apps/web`，在 `/prototype` 截取 Tapper 回答改动前后截图，并点击 Tapper / Test Management / Test Insights / Low Code Automation 导航与浮动助手确认无回归。
- [x] **Step 5: Commit**

```bash
git add apps/web/src/widgets/tap/prototype/answer
git commit -m "feat: add answer feedback to the prototype"
```

### Task 9: TAP AI 前端反馈

**Files:**
- Modify: `apps/tap-ai-frontend/src/features/conversations/api/client.ts`（`putFeedback`、`deleteFeedback`）
- Modify: `apps/tap-ai-frontend/src/features/conversations/api/queries.ts`（`useTurnFeedback` mutation，成功后更新会话详情缓存中该 turn 的 `myFeedback`）
- Create: `apps/tap-ai-frontend/src/features/conversations/feedback/AnswerFeedback.tsx`
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/model.ts`（turn 增加 `myFeedback?: FeedbackValue | null`）
- Modify: `apps/tap-ai-frontend/src/widgets/tap/TapperWorkspace.tsx`（映射 `turn.myFeedback`；仅 `status === "completed"` 且有 `conversationId` 时在 `DurableTracePanel` 前渲染）
- Test: `apps/tap-ai-frontend/src/features/conversations/feedback/AnswerFeedback.test.tsx`、`apps/tap-ai-frontend/src/features/conversations/api/client.test.ts`（新增用例）

**Interfaces:**
- Consumes: Task 4 生成类型 `components["schemas"]["AnswerFeedbackRequest" | "AnswerFeedbackView"]`；交互同 Task 8。
- Produces: `ConversationClient.putFeedback(conversationId, turnId, body: AnswerFeedbackRequest, signal?) -> Promise<AnswerFeedbackView>`；`deleteFeedback(conversationId, turnId, signal?) -> Promise<void>`。

- [x] **Step 1: 写失败测试**：client 发出正确方法与路径；组件用初始 `myFeedback` 恢复选中态；👍 调 `putFeedback({rating:"up"})`；👎 先 `putFeedback({rating:"down"})`，提交评论再 `putFeedback({rating:"down", comment})`；再点已选中按钮调 `deleteFeedback`；请求失败时恢复原状态并显示错误提示；`abstained`/`failed`/`running` 的 turn 不渲染。
- [x] **Step 2:** Run `corepack pnpm --filter @tap/ai-frontend exec vitest run src/features/conversations`；Expected: FAIL。
- [x] **Step 3:** 实现；视觉与 Task 8 原型一致。
- [x] **Step 4:** 同 Step 2 PASS；`corepack pnpm --filter @tap/ai-frontend exec tsc --noEmit` 无错误。
- [x] **Step 5: Commit**

```bash
git add apps/tap-ai-frontend/src
git commit -m "feat: let users rate tapper answers"
```

### Task 10: E2E 旅程

**Files:**
- Modify: `apps/tap-ai-frontend/tests/e2e/knowledge-conversation.spec.ts`

- [x] **Step 1:** 在已有“得到完成回答”的步骤后追加：点“没帮助”，填写评论并提交，`page.reload()` 后该按钮 `aria-pressed="true"`；通过 API `GET` 会话详情断言该 turn `myFeedback.rating === "down"` 且 comment 一致。
- [x] **Step 2:** Run `make demo-e2e`；Expected: 全部通过（E2E 不启动 Langfuse）。
- [x] **Step 3: Commit**

```bash
git add apps/tap-ai-frontend/tests/e2e/knowledge-conversation.spec.ts
git commit -m "test: cover answer feedback in the conversation journey"
```

### Task 11: 文档与路线图

**Files:**
- Modify: `docs/superpowers/specs/2026-09-30-observability-design.md`（§1 删除“不部署 Langfuse”，改为链接本设计；§4 删除 Phoenix compose 描述）
- Modify: `docs/guides/2026-09-30-observability.md`（§2 Phoenix 改为 Langfuse：`make langfuse-up`、登录、看板、按会话/用户筛选、重推方法“把 `langfuse_sync_cursor` 置为 `1970-01-01`”、静默窗口前提、失败表排查）
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 5 新增未勾选工程项“Langfuse 运营看板与用户反馈”；子项目列表新增本 spec/plan，状态“进行中”）
- Modify: `docs/superpowers/plans/2026-10-01-product-roadmap.md`（V2 新增“Tapper LangGraph 化”路线：图重构、角色定义与意图识别、护栏、工具调用与长任务编排、可信知识，标注“待设计”）
- Modify: `docs/architecture.md`（进程列表加入同步 worker；数据流加入 MySQL → Langfuse；中间件复用说明）
- Modify: `README.md`（仅当其中提到 Phoenix 时同步修改）

- [x] **Step 1:** 完成上述修改；`grep -rn -i phoenix docs README.md .env.example compose.yaml apps/tap-ai-backend/src` 只剩历史 plan 文件中的记录。
- [x] **Step 2:** `git diff --check` 无输出；预览修改过的 Markdown 与 Mermaid。
- [x] **Step 3: Commit**

```bash
git add docs README.md
git commit -m "docs: document langfuse ops dashboard and v2 langgraph route"
```

### Task 12: 全量验证与手工验收

由主会话或人工在有 `.env` 的主工作区执行，不派给子代理。

- [x] **Step 1:** `make check` 与 `make test` 全部通过。
- [x] **Step 2:** 在有 `.env` 的主工作区按 spec §7 手工验收 1–5 项：`make langfuse-up`、`make demo-dev`、若干轮对话与赞/踩；Langfuse 中按会话/用户/项目筛选、generation 的 token 与成本、输入输出脱敏（虚构手机号与邮箱）、score 带评论、内置看板可见成本/延迟/用量/满意度；停止 Langfuse 继续对话再启动，数据补齐；产品内调用链面板正常。
- [x] **Step 3:** 结果记入 PR 描述；V1 总纲能力 5 新工程项在全部满足后勾选并提交：

```bash
git add docs/superpowers/plans/2026-09-29-v1-roadmap.md
git commit -m "docs: mark langfuse ops observability complete"
```
