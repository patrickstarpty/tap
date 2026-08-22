# TAP — Test Automation Platform

TAP（**Test Automation Platform**）是 `engprod` 讨论沉淀出的自动化测试与研发效能平台。本仓库当前保存可评审、可演进的技术架构基线；实现代码尚未开始。

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
- [整体架构评审](docs/reviews/2026-08-21-architecture-review.md)：评审结论、优先级问题、整改建议与分阶段决策门禁。
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
- 实现状态：`not started`
- 当前交付重点：`Phase 1 — Azure AI Search RAG + TAP Knowledge Chat`
- 下一实验增量：`Phase 1.5 — optional Codex Research / Knowledge Enrichment runtime`
- 默认仓库可见性：建议 `private`
- 下一决策点：见 [待确认项](docs/proposals/2026-08-20-open-questions.md)
