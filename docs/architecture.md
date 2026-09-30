# TAP 现状架构

本页只记录代码中可验证的现状，随代码更新。目标能力与验收见 [V1 总纲](superpowers/plans/2026-09-29-v1-roadmap.md)；历史设计见[归档](archive/index.md)。

## 1. 应用边界

- `apps/tap-ai-backend`：TAP AI 后端（Python 3.13 / FastAPI），Tapper 问答、知识文档、图谱、Agent/Skill 资产与测试方案生成。
- `apps/tap-ai-frontend`：TAP AI 前端（React / TypeScript / Vite）。
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

HTTP 路由位于 `interfaces/http/routes/`；公共基础设施位于 `platform/{db,messaging,storage}`。进程入口位于 `entrypoints/`：`tapper_api`（API）、`relay_reconciler`（Outbox → Redis Stream 中继），以及 ingestion、parser、graph、generation、test design 五个 worker。`legacy_litellm.py` 与 `legacy_loopback_answer_runtime.py` 为遗留路径。

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

**问答**

1. 用户在 Conversation 中提交问题，生成不可变 Turn。
2. worker 在授权范围内经 Milvus 混合检索（RRF 融合）取回切片；`GraphAnswerEnricher` 按问题关键词附加最多 20 个图谱节点作为上下文。
3. 经 LiteLLM 生成回答并校验引用，结果写回 MySQL；可选模型目录由 LiteLLM `GET /v1/model/info` 动态提供（60 秒缓存），新增模型只改 `deploy/local/litellm/config.yaml` 并重启 LiteLLM，后端无需改代码或重启。
4. 前端经 SSE 获取 Turn 事件（服务端轮询数据库，支持 `Last-Event-ID` 续传）。

**图谱**

1. graph worker 从已摄取文档抽取实体与关系，写入 MySQL 快照。
2. 查询 API 读取快照，载入内存图存储后返回节点、边与证据。

LangGraph 交互图（`modules/ai/application/interaction_graph.py`）当前为固定的 `classify → admit → execute` 三步。

**可观测性**

1. API 处理请求时开启 `turn.request` span 并生成 traceparent，写入 `chat_turn.traceparent` 随 Outbox/Redis Stream 传给 worker。
2. worker 用同一 traceparent 开启 `turn.execute` span，检索、图谱扩展、模型调用、工具调用各自开子 span；span 结束经 `MysqlSpanExporter` 写入 `trace_span`，每次模型调用同时由 `MysqlModelCallRecorder` 写入 `model_call` 与 `model_call_content`。
3. 若配置了 `OTEL_EXPORTER_OTLP_ENDPOINT`，`BatchSpanProcessor` 同时把 span 导出到该 OTLP 端点（本地为 Phoenix）；未配置时仅写 MySQL。

## 5. 与 V1 目标的已知差距

| V1 能力 | 现状缺口 |
| --- | --- |
| [可靠问答](superpowers/plans/2026-09-29-v1-roadmap.md#1-可靠问答) | 仓库内质量用例为空；SSE 为数据库轮询，无 token 级流式；摄取任务租约回收不递增 `attempt`（Turn 回收递增但无上限）、摄取瞬时失败直接永久失败、worker loop 无异常保护 |
| [知识图谱展示](superpowers/plans/2026-09-29-v1-roadmap.md#2-知识图谱展示) | 每次查询把整个快照载入内存 |
| [图谱脉络分析](superpowers/plans/2026-09-29-v1-roadmap.md#3-图谱脉络分析) | 仅关键词取节点拼入上下文；无多跳扩展、路径推理、关系边引用与路径高亮 |
| [Skills/Agents](superpowers/plans/2026-09-29-v1-roadmap.md#4-skillsagents) | 工具白名单硬编码为 `knowledge.search` / `knowledge.answer`；无导入能力 |
| [可观测性](superpowers/plans/2026-09-29-v1-roadmap.md#5-可观测性) | 追踪数据无保留期与清理任务，`model_call_content` 原文永久保留会持续增长；DashScope 部分模型算不出成本，需要手动在 `deploy/local/litellm/config.yaml` 配置 `input_cost_per_token`/`output_cost_per_token` |
