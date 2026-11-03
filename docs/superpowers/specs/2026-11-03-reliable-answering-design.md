# 可靠问答设计

日期：2026-11-03。V1 子项目 3（能力 1），见 [V1 总纲](../plans/2026-09-29-v1-roadmap.md)。推荐问题部分已由 [推荐问题设计](2026-09-30-tapper-prompt-suggestions-design.md) 交付（#38），本设计不再重复。

## 目标

1. 仓库内 golden set 与回归命令：默认用确定性假模型运行，可选真实模型；CI 运行假模型回归。
2. 回答逐 token 流式下发：LiteLLM 流式输出，worker 写入增量事件，SSE 由事件通知驱动，替换 100ms 轮询。
3. 后台任务可靠：租约回收计数且有上限，瞬时失败退避重试，worker 主循环单次异常不退出。
4. 持久化集成测试在缺 MySQL 时明确报告，可配置为失败。

## 非目标

- 30–50 条真实标注问答的基线分数（业务验证项，由使用者用本设计的回归命令产出）。
- Outbox → Relay → Redis Stream 简化。
- Turn 级重试策略变更：Turn 认领已递增 `processing_attempt`，`max_checkpoint_attempts=3` 封顶；网关内部已有调用重试。
- 生产部署、认证（V4）。

## 1. Golden set

### 格式

目录 `apps/tap-ai-backend/tests/quality/golden/`：

| 文件 | 内容 |
| --- | --- |
| `corpus/*.md` | 固定语料，脱敏、可公开，覆盖事实、流程、冲突与无答案场景 |
| `kb-golden.v1.jsonl` | 每行一条用例 |
| `baseline.fake.json` | 假模型基线分数（提交到仓库） |

用例字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 稳定 ID，如 `kb-fact-001` |
| `category` | `fact` / `procedure` / `abstain` / `relation`（`relation` 由子项目 5 使用） |
| `question` | 问题 |
| `sources` | 参与检索的语料文件名列表 |
| `expect.abstain` | 是否应拒答 |
| `expect.citedDocuments` | 必须被引用的语料文件名（至少命中一个） |
| `expect.mustContain` | 回答必须包含的关键词（大小写不敏感） |
| `expect.relations` | 仅 `relation`：期望引用的关系类型（子项目 5） |

### 运行器

新模块 `tap.quality.golden`：

- 把 `sources` 语料按现有切片规则写入进程内检索适配器（与契约测试同一套内存实现），通过 `KnowledgeAnswerService` 正常路径回答，保证评测的是生产编排而非旁路。
- 模型：默认 `DeterministicModelGateway`（扩展为按检索证据确定性组装回答与引用）；`TAP_GOLDEN_REAL_MODEL=1` 时使用 LiteLLM 配置的真实模型，并要求 `LITELLM_BASE_URL`、`LITELLM_MASTER_KEY` 配置齐全，否则报错退出。
- 指标（按类别与总计）：`citationRecall`（命中 `citedDocuments` 的比例）、`keywordRecall`、`abstainAccuracy`、`answered`。
- 输出 JSON 报告到 `apps/tap-ai-backend/.quality/golden-<timestamp>.json`（已 gitignore）与终端表格。
- 回归门：假模型运行与 `baseline.fake.json` 比较，任一指标下降超过 0.0 即失败（确定性，零容忍）；真实模型只报告，不设门，可用 `--write-baseline <path>` 记录业务基线。

### 命令与 CI

- `make quality-golden`：假模型回归；`make quality-golden-real`：真实模型。
- 回归作为 pytest 用例 `tests/quality/test_golden_regression.py` 纳入 `make test`。
- 新增 `.github/workflows/ci.yml`：`uv sync --frozen`、`pnpm install --frozen-lockfile`，运行后端单元/契约/质量测试（不含需 MySQL 的集成测试）与前端 Vitest；golden 回归在其中执行。

## 2. 流式输出

### 网关

- `ModelGateway` 端口新增 `stream_structured(request) -> AsyncIterator[ModelStreamEvent]`，事件为 `TextDelta(text)` 或终止的 `StreamCompleted(result: ModelResult)`。
- `LiteLLMModelGateway` 发送 `stream: true`、`stream_options.include_usage: true`，累积全文后做与 `generate_structured` 相同的 JSON/schema 校验、用量与成本记录（`model_call` 一行）。首个 token 前失败时退回非流式 `generate_structured`；首 token 后失败抛出 `ModelGatewayUnavailable`。
- `DeterministicModelGateway` 实现流式：把确定性输出按固定大小切块产出，保证 E2E 覆盖。

### 增量抽取

- 回答 schema 中 `answer` 为首个属性。新 `IncrementalJsonField("answer")` 解析器逐块读取原始 JSON 文本，只输出顶层 `answer` 字符串的已解码增量（处理转义与跨块 `\uXXXX`）。
- `KnowledgeModelGateway.answer(..., on_delta=None)`：传入回调时走流式；最终仍以完整 JSON 校验后的结果为准。

### worker 与事件

- 生成 worker 在回答阶段传入 `on_delta`，按 150ms 或 200 字符批量追加 `answer.delta` 事件（新仓储方法 `append_stream_event`，校验租约令牌），每次追加后发布通知。
- 新事件 `answer.reset`（`{"attempt": n}`）：Turn 被重新认领开始新 attempt 时先写入，客户端清空临时文本。
- 完成时不再一次性写整段 `answer.delta`；终态以 `answer_snapshot` 为准，客户端收到 `turn.completed` 后用持久化回答替换临时文本（引用与拒答以服务端校验结果为准）。
- 事件契约（`contracts/chat_stream.py`、`contracts/events`）新增 `answer.reset`，经 `make contracts` 生成。

### SSE

- 新端口 `ConversationEventNotifier`：`publish(chat_id)`、`wait(chat_id, timeout) -> bool`。Redis 适配器用 Pub/Sub 频道 `tap:chat:{project_id}:{chat_id}`；内存适配器用 `asyncio.Condition`，供测试与无 Redis 的假环境。
- 仓储在提交事件事务后调用 `publish`（失败只告警）。
- SSE 循环：读取 `sequence > resume` 的事件并下发 → 若会话无 `queued`/`running` Turn 且已追平则结束 → 否则 `wait(timeout=15s)`；超时发送 SSE 注释 `: keepalive` 并重新读库（通知丢失的兜底）。单连接最长 10 分钟，客户端凭 `Last-Event-ID` 续接。
- 只读增量：仓储新增 `events_after(chat_id, sequence)`，不再每次加载整个会话。

## 3. 后台任务可靠性

### 共享策略

`tap/platform/retry.py`：`RetryPolicy(max_attempts=5, base=2s, cap=300s)`，`next_delay(attempt)` 为 `min(cap, base·2^(attempt-1))` 的全抖动值；`is_exhausted(attempt)`。

### 摄取任务

- 新列 `knowledge_ingestion_job.delivery_count`（迁移，默认 0）：每次认领（含租约过期回收）+1；用户触发的重试（`attempt+1`）把它清零。`attempt` 语义（解析清单与重试 API 的代号）不变。
- 认领时若 `delivery_count` 已达上限，直接置 `FAILED`、`error_code="lease-exhausted"`，不再交给 worker。
- `fail_job` 按错误码分类：瞬时错误（模型、对象存储、索引、解析器不可用或超时）且未达上限时置回 `PENDING`，`next_attempt_at = now + next_delay`，阶段结果保留失败记录；否则维持现有终态行为。瞬时错误码集合定义在领域层并有单元测试。

### 图谱抽取任务

- 新列 `graph_extraction_job.next_attempt_at`（迁移，可空）；认领条件加 `next_attempt_at <= now`。
- 认领时 `attempt_count` 已达上限 → `FAILED`、`failure_code="attempts-exhausted"`，候选快照置 `FAILED`。
- 瞬时失败（模型不可用、超时）未达上限时回到 `PENDING` 并退避；其他失败维持终态。

### worker 主循环

- `tap/platform/worker_loop.py`：`run_worker_loop(name, iteration, *, idle_wait, policy)`。单次迭代抛出非 `CancelledError` 异常时记录带堆栈的错误日志与 `worker.iteration.failed` span，按连续失败次数退避（上限 30s）后继续；成功一次即清零。
- 摄取、解析、图谱、生成、推荐问题、测试方案 worker 的 `main` 改用该循环。

## 4. 集成测试报告

- `tests/conftest.py` 记录因缺 `TAP_RUN_MYSQL_INTEGRATION` 跳过的用例；`pytest_terminal_summary` 输出醒目一行：`MySQL persistence tests: N skipped (set TAP_RUN_MYSQL_INTEGRATION=1 with owned MySQL)`。
- `TAP_REQUIRE_MYSQL_INTEGRATION=1` 时同一条件改为失败。
- `make test` 输出上述摘要；文档说明本地如何启用。

## 5. 前端

- 会话流消费：处理 `answer.delta` 追加、`answer.reset` 清空、`turn.completed` 后拉取持久化 Turn 替换；流式期间显示光标且不显示引用；连接结束且仍有运行中 Turn 时按 `Last-Event-ID` 重连。
- `/prototype` 的 Tapper 回答已是逐字呈现，无需改动设计基准。

## 6. 测试与验收

- 单元：`IncrementalJsonField`（转义、Unicode 跨块、非首属性）、`RetryPolicy`、`run_worker_loop`、瞬时错误分类。
- 契约：网关流式（假 HTTP 传输返回 SSE 块，含 usage 与首 token 前失败回退）、SSE 端点（内存通知器，增量下发、keepalive、追平后关闭）、`answer.reset` 契约。
- MySQL 集成：摄取 `delivery_count` 回收与耗尽、瞬时失败退避、图谱任务上限与退避、`append_stream_event` 租约校验。
- 前端：流解析与渲染状态机。
- E2E：隔离旅程断言回答在 `turn.completed` 前已出现部分文本。
- golden：`make quality-golden` 通过并纳入 `make test`。
- `make check`、`make test` 通过；更新 `docs/architecture.md`（流式与通知、重试策略）。
