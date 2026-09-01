# TAP — Test Automation Platform

TAP（**Test Automation Platform**）是 `engprod` 讨论沉淀出的自动化测试与研发效能平台。本仓库同时包含可评审、可演进的技术架构基线，以及 Python/FastAPI Backend、React/Vite Web、公共契约和本地运行工具。Athena 本地知识工作区已经打通“上传文档 → 可恢复索引 → 限定来源问答 → 核验引用”的 `doc` 纵向切片；完整 Phase 1、共享环境部署与生产加固仍在推进。

## 一句话架构

TAP 以 **Test IR + Git 版本化 + 统一执行证据** 为核心，采用 **React + TypeScript 前端、Python + FastAPI/ASGI 后端**。Phase 1 先在 AKS 上交付基于 Azure AI Search 的确定性 RAG 与参考 Codex/Claude Code 交互模式的 Knowledge Chat；Phase 1.5 再以可拔掉的 `CodexRuntimeAdapter` 验证只读 Research 与受控 Knowledge Enrichment；Test IR/代码生成在 Phase 2 基础契约就绪后接入。在线问答不依赖 Agent，模型、Agent Runtime、BrowserStack 和自建执行网格都通过适配层接入。

已确认的企业技术栈：

```text
AKS + PaaS MySQL + PaaS Redis + Azure AI Search
+ Blob Storage + Key Vault + LiteLLM
+ React/TypeScript + Python/FastAPI
```

## 目标

- 让用户用自然语言或 BDD 创建测试，也能基于已有自动化资产做定向更新。
- 用稳定 Test IR 连接需求、BDD、脚本、Locator、Fixture、Hook、测试数据和运行证据。
- 在同一条 Run 时间线中关联 Git revision、Agent 行为、测试结果、自愈/RCA、证据和人工审批。
- 同时支持内网自建 Browser/Device Grid 与 BrowserStack，避免单一供应商依赖。
- 通过 LiteLLM 统一路由 Chat、Coder、Embedding、Reranker、Vision 模型。
- 默认隔离不可信代码，限制凭证、网络和高风险工具调用。
- 第一阶段提供可持续使用的 Project/Conversation 知识问答页面，并用逐条引用、Trace 与反馈闭环验收 RAG。
- 在不改变 Retrieval/Citation Contract 的前提下，以隔离异步 Worker 验证 Codex Agent Runtime；所有检索走 TAP API，所有生成只形成可验证、可审批的候选 Artifact。

## 非目标

- 不在已有 BrowserStack 能力可满足外部测试时重复建设设备云；内网场景保留自建网格。
- 不把 DeepSeek Harness、LangGraph 或 BrowserStack 的内部对象直接暴露为 TAP 公共契约。
- 不在 MVP 阶段构建通用低代码编排器或多云调度平台。
- 不让非确定性的 Agent 判断替代确定性的测试门禁。
- 不把 Codex CLI/SDK 变成 Knowledge Chat、ACL、Ingestion 或 Azure AI Search 写入的必需依赖。

## 文档导航

- [总体技术架构](docs/architecture/2026-08-20-overview.md)：边界、组件、数据、流程、安全、可靠性与部署。
- TAP 平台架构简图：[draw.io 源文件](docs/architecture/2026-08-27-tap-platform-architecture.drawio) / [SVG 预览](docs/architecture/2026-08-27-tap-platform-architecture.svg)：面向管理层说明输入、统一平台、业务结果与共享底座。
- RAG 知识问答简图：[draw.io 源文件](docs/architecture/rag/2026-08-27-rag-knowledge-business-flow.drawio) / [SVG 预览](docs/architecture/rag/2026-08-27-rag-knowledge-business-flow.svg)：用知识建设与在线问答两条主线说明从数据源到可溯源回答的完整链路。
- [整体架构评审](docs/reviews/2026-08-21-architecture-review.md)：评审结论、优先级问题、整改建议与分阶段决策门禁。
- [Milvus 本地检索实验评审](docs/reviews/2026-08-27-milvus-local-search-experiment.md)：记录真实数据库、空卷重建、embedding 预算证据与严格的决策边界。
- [Athena 本地知识 Demo RFC](docs/proposals/2026-08-27-rfc-005-athena-local-knowledge-demo.md)：本地来源优先工作区、运行边界与验收标准。
- [Athena 本地知识 Demo 计划](docs/plans/2026-08-27-athena-local-knowledge-demo.md)：纵向实现步骤与确定性门禁。
- [Athena 本地知识 Demo 验收](docs/reviews/2026-08-27-athena-local-knowledge-demo.md)：本地中间件、浏览器、持久化和可选真实模型的证据记录。
- [Athena 本地回答后端 RFC](docs/proposals/2026-08-31-rfc-006-athena-local-codex-answer-backend.md)：记录 LiteLLM/Codex 独占选择、固定 Embedding 与 fail-closed 验收。
- [Athena 单智能体、无工具 Codex 决策](docs/decisions/2026-09-01-adr-018-athena-local-codex-tool-free-answer.md)：记录精确 CLI/model/catalog 契约及其本地边界。
- [Phase 1：RAG 基础](docs/architecture/rag/2026-08-21-foundation.md)：第一阶段的范围、四索引、流水线、评测与验收标准。
- [数据切片与溯源](docs/architecture/rag/2026-08-21-chunking-and-provenance.md)：分型切片、稳定身份、revision lineage、删除与重建。
- [Azure AI Search 索引设计](docs/architecture/rag/2026-08-21-ai-search-index.md)：四类物理索引、字段、ACL、向量与蓝绿升级。
- [检索调优方案](docs/architecture/rag/2026-08-21-retrieval-tuning.md)：BM25/Vector/Hybrid/RRF/Rerank 的可复现实验阶梯。
- [TAP Knowledge Chat](docs/architecture/2026-08-21-knowledge-chat-ui.md)：Codex/Claude Code 式会话、流式状态、引用与 Trace 交互。
- [受控 Codex Agent Runtime](docs/proposals/2026-08-21-rfc-001-codex-agent-runtime.md)：后台 SDK/CLI/App Server 选择、异步 Job、沙箱、工具、凭证、生成与审批边界。
- [核心契约](docs/reference/2026-08-20-contracts.md)：RunSpec、事件、Provider Port 和状态机约束。
- [架构决策](docs/decisions/index.md)：架构决策、取舍与被覆盖的历史方案。
- [交付路线图](docs/plans/2026-08-20-roadmap.md)：从架构基线到可用 MVP 的阶段计划。
- [来源与可追溯性](docs/reference/2026-08-20-source-notes.md)：`engprod` 会话索引、官方资料和推断边界。

## 核心原则

1. **Test IR 是稳定中间层**：自然语言、BDD、低代码和脚本都映射到版本化 IR。
2. **Git 管序列化内容版本，MySQL 管目录投影、权限/流程与运行事实**：职责明确，禁止无约束双向写入。
3. **执行证据统一**：自建 Grid、BrowserStack、API Runner 都产出同一证据模型。
4. **平台拥有控制面**：身份、策略、状态、审批、审计和归一化结果由 TAP 管理。
5. **执行端可替换**：Agent、模型、BrowserStack、自托管 Runner 均通过端口接入。
6. **确定性门禁优先**：Agent 可以建议、生成和诊断，最终门禁必须落到明确规则。
7. **不可信输入默认隔离**：代码、网页内容、模型输出和第三方回调都不可信。

## 当前状态

- 架构状态：`v0.3 integrated platform + RAG baseline / review-ready`
- 实现状态：`Athena local doc Q&A slice implemented; full Phase 1/shared/production active`
- 当前交付重点：`Phase 1 — Azure AI Search RAG + TAP Knowledge Chat`
- 下一实验增量：`Phase 1.5 — optional Codex Research / Knowledge Enrichment runtime`
- 默认仓库可见性：建议 `private`
- 下一决策点：见 [待确认项](docs/proposals/2026-08-20-open-questions.md)

## Athena 本地知识工作区

Athena 是来源优先的本地 Demo 产品外壳，不是完整 Knowledge Chat。当前页面可上传、查看六阶段 ingestion、选择 ready 来源、发起单次非流式问答并打开逐条引用；没有登录、Conversation/history、SSE、停止/队列、Trace、Feedback 或 OCR。API、Web 和所有中间件只绑定精确 loopback；无身份验证仅适用于单机开发，不能开放到局域网或生产环境。Azure AI Search、Entra ID 与 Key Vault 没有本地替身，本地 Milvus 也不改变企业 Azure 四索引基线。

支持文本可提取的 PDF、DOCX、Markdown（MD）和 TXT。PDF 不执行 OCR；扫描件返回 `ocr-required`。服务端硬上限为每文件 `25 MiB`、最多 `50` 份未删除文档、每次回答最多选择 `20` 份 ready 文档。

首次启动：

```sh
cp .env.example .env
# 在 .env 中填入实际 provider credential；不要提交该文件
make bootstrap
make demo-up
make demo-check
make demo-dev
```

`make demo-up` 启动并初始化 MySQL、Redis、Azurite、Milvus 与 LiteLLM；`make demo-dev` 在 `127.0.0.1:8000` 运行 FastAPI，在 `127.0.0.1:5173` 运行 Vite Web，并启动 Relay 与 Athena Ingestion Worker。默认本地端口如下：

| 组件 | 默认 loopback 端口 | 职责 |
| --- | --- | --- |
| MySQL 8.4 LTS | `3306` | 文档/revision/job/manifest、query hash/所选 revision/citation 核验快照与 Outbox；不保存回答正文/history |
| Redis 7.4 | `6379` | 可重建命令分发与任务唤醒 |
| Azurite Blob | `10000` | 原文件、normalized/chunk/embedding artifact |
| LiteLLM Proxy | `4000` | 固定 Chat/Embedding alias 路由 |
| Milvus | `19530` / `9091` | 本地 `doc` 可重建检索投影与健康端口 |
| FastAPI / Vite | `8000` / `5173` | Knowledge HTTP API 与 Athena Web |

模型配置只来自服务端 `.env`，不在 UI 或单次请求暴露。默认及已验收的完整回答选择块为：

```dotenv
ATHENA_MODEL_BACKEND=litellm
ATHENA_ANSWER_BACKEND=litellm
ATHENA_CODEX_MODEL=gpt-5.6-sol
ATHENA_CODEX_REASONING_EFFORT=ultra
ATHENA_CODEX_TIMEOUT_SECONDS=300
ATHENA_CHAT_ALIAS=athena-chat
ATHENA_EMBEDDING_ALIAS=athena-embedding
ATHENA_EMBEDDING_DIMENSION=1536
```

默认 `ATHENA_ANSWER_BACKEND=litellm`；要使用已验收的本机 Codex 路径，只把该值改为 `codex`，其余固定值不变，然后停止并重新运行 `make demo-dev` 使 API/Relay/Worker 重新读取同一配置。若同时修改 LiteLLM route 或 credential，还要重新运行 `make demo-up`。两个值是启动时独占选择：LiteLLM 与 Codex 不会相互 fallback、hedge 或重试到另一后端。

无论选择哪一个回答后端，文档与查询 Embedding 都必须经 LiteLLM 固定 `athena-embedding` alias 发往阿里云百炼/DashScope `text-embedding-v4`，维度固定为 `1536`，并支持中文、英文和混合术语检索；这也是 LiteLLM 在 Codex 模式下仍为必需中间件的原因。LiteLLM 回答模式另使用 `athena-chat`。Codex 回答模式精确要求原生 `codex-cli 0.149.0`、`gpt-5.6-sol`、`ultra`、有效的本机 ChatGPT 登录、单智能体、零工具、单 API 进程内并发 `1` 和 300 秒超时，不读取或要求 `OPENAI_API_KEY`/`CODEX_API_KEY`。

Codex CLI 只是本机调用入口，不是本地推理：query 与所选 Evidence 会发送给 OpenAI；文档和 query 的 Embedding 内容会发送给阿里云百炼。该数据边界只获准用于 loopback、无认证的单操作者本地 Demo，不得据此开放 LAN、共享或生产服务。

Codex 的请求自有 canonical model catalog 会消除内建 CodeModeOnly、多智能体和 apply-patch metadata，并与 24 个禁用 feature 及显式 plan/input/agent overrides 一起保持 Direct tool registry 为空。这个 catalog 的固定 entry schema 只保证精确 CLI `0.149.0`，不是跨版本兼容承诺；CLI、登录、feature、catalog/schema、模型或能力任何漂移都会使 readiness/request fail closed，返回 `503 answer-unavailable`，且绝不调用 LiteLLM answer。Web 对该错误只显示“回答模型暂时不可用，请稍后重试。”

LiteLLM 用 `LITELLM_BASE_URL`、`LITELLM_MASTER_KEY`、`LITELLM_MODEL`、`OPENAI_API_KEY` 与 `LITELLM_ATHENA_EMBEDDING_MODEL`/`DASHSCOPE_API_KEY` 注入实际路由与凭据；`LITELLM_EMBEDDING_*` 只供单独批准的付费 Embedding research 使用，Athena runtime 不读取。

页面刷新会重新读取已提交的文档、ingestion/index 状态和必要的可重建状态；API/Web/Worker 进程重启与普通 Compose 停止/再次启动后也从这些持久事实恢复。当前渲染的回答只存在于 Web 页面内存，刷新会清空，本版没有历史回答恢复 API；用户可以基于仍为 `ready` 的持久来源重新提问。普通停止/再次启动保留具名卷：

```sh
make demo-down
make demo-up
make demo-dev
```

只有下面的 guarded 命令会不可逆删除精确 Compose project `tap-athena-demo` 的 MySQL、Redis、Azurite 和 Milvus 卷；命令拒绝其他 project 名称：

```sh
TAP_ATHENA_COMPOSE_PROJECT=tap-athena-demo \
  TAP_ALLOW_ATHENA_VOLUME_RESET=1 make demo-reset
```

### `demo-check` 故障定位

`make demo-check` 独立检查五个组件，只输出组件、结果和安全修复码：

| 组件 / 修复码 | 处理方式 |
| --- | --- |
| MySQL / `start-mysql` | 运行 `make demo-up`；确认 `TAP_DATABASE_URL` 与迁移 head 使用默认 loopback project。 |
| Redis / `start-redis` | 运行 `make demo-up`；确认 `TAP_REDIS_URL` 指向 `redis://127.0.0.1:6379/0`。 |
| Blob / `start-blob` | 运行 `make demo-up`；确认 `AZURE_STORAGE_CONNECTION_STRING` 指向 loopback Azurite，两个容器保持 private。 |
| Milvus / `start-milvus` | 为 Docker 分配至少 2 vCPU / 8 GiB，运行 `make demo-up`，并保留固定 reader/writer/provisioner 配置。 |
| Models / `configure-models` | 两种模式都要在 ignored `.env` 配置 `DASHSCOPE_API_KEY`，重启本地角色并确认 `athena-embedding`；LiteLLM 回答模式还要确认 `athena-chat`，Codex 回答模式则检查精确原生 `0.149.0`、ChatGPT 登录、tool-free catalog/feature 契约。Codex 失败不会回退 LiteLLM，用户只收到 `answer-unavailable` 安全文案。 |

### 确定性 E2E 与真实模型 smoke

日常验收使用隔离 project、真实本地中间件和 deterministic fake 模型，不消耗 provider 配额：

```sh
make demo-e2e
```

真实 provider gate 是单独的显式 opt-in；未设置开关时两个 smoke 文件精确产生两次有意 skip，且不会进入 provider/Codex 请求体。阿里门禁验证 `athena-embedding`、1536 维、有限数值及 zh→en/en→zh 相似度；Codex 门禁验证精确 `0.149.0 + gpt-5.6-sol + ultra` 的单智能体、零工具、grounded/cited/sanitized/cleanup 契约。缺凭据、provider `401`、维度或 catalog 漂移、非法 claim/citation、工具事件和清理不确定性都算失败，不会转为 skip：

```sh
set -a
. ./.env
set +a
TAP_RUN_ATHENA_REAL_MODEL_SMOKE=1 uv run --project apps/backend pytest \
  apps/backend/tests/smoke/test_athena_real_model.py -v -rs
TAP_RUN_ATHENA_CODEX_CONFORMANCE=1 uv run --project apps/backend pytest \
  apps/backend/tests/smoke/test_athena_codex_smoke.py -v -rs
```

2026-09-01 的验收证据为：阿里 `athena-embedding` 的 zh→en 与 en→zh 门禁均通过且维度为 `1536`；Codex bootstrap 和未打补丁的生产配置均通过，最新生产复验输出 `version=0.149.0 model=gpt-5.6-sol reasoning=ultra single_agent=true grounded=true cited=true sanitized=true cleanup=true elapsed_ms=24026`，pytest 为 `1 passed in 24.08s`、exit `0`。默认无授权执行为 `2 skipped in 0.72s`、exit `0`。证据不保存 query、Evidence、回答、向量、JSONL 或登录信息。

### 实验性 Milvus 检索门禁

Milvus 目前仅是本地、可重建且可替换的 `doc` 检索实验，不是共享非生产或生产默认后端，也不改变 Azure AI Search 的既有发布门禁；这里的检索实现可替换性不表示回答后端存在 fallback。固定版本与脱敏预计算向量的可复现 correctness gate 为：

```sh
make milvus-preflight

# 仅首次创建全新 volume；完成 root 轮换后不再设置该开关
TAP_ALLOW_INITIAL_MILVUS_ROOT=1 make test-milvus

# 已完成 root 轮换的既有 volume
make test-milvus

TAP_ALLOW_INITIAL_MILVUS_ROOT=1 \
  TAP_ALLOW_MILVUS_VOLUME_RESET=1 \
  make test-milvus-rebuild-empty
```

真实 embedding profile 是显式授权的付费研究入口，只能在注入未跟踪 provider 配置并单独批准后运行 `TAP_RUN_PAID_EMBEDDING_RESEARCH=1 make research-embeddings`。上述命令或单次 GREEN 都不表示 RFC 已接受、ADR 已变更或共享环境已获批准；生命周期建议以[本次实验评审](docs/reviews/2026-08-27-milvus-local-search-experiment.md)的完整证据为准。

## 开发工作区与契约

运行时和依赖图固定为 Python 3.13.12、uv 0.10.8、Node 22.22.0、pnpm 10.15.1、`uv.lock` 与 `pnpm-lock.yaml`。从仓库根目录执行：

```sh
make bootstrap
make contracts
make check
make test
```

`make contracts` 从 FastAPI 路由元数据和公共 Pydantic 模型确定性导出并检查 `contracts/openapi/api.json` 与 `contracts/events/chat-stream.schema.json`：JSON 使用排序键、两空格缩进、一个末尾换行，且不写入时间戳。HTTP DTO 与 SSE event models 是彼此独立的模型图；浏览器可见的 SSE schema 不描述 `text/event-stream` framing。

`make contracts` 同时更新并检查 `apps/web/src/shared/api/generated/` 的 TypeScript client/type。冻结安装使用 `uv sync --frozen --all-groups` 和 `corepack pnpm install --frozen-lockfile`，不依赖全局 pnpm。
