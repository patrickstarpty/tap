# TAP 现状架构

本页只记录代码中可验证的现状，随代码更新。目标能力与验收见 [V1 总纲](superpowers/plans/2026-09-29-v1-roadmap.md)；历史设计见[归档](archive/index.md)。

## 1. 应用边界

- `apps/tap-ai-backend`：TAP AI 后端（Python 3.13 / FastAPI），Tapper 问答、知识文档、图谱、Agent/Skill 资产与测试方案生成。
- `apps/tap-ai-frontend`：TAP AI 前端（React / TypeScript / Vite）；只呈现 API 驱动的产品界面，交互以 `apps/web` `/prototype` 为设计来源。
- `apps/backend`：TAP 非 AI 后端，当前主要承载 Test Insights（`tap_platform/insights`，ClickHouse 适配器）。
- `apps/web`：TAP 非 AI 前端；`/prototype` 路由是完整产品设计基准（见[产品原型基准规范](guides/2026-09-22-product-prototype-baseline.md)），不代表后端已实现。

两个后端之间、两个前端之间由边界脚本禁止互相 import，当前没有共享包。

## 2. TAP AI 后端分层

模块位于 `apps/tap-ai-backend/src/tap/modules/<name>/`，按 `domain`、`application`、`ports`、`adapters` 分层，domain 层不依赖框架。

| 模块 | 职责 |
| --- | --- |
| `access` | Project 范围、授权策略与上下文 |
| `knowledge` | Source/Document 上传、分阶段摄取、切片维护、审阅、检索与带引用回答 |
| `graph` | 从文档抽取实体与关系、图谱任务与查询 |
| `chat` | Conversation、不可变 Turn、回答规划与 Turn 处理 |
| `ai` | Agent/Skill 资产 Revision、模型目录、LangGraph 交互图 |
| `governance` | 审计 |
| `test_management` | 测试方案生成、校验与发布（V1 冻结） |

HTTP 路由位于 `interfaces/http/routes/`；公共基础设施位于 `platform/{db,messaging,storage}`。进程入口位于 `entrypoints/`：`tapper_api`（API）、`relay_reconciler`（Outbox → Redis Stream 中继），以及 ingestion、parser、graph、generation、test design、prompt suggestion 六个 worker。`legacy_litellm.py` 与 `legacy_loopback_answer_runtime.py` 为遗留路径。

## 3. 基础设施

`compose.yaml` 中的服务：

| 服务 | 用途 |
| --- | --- |
| `mysql` | 唯一权威业务状态、Outbox 与 Knowledge Graph |
| `redis` | 可重建的唤醒信号（Redis Stream） |
| `milvus`（含 `milvus-etcd`、`milvus-minio`） | 可重建的文档检索投影，BM25 + dense 混合检索 |
| `litellm` | 所有模型调用的统一网关 |
| `tap-parser` | 隔离的文档解析 |
| `tap-minio` | 唯一对象存储：原件与中间产物（S3 API） |
| `clickhouse` | Test Insights 指标（`apps/backend`，不在 V1 交付路径） |
| `phoenix`（可选，profile `observability`） | OTLP 追踪查看器，仅在设置 `OTEL_EXPORTER_OTLP_ENDPOINT` 时接收导出的 span |

## 4. 主要数据流

**摄取**

1. API 接收上传，原件写入对象存储，文档与任务状态写入 MySQL，并写 Outbox。
2. Relay 把 Outbox 事件转发到 Redis Stream 唤醒 worker；worker 同时按固定间隔轮询 MySQL。
3. worker 按阶段执行解析（`tap-parser`）、切片、embedding，每阶段产物内容寻址写入对象存储。
4. 切片投影写入 Milvus；投影可从 MySQL 与中间产物重建。
5. 摄取任务每次认领（含租约过期回收）递增 `delivery_count`，达到上限时认领即置 `FAILED`（`lease-exhausted`）；瞬时失败（模型、对象存储、索引、解析器不可用或超时）未达上限时回到 `PENDING` 并按退避设置 `next_attempt_at`。删除任务不设投递上限。用户重试把 `delivery_count` 清零。

**问答**

1. 用户在 Conversation 中提交问题，生成不可变 Turn。
2. worker 在授权范围内经 Milvus 混合检索（RRF 融合）取回切片；`GraphAnswerEnricher` 按问题关键词附加最多 20 个图谱节点作为上下文。
3. 经 LiteLLM 流式生成回答（`ModelGateway.stream_structured`，首个 token 前失败退回非流式调用），完整 JSON 校验并校验引用后结果写回 MySQL；可选模型目录由 LiteLLM `GET /v1/model/info` 动态提供（60 秒缓存），新增模型只改 `deploy/local/litellm/config.yaml` 并重启 LiteLLM，后端无需改代码或重启。
4. 前端经 SSE 获取 Turn 事件并逐段渲染回答，支持 `Last-Event-ID` 续传；`turn.completed` 后以持久化回答（含引用）替换临时文本。

**流式与通知**

1. 生成 worker 用 `IncrementalJsonField` 从模型流中抽取顶层 `answer` 字段增量，按 150ms 或 200 字符批量追加 `answer.delta` 事件（校验租约令牌）。
2. Turn 被重新认领开始新 attempt 时，以及 Turn 以非完成终态结束前，写入 `answer.reset`；被撤回的增量不再出现在读取结果中。
3. 事件事务提交后经 `ConversationEventNotifier` 发布通知：Redis 适配器使用 Pub/Sub 频道 `tap:chat:{project_id}:{chat_id}`，内存适配器供测试使用；发布失败只告警。
4. SSE 循环读取新事件下发，会话无运行中 Turn 且已追平时结束；否则等待通知最长 15 秒，超时发送 `: keepalive` 并重新读库（通知丢失的兜底）。单连接最长 10 分钟，客户端凭 `Last-Event-ID` 续接。

**图谱**

1. graph worker 从已摄取文档抽取实体与关系，写入 MySQL 快照；`attempt_count` 达到上限时认领即置 `FAILED`（`attempts-exhausted`），瞬时失败（模型不可用、超时）未达上限时回到 `PENDING` 并按退避设置 `next_attempt_at`。
2. 查询 API 读取快照，载入内存图存储后返回节点、边与证据。

**重试策略与 worker 主循环**

- `tap/platform/retry.py` 的 `RetryPolicy`（默认 `max_attempts=5`、`base=2s`、`cap=300s`）为摄取与图谱任务提供全抖动指数退避与上限判断。
- `tap/platform/worker_loop.py` 的 `run_worker_loop`：单次迭代异常记录错误日志与 `worker.iteration.failed` span，按连续失败次数退避（上限 30 秒）后继续，成功一次即清零。摄取、图谱、生成、推荐问题、测试方案 worker 使用该循环；parser worker 是 socket 服务，不使用。

**Golden set**

`apps/tap-ai-backend/tests/quality/golden/` 存放脱敏语料、用例与确定性模型基线；`tap.quality.golden` 把语料写入进程内检索适配器后经生产回答编排评测。确定性模型回归由 `tests/quality/test_golden_regression.py` 纳入 `make test` 与 CI（`.github/workflows/ci.yml`），真实模型只报告不设门。见 [Golden set 指南](guides/2026-11-03-golden-set.md)。

LangGraph 交互图（`modules/ai/application/interaction_graph.py`）当前为固定的 `classify → admit → execute` 三步。

**推荐问题**

1. 四类触发写入 MySQL 刷新队列表，以（项目、用户、语言）为单位：知识发布（Ready 投影同事务内请求整项目刷新，立即到期）、对话完成（节流 10 分钟）、读取时命中过滤或首次读取（无历史缓存立即到期，其余过滤节流 10 分钟），以及每日兜底扫描（24 小时）。
2. 独立的 prompt suggestion worker 轮询队列认领到期行，调用 `PromptSuggestionService.refresh`：拼装知识库主题与图谱主要实体、本人历史提问来源、其他用户来源使用次数（仅次数，不含提问原文）作为模型输入，经模型网关 `generate_structured` 生成候选（每次最多 8 条，每条 1–3 个来源，来源须属于本次输入的知识库来源，语言与请求 locale 一致）。
3. 每条候选以当前用户授权、按对话回合的同一路径（冻结来源 → 回答规划 → `answer_conversation`，来源以 `ResourceMode.SCOPE` 限定，不保存对话）验证依据，仅未弃答且带引用的候选写入 MySQL 缓存；刷新失败保留旧缓存而非清空，知识库为空时缓存写为空列表。
4. `GET /prompt-suggestions` 只读 MySQL 缓存并与知识库当前来源状态比对，过滤掉来源已删除、未发布、当前用户无权限或版本已更新的条目；接口本身从不调用模型，过滤后为空返回空列表，且任一条目被过滤会重新排入一次刷新。

**可观测性**

1. API 处理请求时开启 `turn.request` span 并生成 traceparent，写入 `chat_turn.traceparent` 随 Outbox/Redis Stream 传给 worker。
2. worker 用同一 traceparent 开启 `turn.execute` span，检索、图谱扩展、模型调用、工具调用各自开子 span；span 结束经 `MysqlSpanExporter` 写入 `trace_span`，每次模型调用同时由 `MysqlModelCallRecorder` 写入 `model_call` 与 `model_call_content`。
3. 若配置了 `OTEL_EXPORTER_OTLP_ENDPOINT`，`BatchSpanProcessor` 同时把 span 导出到该 OTLP 端点（本地为 Phoenix）；未配置时仅写 MySQL。

## 5. 与 V1 目标的已知差距

| V1 能力 | 现状缺口 |
| --- | --- |
| [可靠问答](superpowers/plans/2026-09-29-v1-roadmap.md#1-可靠问答) | 真实模型 golden 基线分数（30–50 条真实标注问答）尚未产出，属业务验证项 |
| [知识图谱展示](superpowers/plans/2026-09-29-v1-roadmap.md#2-知识图谱展示) | 每次查询把整个快照载入内存 |
| [图谱脉络分析](superpowers/plans/2026-09-29-v1-roadmap.md#3-图谱脉络分析) | 仅关键词取节点拼入上下文；无多跳扩展、路径推理、关系边引用与路径高亮 |
| [Skills/Agents](superpowers/plans/2026-09-29-v1-roadmap.md#4-skillsagents) | 工具白名单硬编码为 `knowledge.search` / `knowledge.answer`；无导入能力 |
| [可观测性](superpowers/plans/2026-09-29-v1-roadmap.md#5-可观测性) | 追踪数据无保留期与清理任务，`model_call_content` 原文永久保留会持续增长；DashScope 部分模型算不出成本，需要手动在 `deploy/local/litellm/config.yaml` 配置 `input_cost_per_token`/`output_cost_per_token` |
