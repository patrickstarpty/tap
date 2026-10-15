# 可观测性指南

创建日期：2026-09-30。TAP AI 的调用链与模型调用记录永久写入 MySQL，不依赖任何外部追踪后端；`OTEL_EXPORTER_OTLP_ENDPOINT` 只是可选的额外导出目的地。背景见[可观测性设计](../superpowers/specs/2026-09-30-observability-design.md)。

## 1. 三张表

- `trace_span`：OpenTelemetry span 落库副本，主键 `(trace_id, span_id)`，含 `parent_span_id`、`turn_id`、`job_id`、`service_name`、`name`、`status`、`started_at`、`duration_ms` 与 `attributes`（JSON，序列化超过 65536 字节时先删最大属性直至不超限，并置 `tap.truncated=true`）。
- `model_call`：每次模型调用一行，主键 `call_id`（UUID），关联 `trace_id`/`span_id`/`turn_id`/`job_id`，记录请求的 `model_name`、LiteLLM 实际路由到的 `upstream_model`、`provider`、`input_tokens`/`output_tokens`、`cost_usd`（取响应头 `x-litellm-response-cost`，缺失或非法为 `NULL`）、`latency_ms`、`attempts`、`status`、`error_code`。
- `model_call_content`：与 `model_call` 一对一，`call_id` 外键，存放原文：`request_json`（实际发送的 payload；图片 data URL 替换为 `sha256:<digest>`，不存图片字节）、`response_text`（embedding 调用存 `{"dimension": <int>, "count": <int>}`，不存向量）、`reasoning_text`（取 `choices[0].message.reasoning_content`，无则 `NULL`）。

`chat_turn.traceparent` 记录 API 进程为该 Turn 生成的 W3C traceparent，随 Outbox/Redis Stream 一并投递给 worker，使 worker 侧的 `turn.execute` span 与 API 侧的 `turn.request` span 共享同一个 `trace_id`。

追踪数据不设保留期、没有清理任务；删除文档或软删除会话不会改写或级联删除已写入的追踪行（见 §4 增长风险）。可观测性写入失败只记 `logging.warning`，不影响业务请求。

## 2. 启用 Langfuse 运营看板（可选）

Langfuse 是可选的运营级看板：成本趋势、延迟分布、按模型/项目/用户统计、满意度，并支持按会话、用户、项目、模型筛选后下钻到单条 trace。MySQL 仍是链路数据的权威来源，产品内的调用链面板（§1、§3）继续直接读 MySQL，不依赖 Langfuse；Langfuse 是可从 MySQL 完整重建的运营展示副本。设计细节见[运营可观测性设计](../superpowers/specs/2026-10-14-langfuse-ops-observability-design.md)。

```sh
# 1. 启动 Langfuse（先确保 insights ClickHouse 已启动，幂等创建 ClickHouse 库/账号与 MinIO bucket）：
make langfuse-up

# 2. 浏览器打开 http://127.0.0.1:${LANGFUSE_PORT:-23000}，用 .env 中
#    LANGFUSE_INIT_USER_EMAIL / LANGFUSE_INIT_USER_PASSWORD 登录本地管理员账号。

# 3. 在 .env 中取消注释并设置，然后重启 TAP AI 的 API 与 worker 进程：
LANGFUSE_HOST=http://127.0.0.1:23000
LANGFUSE_PUBLIC_KEY=pk-lf-local-example
LANGFUSE_SECRET_KEY=sk-lf-local-example

# 4. 停止 Langfuse，保留数据卷：
make langfuse-down
```

未配置 `LANGFUSE_HOST` 时，`tapper_langfuse_sync_worker` 只打一条启动日志后空转；一旦配置并重启，历史链路数据与反馈会自动补推。看板中每个 Turn 对应一条 trace，可按会话、用户、项目、模型筛选并下钻；generation 带 token 与成本；回答的 👍/👎 反馈同步为名为 `user_feedback` 的 score。

`LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` 与 `TAP_TRACE_REDACTION_RULES` 均由共享的 `TapperSettings` 在启动时校验：`LANGFUSE_HOST` 配置但缺少任一 key，或 `TAP_TRACE_REDACTION_RULES` 指向不存在的文件，都会在配置解析阶段报错，导致 API、Relay、worker 等全部 TAP AI 进程无法启动，而不是仅 Langfuse 相关进程失败；修改这三个变量后请先确认路径存在、key 成对配置，再重启进程。

发往 Langfuse 的文本一律先脱敏（凭据、邮箱、手机号、身份证、银行卡号，以及兜底的数字串规则），MySQL 中的原文不受影响；脱敏基于正则，存在漏网可能，进入共享环境前需安全复核（见设计 §4、§10）。

同步 worker 按 `trace_span.recorded_at` 与 `answer_feedback.updated_at` 增量扫描，只处理早于“当前时间 − `TAP_LANGFUSE_SYNC_SETTLE_SECONDS`”（默认 60 秒）的行，避免扫到仍在写入的半条 trace；该静默窗口必须大于最长的一次 span 导出或反馈事务耗时。推送失败的条目记入 `langfuse_sync_failure`，下一轮无限重试、不阻塞其他条目或游标前进。

**重推（ClickHouse 数据丢失或脱敏规则升级后使用）**：把两个游标都重置为 `1970-01-01`，worker 会在下一轮起把全部历史链路与反馈重新推送（Langfuse 按原 id 覆盖，幂等）：

```sql
INSERT INTO langfuse_sync_cursor (name, position) VALUES
  ('trace', '1970-01-01 00:00:00'),
  ('feedback', '1970-01-01 00:00:00')
ON DUPLICATE KEY UPDATE position = VALUES(position);
```

**排查同步失败**：

```sql
SELECT kind, subject_id, failure_count, last_error, last_failed_at
FROM langfuse_sync_failure
ORDER BY last_failed_at DESC;
```

`kind` 为 `trace` 时 `subject_id` 是 `trace_id`，为 `feedback` 时是 `feedback_id`；成功重试后该行自动删除。

仍可选设置通用 `OTEL_EXPORTER_OTLP_ENDPOINT`，把 span 另外导出到任意 OTLP 后端（与 Langfuse 无关、互不依赖）；该变量必须是 loopback URL。

## 3. 按 `trace_id` 排障

先从某个 Turn 找到 `trace_id`（例如经 `GET /api/v1/projects/{project_id}/conversations/{conversation_id}/turns/{turn_id}/trace` 接口，或直接查 `chat_turn.traceparent` 中间一段）。

查某条 trace 的 span 树，按时间排序观察层级与耗时：

```sql
SELECT span_id, parent_span_id, service_name, name, status, started_at, duration_ms
FROM trace_span
WHERE trace_id = '<trace_id>'
ORDER BY started_at;
```

查某个 Turn 下发生的所有模型调用及其原文（含请求、返回、推理）：

```sql
SELECT mc.call_id, mc.model_name, mc.upstream_model, mc.provider,
       mc.input_tokens, mc.output_tokens, mc.cost_usd, mc.status,
       cc.request_json, cc.response_text, cc.reasoning_text
FROM model_call AS mc
JOIN model_call_content AS cc ON cc.call_id = mc.call_id AND cc.project_id = mc.project_id
WHERE mc.turn_id = '<turn_id>'
ORDER BY mc.created_at;
```

也可以直接用 API：`GET /api/v1/projects/{project_id}/model-calls/{call_id}` 返回单次调用的请求/返回/推理原文，供前端"调用链"面板的抽屉按需加载。

## 4. DashScope 成本缺失

LiteLLM 对部分 DashScope 模型算不出成本，此时 `model_call.cost_usd` 为 `NULL`，前端汇总行显示"成本未知"。需要展示成本时，在 `deploy/local/litellm/config.yaml` 对应模型的 `model_info` 下补充：

```yaml
model_info:
  mode: chat
  supports_response_schema: true
  input_cost_per_token: 0.000001
  output_cost_per_token: 0.000002
```

重启 LiteLLM 后生效，无需改动后端代码。

## 5. 原文保留与增长风险

`model_call_content` 永久保留每次模型调用的实际请求与返回原文，本期不做归档或清理。随调用量增长，该表会持续增大；后续如需按保留期归档或裁剪，需要另行设计（不在本期范围内）。
