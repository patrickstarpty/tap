---
id: ADR-014
status: proposed
date: 2026-08-21
supersedes: []
superseded-by: []
related-rfcs:
  - RFC-001
---

# ADR-014：Codex 作为可选、隔离的 Specialist Runtime

- **状态**：拟议，建议作为 Phase 1.5 Research/Enrichment POC 采纳；不扩大 Phase 1 出口范围。Test IR/代码生成待 Phase 2 前置契约就绪。
- **决策**：在 TAP `AgentRuntime` 端口之后增加 `CodexRuntimeAdapter`。后台长期集成首选服务端 Codex SDK；`codex exec` 只用于本地 POC、CI 和一次性批处理；只有需要完整会话、steer/fork、审批和细粒度流式事件时才 POC App Server，且在其 command/transport 仍被官方标为 experimental/unsupported for production 时不形成生产承诺。普通 Knowledge Chat、Ingestion、ACL、QueryPlan、Azure AI Search 写入和 Citation 不依赖 Codex。
- **用途**：Phase 1.5 只读 Research、跨代码/知识分析与 staging parser/enrichment 建议；Phase 2 再增加 Draft Test IR、最小语义 patch 和候选 Framework Code。Codex 只能通过 TAP Tool Gateway 调用阶段内已批准的窄接口，不能直接持有 AI Search、MySQL、Blob、Key Vault 或生产 Git 凭据。
- **原因**：Codex SDK 官方支持把 coding-focused Agent 集成进内部工具、工作流和应用，但 Agent Runtime 的线程、工具和 workspace 能力与 LiteLLM 模型路由、确定性 RAG 是不同层次。把它放在可替换 Provider 后面可以复用其代码理解与生成能力，同时保持供应商可替换、权限安全和 RAG 独立可用。
- **后果**：一 Attempt 一隔离 Worker；Phase 1.5 仅 `read_only`，Phase 2 候选 patch 才允许 `workspace_write`，始终禁止 `full_access`。命令网络及 web search/connectors/plugins/非 TAP MCP/Browser/Computer Use 分别默认关闭。Phase 1.5 enrichment 经 Validator/管理员审批后才交给标准 Indexer；Phase 2 代码只形成 local immutable validation commit/受控测试 ChangeSet 并完成确定性检查与人工审批，不持有生产 Git 凭据；Phase 3 才由正式 Commit Service 发布 remote branch/PR。详细设计见 [受控 Codex Agent Runtime](../proposals/2026-08-21-rfc-001-codex-agent-runtime.md)。
- **模型与认证门禁**：个人 Lab 可用受保护的 API key POC；共享后台不使用个人 ChatGPT 登录。企业优先评估短期 access token/workload identity。Codex 自定义 provider 需完整兼容 Responses API；LiteLLM 接入必须先通过 streaming/tool/cancel/usage 契约测试，否则作为显式、受审计的 direct-provider 例外。
