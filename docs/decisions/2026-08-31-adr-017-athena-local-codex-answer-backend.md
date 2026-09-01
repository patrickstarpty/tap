---
id: ADR-017
status: superseded
date: 2026-08-31
supersedes: []
superseded-by:
  - ADR-018
related-rfcs:
  - RFC-006
---

# ADR-017：Athena 本地回答端口可选 Codex CLI

## 背景

Athena 本地 Demo 当前用一个 `ModelPort` 同时承担 query Embedding 和 grounded answer，真实路径统一由 LiteLLM 提供。操作者需要继续使用百炼 `text-embedding-v4` 的现有 1536 维向量空间，同时可以从 `.env` 选择本机已登录的 Codex CLI 生成最终回答，不配置额外 OpenAI API key。

这项需求只针对 loopback、无认证、单操作者的 Athena Lab。它不等同于 [ADR-014](2026-08-21-adr-014-codex-specialist-runtime.md) 的共享 Specialist Runtime，也不能放宽后者对个人登录、异步 worker、凭据和生产隔离的要求。

## 决策

- 把 query Embedding 与 answer generation 拆为独立端口；现有 `DocumentEmbeddingPort` 保持独立。
- 文档和查询 Embedding 始终通过 LiteLLM 固定 alias 使用百炼 `text-embedding-v4` 与 1536 维 Milvus vector space。
- 新增服务端 `ATHENA_ANSWER_BACKEND=litellm|codex`，默认 `litellm`；不增加浏览器模型选择字段。
- 首个 Codex 配置为 `ATHENA_CODEX_MODEL=gpt-5.6-sol`、`ATHENA_CODEX_REASONING_EFFORT=ultra`、`ATHENA_CODEX_TIMEOUT_SECONDS=300`。
- Codex adapter 只接收本次有界 query/Evidence，只返回闭合 answer/claims；Citation ID 和来源解析继续由 TAP 决定。
- `codex exec` 复用本机保存的 ChatGPT 登录，使用临时会话、空目录和临时 `HOME`，忽略个人/repo 配置，关闭 shell、浏览器、应用、插件、技能、文件和外部工具；只保留 Ultra 的内部委派。
- 首版候选版本为 `codex-cli 0.149.0`。npm `env node` launcher 必须解析为已验证 owner/mode 的同平台原生 binary 后直接执行；子进程不继承 `PATH`。调用使用固定 capability-disable matrix、`--json` 事件审计和不含 provider/存储秘密的最小环境。只有真实 opt-in conformance 证明 Ultra 嵌套委派继承禁用矩阵且父事件流完整可见后，该版本才进入支持常量并允许 readiness 通过；其他版本、安装 target 漂移或证明失败都 fail closed。
- Codex 不可用或输出不合规时 fail closed，不重试、不自动回退 LiteLLM；初始并发固定为 1。
- 本地调用入口不代表本地推理。query/Evidence 会发送到 OpenAI；Embedding 内容继续发送到百炼，文档必须明确披露该边界。
- 本决策是 local-only Athena Lab 例外，不替代 ADR-014，也不形成共享或生产部署承诺。

## 考虑过的方案

- **拆分端口，直接调用受限 `codex exec`**：职责与数据流清晰，复用本机登录，且不改变摄取或公共 API。采用。
- **保留合并 ModelPort 并委托**：代码改动较少，但持续误导 Codex 具有 Embedding 能力，并耦合 worker 与 answer backend。不采用。
- **把 CLI 包装成 OpenAI-compatible 服务**：增加常驻进程、协议、认证和运维面，对单机 Demo 过重。不采用。
- **Codex 生成 Embedding**：破坏已固定 vector space，需要重建索引，也不符合 CLI 能力边界。不采用。
- **Codex 失败自动回退 LiteLLM**：隐藏实际 provider、数据流向和成本，违背显式配置。不采用。

## 后果

- LiteLLM 仍是 Athena 的必需本地中间件，因为两个回答模式都依赖其百炼 Embedding route；Codex 模式只移除 LiteLLM chat route 的 readiness 要求。
- API/retrieval 需要分别注入 Embedding 和 answer ports；ingestion worker 不再依赖 answer capability。
- 本地操作者可以在重启后切换回答后端，但不能在页面或单次请求中切换。
- Codex 模式无需 OpenAI API key，却依赖本机 CLI、有效 ChatGPT 登录、OpenAI 可达性和账户额度。
- `gpt-5.6-sol + ultra` 会增加延迟与额度消耗，因此必须有 300 秒默认超时、单 API 进程并发 1、输出上限和无重试语义；多进程全局互斥不在本地 Demo 范围内。
- Ultra 的嵌套工具继承不是仅凭 CLI help 即可接受的假设；真实 capability conformance 是启用门禁，失败时不能通过关闭 multi-agent 静默改变操作者选择。
- 文档 prompt injection 的影响被限制在候选回答文本；任何工具事件、非法 claim 或 citation label 都使回答失败。
- 企业 Knowledge Chat、Agent Runtime 和生产凭据边界不变；若未来要共享部署该能力，必须另立 RFC/ADR。

详细设计和验收门禁见 [RFC-006](../proposals/2026-08-31-rfc-006-athena-local-codex-answer-backend.md)。
