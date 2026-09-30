# 可观测性设计

日期：2026-09-30。V1 子项目 2（能力 5），见 [V1 总纲](../plans/2026-09-29-v1-roadmap.md)。依赖子项目 1（[基础设施收敛](2026-09-29-infra-convergence-design.md)）的模型目录与实际上游模型记录。

## 目标

面向开发者与运维，用于排障与成本观察：

1. 每个会话 Turn 与后台任务有一条 trace，包含检索、图谱扩展、模型调用、工具调用等步骤的 span。
2. 每次模型调用持久化模型名、实际上游模型、供应商、token、成本、耗时与原文。
3. 前端在每条回答下展示调用链、token 与成本面板，可查看每次模型调用的请求、返回与推理摘要。

## 非目标

- 终端用户视角的"答案由来"说明与角色权限区分。
- 后台任务的前端 trace 界面（数据照常写入，可查 MySQL 或 Phoenix）。
- 引入或扩展 LangGraph；现有 `classify → admit → execute` 图的去留由子项目 5 决定。埋点在应用层函数上，不依赖 LangGraph。
- 流式输出、重试退避（子项目 3）。
- 按文档清除追踪原文（未来合规需求再做）。

## 1. 方案

OpenTelemetry 埋点，属性遵循 OTel GenAI 语义约定；MySQL 为权威存储，前端面板只读 MySQL；可选 OTLP 导出到 Phoenix（或任何 OTLP 后端）。不部署 Langfuse。

## 2. 追踪边界

- **会话 Turn**：`POST .../turns` 时 API 开根 span `turn.request`，把 W3C `traceparent` 写入 `chat_turn.traceparent`（新列）。worker 认领 Turn 时从该列恢复上下文，开 `turn.execute`，属性 `tap.turn_id`、`tap.attempt`。一个 Turn 一条 trace。
- **重新执行**：Turn 被重新认领时沿用同一 trace，新的 `turn.execute` 带新的 `tap.attempt`；面板按 attempt 分组。
- **后台任务**：摄取、图谱抽取、测试方案生成等每次任务执行开一个根 span，属性 `tap.job_id`、`tap.job_kind`。

## 3. 数据模型（新迁移 `0025_observability`）

### `trace_span`

| 列 | 说明 |
| --- | --- |
| `trace_id` CHAR(32)、`span_id` CHAR(16) | 主键 |
| `parent_span_id` CHAR(16) NULL | |
| `project_id`、`turn_id` NULL、`job_id` NULL | 归属；索引 `(project_id, turn_id)` |
| `service_name` | 如 `tap-ai-api`、`tap-ai-worker-generation` |
| `name`、`status`（`ok`/`error`）、`status_message` NULL | |
| `started_at` DATETIME(6)、`duration_ms` | |
| `attributes` JSON | 元数据，不含大段原文；上限 64 KiB，超出时截断并标记 `tap.truncated=true` |

### `model_call`

| 列 | 说明 |
| --- | --- |
| `call_id` CHAR(36) | 主键，每次网关调用新生成 UUID |
| `trace_id`、`span_id` NULL | 关联 span；索引 `trace_id` |
| `project_id`、`turn_id` NULL、`job_id` NULL | |
| `operation` | `chat` / `structured` / `embed` |
| `model_name` | 请求的模型名 |
| `upstream_model`、`provider` | 来自子项目 1 的实际上游模型记录；失败时可空 |
| `input_tokens`、`output_tokens` NULL | 失败的尝试无用量 |
| `cost_usd` DECIMAL(18,8) NULL | 解析 `x-litellm-response-cost`；缺失为 NULL |
| `latency_ms`、`attempts` | `attempts` 含网关内部重试 |
| `status`（`ok`/`error`）、`error_code` NULL | |
| `gateway_call_id`、`provider_request_id` NULL | |
| `created_at` DATETIME(6) | |

### `model_call_content`（与 `model_call` 一对一）

| 列 | 说明 |
| --- | --- |
| `call_id` | 主键、外键 |
| `request_json` LONGTEXT | 实际发送的 messages、schema 与参数（已经过现有脱敏；图片以摘要代替字节） |
| `response_text` LONGTEXT NULL | 模型原始返回；embedding 调用只记维度与数量，不存向量 |
| `reasoning_text` LONGTEXT NULL | 响应中的 `reasoning_content`（仅推理模型返回时） |

### 保留

默认记录原文，所有追踪数据永久保留，不做自动清理，不提供保留期配置。追踪数据按审计记录处理：删除文档或软删除会话不改写已有追踪。

## 4. 埋点与写入

### 基础设施

- 新依赖：`opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`（锁定版本）。
- 新模块 `apps/tap-ai-backend/src/tap/platform/telemetry/`：进程初始化 `configure_tracing(service_name)`、上下文工具 `span(name, **attributes)`、`inject_traceparent()` / `extract_traceparent(value)`、`MysqlSpanExporter`。
- 每个进程（API、各 worker）初始化一个 TracerProvider；`BatchSpanProcessor` → `MysqlSpanExporter`（每 5 秒或 512 个 span 刷新）；Turn 或任务结束时 `force_flush`。
- 设置 `OTEL_EXPORTER_OTLP_ENDPOINT` 时追加 OTLP/HTTP 导出。compose 新增可选服务 `phoenix`（profile `observability`，仅 loopback 端口）。

### 埋点位置

| span | 位置 | 关键属性 |
| --- | --- | --- |
| `turn.request` | API `POST .../turns` | conversation_id、turn_id |
| `turn.execute` | 生成 worker | turn_id、attempt、outcome |
| `chat.plan` | AnswerPlanner | 计划类型 |
| `retrieval.search` | `_retrieve`（含规划多路查询、流程图补充检索） | 命中切片 ID、分数、数量 |
| `graph.enrich` | GraphAnswerEnricher | 快照 ID、节点/边数量 |
| `tool.insights.query` | Insights 分支 | 查询参数、行数 |
| `citations.resolve` | 引用解析 | 引用数 |
| `chat {model}` / `embeddings {model}` | `LiteLLMModelGateway` 每次调用 | `gen_ai.operation.name`、`gen_ai.request.model`、`gen_ai.response.model`、`gen_ai.provider.name`、`gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens`、`tap.model_call_id` |
| 任务根 span | 摄取、图谱、测试方案、流程图识别 worker | job_id、job_kind |

### 模型调用记录

- 新端口 `ai/ports/model_calls.py`：`ModelCallRecorder.record(call: ModelCallRecord) -> None`。MySQL 适配器用独立短事务写 `model_call` 与 `model_call_content`，不参与业务事务。
- `LiteLLMModelGateway` 在每次调用结束（成功或失败）时同步记录一次；`call_id` 同时写入模型调用 span 的 `tap.model_call_id`。
- `ModelResult` 新增 `call_id` 与 `cost_usd`，供调用方关联。

### 失败处理

- 可观测性任何失败不影响业务：exporter 或 recorder 异常只记告警日志，模型调用、Turn、任务照常继续。
- recorder 最近一次写入失败时，`/health` 的 models 组件以提示（notice）形式报告，不判为不健康。

### SSE 事件契约修正

worker 发出的 `context.assembled`、`stage.completed` 事件字段与严格契约（`contracts/chat_stream.py`）不一致；本子项目统一以契约为准修正发出方，并补回归测试。

## 5. API

- `ConversationTurnSummary` 新增 `traceId`（可空）。
- `GET /api/v1/projects/{project_id}/conversations/{conversation_id}/turns/{turn_id}/trace` → `TurnTrace`：
  - `summary`：总耗时、输入/输出 token 合计、成本合计（任一调用缺成本时标记 `costIncomplete`）、请求模型与实际上游模型列表、attempt 数；
  - `spans`：扁平列表（`spanId`、`parentSpanId`、`name`、`status`、`startedAt`、`durationMs`、`attributes`、`attempt`）；
  - `modelCalls`：`model_call` 元数据（不含原文），含 `spanId`。
- `GET /api/v1/projects/{project_id}/model-calls/{call_id}` → `ModelCallDetail`：元数据 + `request`、`response`、`reasoning` 原文。
- 两个接口受现有项目授权约束；契约经 `make contracts` 生成。

## 6. 前端

`apps/tap-ai-frontend`，每条回答下：

- 现有"执行记录 / Activity"折叠区升级为"调用链"。折叠时一行汇总：`总耗时 · 输入/输出 tokens · 成本 · 请求模型 → 实际上游模型`。
- 展开为瀑布图：按父子缩进、条长表示耗时、失败标红；多 attempt 用标签切换；检索 span 列出命中切片并可跳转到现有原件/切片查看。
- 模型调用 span 打开侧边抽屉，"请求 / 返回 / 推理"三个标签，按需加载原文，支持复制。
- Turn 进入终态后才请求 trace，执行中不轮询。

设计基准：按 `AGENTS.md`，在 `apps/web` 的 `/prototype` Tapper 回答下同步加入该面板（fixture 数据，无演示开关或实现说明），对比改动前后截图，核对模块导航与跨模块旅程。

## 7. 测试与验收

- 后端：
  - `MysqlSpanExporter`、`ModelCallRecorder` 的 MySQL 集成测试（写入、截断、失败隔离）；
  - traceparent 从 API 经 `chat_turn` 传到 worker 的端到端测试；
  - 模型调用行与 span 的关联、`cost_usd` 有值与缺失、原文与脱敏后发送内容一致、失败调用的记录；
  - trace 与 model-call 接口的契约与授权测试；
  - SSE 事件契约回归测试。
- 前端：汇总行、瀑布图树形组装、attempt 切换、抽屉按需加载。
- E2E：隔离旅程中打开一条回答的调用链，核对存在 `turn.execute`、`retrieval.search`、模型调用 span 与 token 汇总（假模型返回确定 usage）。
- `make check`、`make test`、`make demo-e2e` 通过。
- 文档：新增 `docs/guides/2026-09-30-observability.md`（数据表、启用 Phoenix、按 trace_id 排障）；更新 `docs/architecture.md`；总纲能力 5 工程完成项在满足后勾选。

## 8. 风险

- LiteLLM 对 DashScope 模型可能算不出成本：`cost_usd` 为 NULL，面板显示"成本未知"；需要时在 `config.yaml` 的 `model_info` 配置 `input_cost_per_token` / `output_cost_per_token`。
- 原文永久保留使 `model_call_content` 持续增长；本期不处理，后续需要时另行设计归档。
- 与子项目 1 的依赖：本分支基于 `claude/v1-infra-convergence`，该 PR 合并后需变基到 `main`。
