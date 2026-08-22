---
id: ADR-015
status: accepted
date: 2026-08-21
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-015：前端采用 React/TypeScript，后端采用 Python/FastAPI，按运行角色隔离

- **状态**：已确认（2026-08-21）。
- **决策**：TAP Web 使用 React + TypeScript；公共 API/BFF、Conversation/Turn、Retrieval、Ingestion control API 与后续平台控制面使用 Python + FastAPI/ASGI。首版保持一个仓库和共享领域 package，通过不同 entrypoint 把 `api-sse`、`turn-worker`、`ingestion-worker`、`embedding-worker`、`index-writer`、`relay-reconciler`、`agent-worker`、`execution-worker` 分角色部署。
- **原因**：当前在线主链主要等待 Azure AI Search、模型、MySQL/Redis 和 Blob I/O，Python ASGI 足以支撑既定规模；React/TypeScript 适合 Project/Conversation、流式回答、引用/Trace 和后续 Test IR/Run 工作台。风险来自阻塞 I/O、CPU 重任务混入 API、无界 fan-out、SSE/React 每 token 放大，而不是语言本身。
- **后果**：API/SSE 全链路异步；CPU/内存密集解析、Embedding、本地 rerank、Codex 和测试运行必须使用独立 process/Pod。流式恢复使用合并事件、REST snapshot + SSE tail、背压和分页；React 使用规范化状态、增量 Markdown 与长列表虚拟化。Python OpenAPI/JSON Schema 生成 TypeScript client/type，禁止手工维护两套状态机。
- **换语言门槛**：先通过 profiler 证明在异步化、隔离和横向扩容后，Python Runtime 仍主导 p99 或成本，才局部用 Go/Rust/native worker 替换高连接网关或 CPU parser；不整体重写平台。
