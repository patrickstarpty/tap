---
id: ADR-022
status: accepted
date: 2026-09-04
supersedes:
  - ADR-002
superseded-by: []
related-rfcs:
  - RFC-009
---

# ADR-022：首个正式交付采用自托管 Docker Compose 基线

## 背景

ADR-002 把 AKS 与 Azure PaaS 服务设为企业部署必选基线。当前客户方向更重视可控成本、内网部署和先完成产品方案验证；团队已经拥有 MySQL、Redis、对象存储、Milvus 与 LiteLLM 的本地 Compose 经验，执行侧也倾向复用外置 Jenkins。

## 决策

首个正式交付拓扑采用企业内网 Linux 主机上的自托管 Docker Compose：React Web、FastAPI API/BFF、Relay/Reconciler 及独立 Worker 由 TAP 管理；MySQL 保存权威业务状态和 Outbox，Redis 负责可重建唤醒，MinIO 保存原件、Bundle 与 Evidence，Milvus 保存可重建 `doc` 检索投影，LiteLLM 提供模型网关；Jenkins Controller 与 Pipeline Agent 作为外置 Execution Provider。

V0–VG 仅构成隔离 Validation 部署；P0 补齐身份与 RBAC，P1 完成 TLS、Secret 管理与轮换、观测、retention、备份恢复、容量、安全负矩阵和受控客户 Pilot 后，才可声明 Production ready。该基线不承诺高可用、在线扩容或多主机自动故障转移。超过已验证容量时，通过新环境部署、数据恢复或重建和受控切流扩展；Kubernetes/云 PaaS 是未来可选迁移目标，不是当前必需依赖。

## 考虑过的方案

- **继续以 AKS + Azure PaaS 为首发基线**：治理能力完整，但成本和运维前置过重，也与当前 Jenkins、自托管和快速验证方向不符。
- **把所有依赖打包进同一个容器**：部署表面简单，却破坏数据职责、升级隔离和故障恢复边界。
- **从验证 Compose 直接复制到生产**：忽略认证、TLS、Secret、容量和恢复门禁，不可接受。

## 后果

- Compose 文件、健康检查、具名卷、备份恢复和升级 Runbook 成为正式交付资产，而不只是开发便利工具。
- 所有服务保持 provider-neutral Port，避免把 Compose hostname 或具体 SDK 泄漏到领域契约。
- Validation、Staging 和 Production 必须使用不同配置、Secret 与数据，并经过各自门禁。
- Azure 专项设计可保留为历史/provider 参考，但不再是当前部署事实源。
