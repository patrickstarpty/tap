# TAP — Test Automation Platform

TAP（**Test Automation Platform**）是 `engprod` 讨论沉淀出的自动化测试与研发效能平台。本仓库保存可评审、可演进的技术架构基线；Phase 1 workspace、公共 HTTP/SSE 契约、授权 Knowledge 纵向切片与本地 Milvus 检索实验已经建立，Web 应用、共享环境部署与生产加固仍未完成。

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
- 实现状态：`Phase 1 backend slices active; web/shared/production pending`
- 当前交付重点：`Phase 1 — Azure AI Search RAG + TAP Knowledge Chat`
- 下一实验增量：`Phase 1.5 — optional Codex Research / Knowledge Enrichment runtime`
- 默认仓库可见性：建议 `private`
- 下一决策点：见 [待确认项](docs/proposals/2026-08-20-open-questions.md)

## 本地中间件预置

Phase 1 默认本地 profile 仅预置确实存在替身的中间件：MySQL、Redis、Azurite Blob 和 LiteLLM Proxy；Milvus 通过独立实验 profile 按需启动。Azure AI Search、Entra ID 与 Key Vault 没有本地替身；单元测试应使用 fake/stub，集成测试与安全验收应连接受控 Azure 测试资源，而不是引入 Elasticsearch/OpenSearch、假身份服务或秘密库模拟器。

首次启动前复制环境模板并按需填入 LiteLLM provider 凭据：

```sh
cp .env.example .env
docker compose config
docker compose up -d --wait --wait-timeout 120
bash scripts/check-local-services.sh
docker compose ps
```

默认端口与用途：

| 服务 | 默认宿主端口 | 用途 |
| --- | --- | --- |
| MySQL 8.4 LTS | `3306` | Phase 1 业务事实、Outbox、权限与运行元数据 |
| Redis 7.4 | `6379` | 可重建分发、短时 fanout、非权威 live 状态 |
| Azurite Blob | `10000` | 本地 Blob 兼容接口与开发期对象读写 |
| LiteLLM Proxy | `4000` | 本地模型路由代理与统一 API 面 |

停止并保留数据卷：

```sh
docker compose down
```

清理包含持久卷的本地状态：

```sh
docker compose down -v
```

### 实验性 Milvus 检索门禁

Milvus 目前仅是本地、可回退的 `doc` 检索实验，不是共享非生产或生产默认后端，也不改变 Azure AI Search 的既有发布门禁。固定版本与脱敏预计算向量的可复现 correctness gate 为：

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

当前工作区尚未建立 `apps/web/`，因此本阶段只导出和校验上述 OpenAPI/SSE artifacts；Task 6 在其拥有的 `apps/web/src/shared/api/generated/` 目录中连接 TypeScript 生成。冻结安装使用 `uv sync --frozen --all-groups` 和 `corepack pnpm install --frozen-lockfile`，不依赖全局 pnpm。
