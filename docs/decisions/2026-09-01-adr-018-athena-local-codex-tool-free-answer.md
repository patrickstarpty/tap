---
id: ADR-018
status: accepted
date: 2026-09-01
supersedes:
  - ADR-017
superseded-by: []
related-rfcs:
  - RFC-006
---

# ADR-018：Athena 本地 Codex 回答固定为单智能体、无工具

## 背景

[ADR-017](2026-08-31-adr-017-athena-local-codex-answer-backend.md) 接受了 Athena 本地 `.env` 可选 Codex 回答端口，并保留 Ultra 内部委派。真实生产形态验收随后证明，精确 `gpt-5.6-sol` 的内建 catalog 会带入 CodeModeOnly、多智能体与 apply-patch metadata；只依赖 feature disable matrix 不能形成可验证的空工具面。当前需求也明确要求一个智能体、无工具，而不是委派继承。

这仍是 loopback、无认证、单操作者 Athena Lab 的回答后端决策。文档和 query Embedding 已固定经 LiteLLM `athena-embedding` 调用阿里云百炼/DashScope `text-embedding-v4`，维度 1536；本决策不改变该向量空间、本地 Milvus `doc` 投影或企业 Azure AI Search 四索引目标。

## 决策

- Codex 回答配置精确固定为原生 `codex-cli 0.149.0`、`gpt-5.6-sol`、`ultra`、一个智能体且不提供任何工具；生产支持集只含 `0.149.0`。
- 每次 readiness/request 创建 owner-only canonical `model_catalog_json`，其中精确 model entry 消除 CodeModeOnly、多智能体和 apply-patch metadata，并把 experimental tool 列表置空。
- 同一 invocation 显式禁用 24 个 feature，包括 `multi_agent`、`multi_agent_v2` 与 `goals`；另固定 `tools.update_plan.enabled=false`、`tools.experimental_request_user_input.enabled=false`、`agents.enabled=false`，不配置 MCP 且不传入任何 `--enable`。
- 非生成 readiness 必须用同一 catalog 运行 `debug models`，严格核对渲染后的 tool-free descriptor、feature inventory、原生 target identity、精确版本与 ChatGPT 登录；真实 JSONL 只允许一个 thread/turn/final agent message，协作或工具事件一律拒绝。
- canonical catalog 的固定 entry schema 有意与 CLI `0.149.0` 耦合，不构成跨版本保证。CLI、登录、feature、catalog 字段/默认值/渲染结果、Direct registry、模型或能力任一漂移都 fail closed 为 `answer-unavailable`。
- `.env` 只在进程启动时独占选择 `litellm|codex`，切换后必须重启本地角色。Codex 不重试且绝不回退 LiteLLM；LiteLLM answer 调用数必须保持为零。文档/query Embedding 在两个回答模式下都继续经 LiteLLM 发往百炼。
- Codex CLI 复用本机 ChatGPT 登录，不读取或要求 OpenAI/Codex API key，但 query 与所选 Evidence 会发送给 OpenAI。这个本地调用入口不是本地推理，也不获准用于 LAN、共享或生产。

## 考虑过的方案

- **保留 ADR-017 的 Ultra 内部委派**：需要证明子智能体继承全部禁用能力与父事件完整可见，且不符合当前一个智能体要求。不采用。
- **只增加 feature disables**：无法消除内建 model catalog 的 CodeModeOnly/协作/apply-patch metadata，真实生产形态曾在 answer gate fail closed。不采用。
- **使用 request-owned canonical catalog 加显式 overrides**：能在同一精确 CLI 上让非生成 readiness 与真实请求共享可验证的空 Direct tool registry，并保持 `ultra`。采用。
- **Codex 失败后回退 LiteLLM**：隐藏操作者选择、数据流向、成本和模型身份。不采用。

## 后果

- 真实 bootstrap 与生产未打补丁 conformance 都必须证明 `single_agent=true`、`grounded=true`、`cited=true`、`sanitized=true`、`cleanup=true`，默认无授权运行仍精确两次 skip。
- catalog entry schema 已成为精确 `0.149.0` 能力契约的一部分。升级 CLI 必须先更新 schema、渲染期望、feature/override matrix、fake contracts 和真实 opt-in gate，不能把未知字段或默认值宽松接受为 ready。
- 版本、登录、catalog 或能力漂移会牺牲可用性以保持边界清晰：readiness 失败，请求返回稳定 `503 answer-unavailable`，不尝试另一回答后端。
- 默认 300 秒 timeout、单 API 进程并发 1、有界 stdin/stdout/stderr/output、进程组清理、grounded claim/citation 复验和脱敏日志边界保持不变。
- Athena 本地仍依赖 LiteLLM 完成文档/query Embedding；本决策不表示本地 `doc` Milvus 投影已完成企业四索引、Entra、共享凭据或完整 Phase 1。

实现与验收证据见 [RFC-006](../proposals/2026-08-31-rfc-006-athena-local-codex-answer-backend.md)。
