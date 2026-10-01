# 可观测性指南

创建日期：2026-09-30。TAP AI 的调用链与模型调用记录永久写入 MySQL，不依赖任何外部追踪后端；`OTEL_EXPORTER_OTLP_ENDPOINT` 只是可选的额外导出目的地。背景见[可观测性设计](../superpowers/specs/2026-09-30-observability-design.md)。

## 1. 三张表

- `trace_span`：OpenTelemetry span 落库副本，主键 `(trace_id, span_id)`，含 `parent_span_id`、`turn_id`、`job_id`、`service_name`、`name`、`status`、`started_at`、`duration_ms` 与 `attributes`（JSON，序列化超过 65536 字节时先删最大属性直至不超限，并置 `tap.truncated=true`）。
- `model_call`：每次模型调用一行，主键 `call_id`（UUID），关联 `trace_id`/`span_id`/`turn_id`/`job_id`，记录请求的 `model_name`、LiteLLM 实际路由到的 `upstream_model`、`provider`、`input_tokens`/`output_tokens`、`cost_usd`（取响应头 `x-litellm-response-cost`，缺失或非法为 `NULL`）、`latency_ms`、`attempts`、`status`、`error_code`。
- `model_call_content`：与 `model_call` 一对一，`call_id` 外键，存放原文：`request_json`（实际发送的 payload；图片 data URL 替换为 `sha256:<digest>`，不存图片字节）、`response_text`（embedding 调用存 `{"dimension": <int>, "count": <int>}`，不存向量）、`reasoning_text`（取 `choices[0].message.reasoning_content`，无则 `NULL`）。

`chat_turn.traceparent` 记录 API 进程为该 Turn 生成的 W3C traceparent，随 Outbox/Redis Stream 一并投递给 worker，使 worker 侧的 `turn.execute` span 与 API 侧的 `turn.request` span 共享同一个 `trace_id`。

追踪数据不设保留期、没有清理任务；删除文档或软删除会话不会改写或级联删除已写入的追踪行（见 §4 增长风险）。可观测性写入失败只记 `logging.warning`，不影响业务请求。

## 2. 启用 Phoenix（可选）

Phoenix 是可选的 OTLP 追踪查看器，通过 compose profile `observability`启动，只绑定 `127.0.0.1`：

```sh
# 1. 在 .env 中取消注释并设置：
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:26006

# 2. 启动 Phoenix（不影响默认 compose 服务）：
docker compose --profile observability up -d phoenix

# 3. 重启 TAP AI 的 API 与 worker 进程使新配置生效，然后在浏览器打开：
# http://127.0.0.1:26006
```

不设置 `OTEL_EXPORTER_OTLP_ENDPOINT` 时，span 仍然正常写入 MySQL，只是不额外导出到 OTLP。`OTEL_EXPORTER_OTLP_ENDPOINT` 必须是 loopback URL；`PHOENIX_IMAGE`（默认 `arizephoenix/phoenix:version-20.16.0`）与 `PHOENIX_PORT`（默认 `26006`）可在 `.env` 覆盖。

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
