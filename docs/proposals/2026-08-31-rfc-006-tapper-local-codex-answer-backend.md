---
id: RFC-006
status: implemented
date: 2026-08-31
related-adrs:
  - ADR-017
  - ADR-018
---

> **命名归一化说明（2026-09-05）：** 本文只对产品和仓库标识做 Tapper 命名归一化，原日期、状态、决策、范围与评审结论未改变。命名归一化前的字节级原文以 Git commit `0eab801` 为准；下列命令或证据文本属于 identifier-normalized transcription，不再声明与该提交逐字节相同。

# RFC-006：Tapper 本地可选 Codex CLI 回答后端

> **现行处置（2026-09-04）**：本 RFC 保持 `implemented`，只记录 Tapper 既有 loopback Answer Adapter 的实现事实；直接 Codex CLI `AnswerGenerationPort`、个人 ChatGPT 登录和 `TAPPER_ANSWER_BACKEND=codex` 不属于 [RFC-009](2026-09-04-rfc-009-tapper-knowledge-web-automation-platform.md) V1 合同，也不得计入 V1 出口证据。可复用的解析、grounding 或错误处理逻辑若迁入新路线，必须经唯一 `ModelGateway` 和 LiteLLM Adapter/conformance suite 收口，不能保留第二模型出口。本 RFC 不授权独立 Intelligence Lab 或 Specialist Runtime；当前交付顺序以 [ADR-021](../decisions/2026-09-04-adr-021-knowledge-first-web-automation-delivery.md) 为准，下文关于 Intelligence-first、Azure 四索引企业基线及 ADR-014 旧 P1.2/P1.3 授权的表述均只保留历史语境。

> **当前阶段处置（2026-09-02）**：本 RFC 的已实现 Tapper Answer Adapter 继续作为独立本地能力维护；其中“完整 Phase 1 仍 active”沿用的是旧 RAG/Knowledge Chat 路线，现已由 [ADR-019](../decisions/2026-09-02-adr-019-phase-1-intelligence-layer-exploration.md) 后置。当前 Intelligence Lab 使用独立、provider-neutral 的 `AgentRuntime`，不复用 Knowledge Answer 语义。

## 摘要

Tapper 本地知识 Demo 将查询向量生成与回答生成拆成独立端口。文档摄取和查询向量始终通过 LiteLLM 的固定 `tapper-embedding` alias 调用百炼 `text-embedding-v4`，继续使用现有 1536 维 Milvus vector space；最终回答由服务端环境变量在现有 LiteLLM 路径和本机 `codex exec` 路径之间选择，不向浏览器暴露模型选择。

Codex 路径复用本机 Codex CLI 已保存的 ChatGPT 登录，不要求 OpenAI API key。批准配置精确为原生 `codex-cli 0.149.0`、`gpt-5.6-sol`、`ultra`、一个智能体且不提供任何工具。调用只接收本次查询及有界 Evidence，输出继续遵守现有 `AnswerGeneration`、Claim 和 Citation 约束；Codex 不参与摄取、Embedding、检索、ACL、Citation 解析或索引写入。

本 RFC 同时修复当前 LiteLLM 成功 Embedding 响应因缺少可选 `id` 而被误判为 `embedding-unavailable` 的兼容问题，并为 ingestion worker 增加脱敏的阶段失败日志。本方案只适用于 loopback、无认证的 Tapper 单机 Demo，不改变企业 Azure 四索引基线，也不把个人 ChatGPT 登录引入共享或生产服务。

## 背景

2026-08-31 的本地故障证据显示，LiteLLM `POST /v1/embeddings` 已返回 HTTP 200，但 Tapper 在约 17 ms 后把文档标记为 `failed / embedding / embedding-unavailable`。当前严格解析器要求 Embedding 顶层字段精确包含 `id`；LiteLLM `1.87.0` 的合法响应可以省略该可选字段，因此成功响应被错误包装为 `ModelUnavailable`。同时，`IngestionWorker.run_once()` 捕获安全阶段错误、持久化失败并继续轮询，却没有发出结构化日志，导致服务端只看到正常进程而没有失败原因。

本地 Demo 当前还用一个 `ModelPort` 同时表达 query embedding 和 answer generation，并由同一个 `LiteLLMAdapter` 实现文档 Embedding。这个合并边界使“Embedding 继续使用百炼、回答可选本机 Codex CLI”难以安全装配，也会错误暗示 Codex 是摄取依赖。

本 RFC 是 [RFC-005](2026-08-27-rfc-005-tapper-local-knowledge-demo.md) 的本地增量。它不是 [RFC-001](2026-08-21-rfc-001-codex-agent-runtime.md) 所定义的共享、异步 Specialist Runtime，也不改变 [ADR-014](../decisions/2026-08-21-adr-014-codex-specialist-runtime.md) 对企业 Agent Runtime、凭据和生产隔离的边界。

## 目标

- 正确接受 LiteLLM 省略可选 Embedding `id` 的成功响应，同时继续严格验证 model、data、usage、索引、向量数量、1536 维和有限浮点数。
- 在 worker 记录可定位但不泄密的 ingestion 阶段失败事件，使 `embedding-unavailable` 等用户安全错误能关联内部诊断原因。
- 通过 `.env` 在 LiteLLM 和本机 Codex CLI 两个回答后端之间选择；切换后重启本地运行角色即可生效，不增加 UI 或公共 API 字段。
- Codex 模式复用本机 ChatGPT 登录，不要求额外 OpenAI API key；Embedding 仍使用现有 `DASHSCOPE_API_KEY` 和百炼模型。
- 保持中英文 UTF-8 内容、跨语言召回、来源限定回答、Claim 校验和 Citation Resolver 的现有权威边界。
- 对 CLI 缺失、未登录、版本能力不兼容、超时、取消、超限和非法输出 fail closed，不自动切换提供方。

## 非目标

- 不把 Codex CLI、个人 ChatGPT 登录或本 RFC 的子进程适配器推广到局域网、共享环境、AKS 或生产部署。
- 不让 Codex 生成文档或查询向量，不改变 `text-embedding-v4`、1536 维 collection、manifest 或 index version，也不触发重建索引。
- 不让 Codex 参与 parsing、chunking、ingestion、Milvus 查询、ACL、QueryPlan、Evidence 选择、Citation ID 生成或原文解析。
- 不新增前端模型切换器、Conversation history、SSE、Agent 工具调用、Web Search、MCP、代码修改或工作区访问。
- 不实现 LiteLLM 与 Codex 之间的自动 fallback、hedging 或重试，也不把一个后端的失败伪装成另一个后端的成功。
- 不替代 RFC-001/ADR-014 的 Specialist Runtime；未来共享服务仍需独立认证、凭据 broker、隔离 worker 和生产安全评审。

## 方案

### 端口与运行时装配

把当前 `ModelPort` 拆为两个用途明确的协议：

- `QueryEmbeddingPort`：暴露固定 embedding model ID、dimension 和 `embed(query)`。
- `AnswerGenerationPort`：只暴露 `answer(query, evidence, profile_id)`。

现有 `DocumentEmbeddingPort` 保持独立。LiteLLM adapter 可以同时实现三个协议，但 API、retrieval application 和 ingestion worker 只接收各自需要的窄端口；Codex adapter 只实现 `AnswerGenerationPort`。`KnowledgeAPI` 的公共 HTTP DTO、Milvus adapter 和 Citation Resolver 不变。

运行时装配规则如下：

```text
Document ingestion ──> LiteLLM Embedding ──> DashScope text-embedding-v4 ──> Milvus

User query ──> LiteLLM query Embedding ──> Milvus hybrid search ──> bounded Evidence
                                                                  │
                                                                  v
                                  TAPPER_ANSWER_BACKEND=litellm ──> LiteLLM answer
                                  TAPPER_ANSWER_BACKEND=codex   ──> codex exec
                                                                  │
                                                                  v
                                           Claim validation ──> Citation snapshots ──> response
```

确定性 E2E 继续由精确的 `TAP_DEMO_MODE=e2e` 与 `TAPPER_MODEL_BACKEND=fake` 配对控制，不能选择 Codex。普通本地运行继续使用 `TAPPER_MODEL_BACKEND=litellm` 提供真实 Embedding；新的回答选择只在该真实模型模式下生效。

### 服务端配置

`.env.example` 增加以下非秘密配置；真实 `.env` 继续 ignored：

```dotenv
TAPPER_MODEL_BACKEND=litellm
TAPPER_ANSWER_BACKEND=codex
TAPPER_CODEX_MODEL=gpt-5.6-sol
TAPPER_CODEX_REASONING_EFFORT=ultra
TAPPER_CODEX_TIMEOUT_SECONDS=300
```

- `TAPPER_ANSWER_BACKEND` 只接受 `litellm | codex`，默认 `litellm`，因此现有环境升级后行为不变。
- Settings parser 先对 `TAPPER_CODEX_MODEL` 应用 `[a-z0-9][a-z0-9._-]{0,127}` 语法上限、对 reasoning 应用 `low | medium | high | xhigh | max | ultra` 闭合集；已实现 Codex adapter/readiness 在此基础上进一步只接受精确 `gpt-5.6-sol + ultra`，其他语法合法值仍 fail closed 为 `answer-unavailable`。
- `TAPPER_CODEX_TIMEOUT_SECONDS` 默认 `300`，允许范围 `30..900` 秒。并发固定为 `1`，首版不增加可调并发变量。
- LiteLLM 模式继续使用现有 `TAPPER_CHAT_ALIAS=tapper-chat`；Codex 模式仍要求 `TAPPER_EMBEDDING_ALIAS=tapper-embedding` 和 `TAPPER_EMBEDDING_DIMENSION=1536`。
- 两种真实回答模式都需要现有 `DASHSCOPE_API_KEY` 完成文档和查询 Embedding；Codex 模式只是不需要额外的 OpenAI API key。
- Codex 配置可以与 LiteLLM 配置同时存在于 `.env`；只有被选择的回答后端参与 readiness 和请求执行。

配置值只来自服务端环境。浏览器不能提交 backend、model、reasoning、timeout、CLI path、sandbox 或 capability。

### Codex CLI 适配器

首版精确支持本机已安装且已通过真实 capability conformance 的 `codex-cli 0.149.0`；生产支持集是只含 `0.149.0` 的 singleton。其他版本即使可以启动也不能自动进入 ready；升级必须先更新 CLI capability/catalog contract 和真实 opt-in conformance，再修改受支持版本常量。

`CodexExecAnswerAdapter` 使用 `asyncio.create_subprocess_exec` 和启动前解析的原生 CLI 绝对路径直接构造 argv，不经过 shell。若 `command -v codex` 指向 npm 的 `#!/usr/bin/env node` launcher，resolver 必须解析其包内、与当前平台匹配的 `vendor/.../bin/codex` 原生 executable；不能执行 JS launcher，也不能依赖继承的 `PATH`。原生 binary、symlink target 和安装根之间的路径组件必须由当前用户或 root 拥有，且不能对其他主体开放写权限；版本/capability 检查与真实请求使用同一个已验证 target，target identity 变化后 readiness 失效。

每个请求创建权限收紧的空临时目录、临时 `HOME` 和输出 schema，查询与 Evidence 以 UTF-8 JSON 从 stdin 传入，绝不放进 argv、环境变量或日志。子进程环境从空 mapping 构建，只设置 `LANG`/`LC_ALL`、本次请求的 `TMPDIR`/`HOME`，以及启动前解析的真实 `CODEX_HOME` 供 CLI 读取认证；不得继承 `PATH`、`DASHSCOPE_API_KEY`、LiteLLM key、数据库/Blob/Milvus 凭据或任意 `TAPPER_*` 请求配置。调用固定以下边界：

- `--ephemeral`：不保存 thread/session 历史。
- `--ignore-user-config` 和 `--ignore-rules`：不加载个人配置、repo rules 或其模型/能力覆盖；认证仍使用本机 Codex CLI 保存的登录状态。
- `--skip-git-repo-check`、私有空工作目录和 `--sandbox read-only`。
- `--model gpt-5.6-sol`、`model_reasoning_effort="ultra"`、`approval_policy="never"` 和 `--strict-config`。
- `--json`、`--output-schema` 与 `--output-last-message`：stdout 必须是有界 JSONL，最终输出必须符合闭合的 `answer + claims` JSON Schema。Adapter 只保留闭合的计数/完整性审计，不持久化或记录可能包含输入输出的原始 JSONL；任何工具或协作事件都终止并拒绝结果。
- 对 `0.149.0` 显式禁用 24 个 feature：既有 shell/code/browser/app/plugin/skill/image/workspace/auth/tool 路径，加上 `multi_agent`、`multi_agent_v2` 和 `goals`；不配置 MCP server，不传入任何 `--enable`。
- 每个 readiness/request 创建 owner-only canonical `model_catalog_json`，其中精确 `gpt-5.6-sol` entry 去除 CodeModeOnly、多智能体和 apply-patch metadata，并将 experimental tool 列表置空；同时显式设置 `tools.update_plan.enabled=false`、`tools.experimental_request_user_input.enabled=false` 与 `agents.enabled=false`。`debug models` 必须在非生成 readiness probe 中呈现精确的 tool-free descriptor，才允许真实请求启动。

Adapter 在启动/ready 检查中通过同一原生 target 验证精确版本、所需 CLI flags/features 和 `codex login status`。stdout、stderr、JSONL 和最终消息分别设置字节上限；到期、取消、输出超限或关闭时终止并 reap 整个子进程组。首版不重试，以避免重复消耗 ChatGPT 额度和产生不明确的双请求。

内建 Sol catalog 会带入 CodeModeOnly/协作/apply-patch metadata，仅检查 CLI help 或 feature inventory 不足以证明零工具。实现因此把 catalog 内容、渲染后的 model descriptor、24 个 feature 状态和三个显式 config override 都纳入 readiness。这个 request-owned catalog 的固定 entry schema 有意与精确 CLI `0.149.0` 耦合，不是跨版本保证；字段、默认值、rendering 或 Direct registry 语义任何漂移都使 backend 不 ready，不能放宽 parser、恢复工具、切换模型或降低 `ultra` 来绕过门禁。

并发 `1` 是单个、唯一 Tapper API 进程内的 semaphore。`make demo-dev` 继续只支持一个 API process/worker；本 RFC 不声称为多个 API 进程提供全局额度互斥。任何多进程或共享部署都超出 local-only 范围，必须另行设计分布式限流和认证隔离。

### Grounding、语言与来源

Codex 只接收应用已经检索和裁剪的 Evidence payload，包括有界 evidence label、内容和已有来源 metadata；它不获得 Milvus、Blob、MySQL 或文件路径。系统提示把 Evidence 明确标记为不可信引用材料，不能改变模型路由、能力或输出契约。

输出沿用现有闭合语义：

- `answer` 是有长度上限的最终文本。
- 每个 `claim` 的文本必须逐字对应 `answer` 中一个完整段落，并引用一个或多个本次 Evidence labels。
- Adapter 再次执行与 LiteLLM 路径相同的 label、数量、长度和完整段落校验；模型不能生成公共 Citation ID。
- Citation Resolver 只根据已验证的 Evidence labels 创建和解析 snapshots；每个非拒答实质 claim 至少有一个可解析来源。
- Evidence 不足、相互冲突或无法形成有效 claim 时返回现有 abstention/受控失败，不编造来源。

摄取和查询统一使用百炼 `text-embedding-v4` 的同一 1536 维空间。验收 fixture 同时覆盖中文问题检索英文来源、英文问题检索中文来源以及中英文混合术语。回答默认跟随问题的主要语言，并保留必要的原文术语；语言判断不改变检索范围或 Citation 规则。

Codex CLI 虽在本机执行，但模型推理仍由 OpenAI 服务完成；查询与所选 Evidence 会发送到 OpenAI。该边界已由本地操作者明确接受，且必须在 `.env.example` 邻近说明和 README 的本地模型章节继续披露。

### Embedding 兼容与日志

LiteLLM Embedding parser 把 `object`、`model`、`data` 和 `usage` 作为必填字段，把 `id` 作为已知可选字段。`id` 存在时仍验证其类型和上限；无论是否存在，都继续验证固定 alias/允许 label、顺序连续的 index、预期 batch 数、1536 维有限浮点 vector 和 usage。未知或矛盾结构仍 fail closed，不能因本修复放宽为任意 provider payload。

Ingestion worker 在安全阶段错误已经持久化之后记录一条结构化事件，至少包含：

```text
event=tapper.ingestion.stage_failed
document_id / revision_id / job_id / attempt
stage / safe_error_code / internal_diagnostic_code
exception_type / duration_ms
```

日志不包含 API key、认证状态正文、endpoint、文件名、文档内容、query、Evidence、向量、provider 原始 body、Codex prompt/output 或完整 stderr。底层异常先映射为闭合的内部 diagnostic code，再记录；公共 Problem Details 和文档安全错误摘要保持现有脱敏语义。

### Readiness 与错误语义

- Embedding readiness 始终检查 LiteLLM 的 `tapper-embedding` route。
- LiteLLM 回答模式额外检查 `tapper-chat` route。
- Codex 回答模式改为检查 CLI 可执行文件、能力和登录状态，不要求 LiteLLM chat route；失败使 readiness/demo-check 失败，并让问答返回受控 `503`。
- Liveness 不因外部模型暂时不可用而失败。
- Codex 模式绝不自动回退 LiteLLM；日志记录实际选中的 backend、内部失败分类和匿名 timing，不记录输入输出。
- 模型端口错误细分为 Embedding 与 answer 两类。Embedding 失败继续映射 `https://tap.example/problems/embedding-unavailable`；回答后端失败映射新增的 `https://tap.example/problems/answer-unavailable`。两者都返回 `503`，且不把 CLI stderr、provider 或登录详情返回浏览器。

### 测试策略

- **Unit/contract**：先增加没有顶层 `id` 的 LiteLLM 200 response RED test，再实现兼容；保留 malformed fields、维度、index、NaN/Infinity 和 alias drift 的拒绝测试。
- **Worker logging**：使用 `caplog` 验证每个可恢复阶段失败恰好记录一条事件，并验证 key、endpoint、文件内容、raw body 和异常原文不出现。
- **Port/config**：覆盖端口拆分、默认 LiteLLM、显式 Codex、fake E2E 排斥 Codex、无效 model/reasoning/timeout 和 backend-aware readiness。
- **Codex adapter**：使用临时 fake executable/安装树，不调用真实 OpenAI；验证 JS launcher 被解析为同平台原生 target、不安全 owner/mode 和 target identity 变化被拒绝，以及精确版本门禁、request-owned catalog entry schema/rendered descriptor、24-feature/三项 override 矩阵、单智能体/零工具 JSONL、临时 `HOME`、无 `PATH` 的最小环境、输入只走 stdin、schema/claim 校验、JSONL/stdout/stderr 上限、单进程并发 1、timeout、cancel、process-group reap 和零 fallback。
- **Cross-language/citation**：使用确定性双语 fixture 验证中英双向召回、来源选择约束、每个 claim 的 citation 和点击原文仍绑定相同 revision/hash/anchor。
- **真实 capability/smoke**：新增独立、显式 opt-in 的本机 Codex gate；未设置开关时精确 skip，不能进入普通 `make test` 或隔离 `make demo-e2e`。支持 `0.149.0` 前先用 bootstrap override 运行单智能体/零工具 conformance，再用生产 singleton 支持集原样复跑，验证 sentinel 不可读、零协作/工具事件、grounded answer、citation 与 cleanup。报告只保存版本、通过项和匿名 timing，不保存 prompt、Evidence、回答、JSONL 或认证信息。

## 替代方案

### 保留合并 ModelPort 并增加委托 facade

改动较少，但接口仍暗示 Codex 可以生成 Embedding，也让 ingestion worker 间接依赖 answer backend。后续 readiness、关闭顺序和测试替身会继续耦合，因此不采用。

### 把 Codex CLI 包装成 OpenAI-compatible 本地服务

可以让 LiteLLM 继续作为统一外观，但需要新增常驻进程、协议转换、认证、健康检查和日志边界。对单机 Demo 过重，也没有消除不可信 Evidence 与 CLI 工具面的隔离问题，因此不采用。

### Codex 同时生成 Embedding

本机 Codex CLI 不是当前 1536 维 Embedding provider。切换会要求重新生成全部 vectors 并验证跨语言质量，且仍不能消除外部模型服务依赖，因此不采用。

### Codex 失败后自动回退 LiteLLM

可提高表面可用性，但会违背 `.env` 的显式选择，混淆数据流向、成本、诊断和模型身份。操作者已选择 fail closed，因此不采用。

## 风险与缓解

- **提示注入读取本机数据或调用工具**：使用临时 `HOME`、忽略个人/repo 配置，在空目录运行，以 request-owned catalog、24 个禁用 feature 和三项显式 override 保持单智能体/零工具，并审计事件；任何协作/工具事件或不可观察状态都使 backend 不 ready。该边界仅批准 loopback 个人 Lab，不能据此形成共享服务安全声明。
- **Ultra 延迟和额度消耗较高**：超时默认 300 秒、上限 900 秒，并发固定 1、不重试；日志只记录匿名 timing。操作者可通过 `.env` 明确改用 LiteLLM。
- **CLI 版本、catalog schema 或安装 target 漂移**：首版只接受 `codex-cli 0.149.0`，并同时核对原生 target identity、owner/mode、所需 flags/features、request-owned catalog entry 与渲染结果；`--strict-config` 在配置失配时 fail closed。支持新版本或安装形态前必须先更新 resolver、catalog/capability matrix、fake CLI tests 和真实 opt-in conformance。
- **本机登录失效或额度耗尽**：ready/demo-check 显示依赖不可用，请求返回稳定 503，不泄露账户详情，也不自动切换后端。
- **数据离开本机的误解**：README 和示例配置明确说明 Codex CLI 只是本地调用入口，query/Evidence 会发送到 OpenAI；Embedding 仍发送到百炼。
- **Embedding parser 过度放宽**：只把已观察到的 `id` 改为可选，保留其余结构、类型、模型、维度、数量和数值校验。
- **日志再次无诊断或泄露内容**：定义闭合 diagnostic code 和字段 allowlist，测试成功、失败和异常链的日志负面断言。
- **本地例外被误报为企业架构**：RFC、ADR、README 和 readiness 文案都标注 local-only/no-auth；ADR-014 的共享/生产边界保持不变。

## 迁移或发布方式

1. 先以测试复现无 `id` 的 HTTP 200 Embedding 响应和 worker 零日志，再修复 parser 与结构化诊断。
2. 拆分 query Embedding/answer ports，保持 LiteLLM 默认路径和 deterministic E2E 全部 GREEN。
3. 增加 Codex 配置解析、backend-aware readiness 和 fake-executable contract tests。
4. 实现受限 Codex answer adapter，接入现有 claim/citation validation；不修改公共 HTTP 契约或 Web 模型选择。
5. 增加双语 fixture、失败/取消/超限测试及显式 opt-in 真实 Codex smoke。
6. 更新 `.env.example`、README、Demo 检查和相关架构/计划文档，运行 narrow tests、`make check`、`make test`、`make demo-check`、`make demo-e2e`、`git diff --check` 和 Markdown/link 检查。

此变更没有数据库 migration、Milvus schema 变更、vector rebuild 或数据清理步骤。普通 `demo-down/up` 继续保留文档、ingestion 和索引数据；不得使用 destructive reset 作为升级步骤。

### 实施证据（2026-09-01）

- 全量确定性/本地门禁：`make check` exit `0`；`make test` 为 Backend `2244 passed, 26 skipped, 5 warnings`、Web `128 passed`；Codex 模式 `make demo-check` 的 `mysql/redis/blob/milvus/models` 五项均为 `ok`；`make demo-e2e` 为 `12 passed, 2 warnings` 且隔离 journey 通过。七条 warning 都来自未修改的 `apps/backend/alembic.ini` `path_separator` 弃用基线。
- 默认未授权执行：两个 opt-in smoke 精确 `2 skipped in 0.63s`，exit `0`，没有进入 provider/Codex 请求体。
- 阿里 Embedding：alias `tapper-embedding`，维度 `1536`，zh→en 与 en→zh 均为 `true`，`elapsed_ms=669`，exit `0`；输入与向量未记录。
- Codex bootstrap：`version=0.149.0 model=gpt-5.6-sol reasoning=ultra single_agent=true grounded=true cited=true sanitized=true cleanup=true elapsed_ms=55379`，`1 passed`，exit `0`。
- Codex 生产未打补丁 gate 最新复验：`version=0.149.0 model=gpt-5.6-sol reasoning=ultra single_agent=true grounded=true cited=true sanitized=true cleanup=true elapsed_ms=21652`，`1 passed in 21.71s`，exit `0`。

这些结果实现了本 RFC；完整 Phase 1 仍为 `active`，RFC-005 保持 `implemented`。本 RFC 的当前单智能体、无工具决策见 [ADR-018](../decisions/2026-09-01-adr-018-tapper-local-codex-tool-free-answer.md)，它替代了 ADR-017 的历史多智能体选择。

## 验收标准

- LiteLLM 返回合法、无顶层 `id` 的 Embedding 200 response 时，文档完成 embedding/publishing 并进入 `ready`；非法结构仍以稳定错误失败。
- 任一 ingestion 阶段失败后，MySQL 中保留安全状态，服务端同时出现一条可关联的脱敏结构化日志；秘密、原文、向量和 provider body 均不出现。
- 默认未设置 `TAPPER_ANSWER_BACKEND` 时，行为与现有 LiteLLM answer path 一致。
- 配置 `codex + gpt-5.6-sol + ultra` 且本机已登录时，回答经过 Codex CLI 并通过同一 claim/citation validation，无需 OpenAI API key。
- Codex 缺失、未登录、能力不兼容、超时、取消、输出超限、非法 JSON 或越界工具事件都明确失败，LiteLLM answer 调用次数保持为零。
- Codex 子进程的 argv、环境、临时目录和日志中没有 query、Evidence、provider key 或回答；子进程环境不含 DashScope、LiteLLM、数据库、Blob 或 Milvus 凭据；请求结束后无遗留子进程或 session 文件。
- npm `env node` launcher 不进入请求进程树；实际执行的是已验证的同平台原生 Codex binary，且父 `PATH` 不进入子进程环境。
- 非 `codex-cli 0.149.0`、request-owned catalog entry schema/渲染结果漂移、缺少任一固定禁用 feature/override、未证明单智能体/零工具，或运行时出现协作/工具事件时，Codex readiness/请求 fail closed。
- 中文问题可以从所选英文来源产生有引用的回答，英文问题可以从所选中文来源产生有引用的回答；取消选择的文档在 hits、claims 和 citations 三层均为零。
- 每个非拒答实质 claim 至少有一个可解析 citation，且引用仍定位相同 document revision、content hash 和 anchor。
- deterministic E2E 不启动 Codex 或真实 provider；未授权真实 Codex smoke 产生一次有意 skip，不被描述为真实模型验证。
- `make check`、`make test`、`make demo-check`、`make demo-e2e`、`git diff --check` 和文档链接检查通过。

## 未决问题

无。回答后端、Codex model、reasoning、超时、fallback、Embedding provider/vector space、数据流向和 local-only 边界均已明确。
