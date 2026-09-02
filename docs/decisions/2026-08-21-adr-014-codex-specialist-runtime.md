---
id: ADR-014
status: accepted
date: 2026-08-21
supersedes: []
superseded-by: []
related-rfcs:
  - RFC-001
  - RFC-007
---

# ADR-014：Codex 作为可选、隔离的 Specialist Runtime

- **状态**：已接受（2026-09-02）；作为 [RFC-007](../proposals/2026-09-02-rfc-007-phase-1-intelligence-layer-exploration.md) 的首个可替换 Runtime Adapter 决策。旧 [RFC-001](../proposals/2026-08-21-rfc-001-codex-agent-runtime.md) 已被拒绝，仅作历史设计记录。
- **决策**：在 TAP 拥有的 `AgentRuntime` 端口之后增加可关闭、可替换的 `CodexRuntimeAdapter`。公共 API、Artifact Schema 和 UI 不暴露 Codex 供应商模式。长任务集成优先评估服务端 SDK；`codex exec` 只用于本地实验、CI 和一次性批处理；App Server 只在确实需要且其契约稳定后单独 POC。Athena 的 Ingestion、Retrieval、Answer 和 Citation 不依赖该 Adapter。
- **用途**：P1.2 只用 `intelligence-readonly-v1` 和 `automation-design-v1` Profile，产生有依据的 Intelligence Report、Assumption Register、Automation Blueprint 和 Review Package。P1.3 仅在凭据隔离、workspace escape、Broker 扫描、patch policy 和独立 Validator 门禁通过后，才能以可关闭的 `automation-engineering-lab-v1` Profile 产生 Code Bundle 或 Candidate Patch。Codex 只能通过 TAP Tool Gateway 调用当前 Profile 已批准的窄接口，不能直接持有 Search、MySQL、Redis、Blob、Key Vault、Docker/Kubernetes、生产 Git、BrowserStack 或被测系统凭据。
- **原因**：Codex SDK 官方支持把 coding-focused Agent 集成进内部工具、工作流和应用，但 Agent Runtime 的线程、工具和 workspace 能力与 LiteLLM 模型路由、确定性 RAG 是不同层次。把它放在可替换 Provider 后面可以复用其代码理解与生成能力，同时保持供应商可替换、权限安全和 RAG 独立可用。
- **后果**：一 Attempt 一隔离 Worker/workspace；P1.2 仅 `read_only`，P1.3 才可能允许 `workspace_write`，始终禁止 `full_access`。Runtime 与可信 Controller、Artifact Broker 和 Validator 分离；命令网络、web search、Apps、Connectors、Plugins、非 TAP MCP、Browser 和 Computer Use 默认关闭。Phase 1 不调度真实测试、不写远程 Git 或外部系统，所有 Artifact 的 `execution_status` 为 `not_run`。完整产品、安全和评测契约见 RFC-007。
- **模型与认证门禁**：个人 Lab 可用受保护的 API key POC；共享后台不使用个人 ChatGPT 登录。企业优先评估短期 access token/workload identity。Codex 自定义 provider 需完整兼容 Responses API；LiteLLM 接入必须先通过 streaming/tool/cancel/usage 契约测试，否则作为显式、受审计的 direct-provider 例外。
