---
id: ADR-020
status: accepted
date: 2026-09-04
supersedes: []
superseded-by: []
related-rfcs:
  - RFC-009
---

# ADR-020：采用 Validation-first 交付顺序

## 背景

TAP 的目标产品是单一企业、多 Project、多用户的平台，但当前仍在快速验证知识问答、知识图谱、测试设计与 Web 自动化闭环。如果先完成账号、登录、Membership、RBAC 和完整多 Project 管理，核心产品假设的验证时间会被身份工程拉长；如果为了提速而让领域服务直接信任匿名请求或客户端传入的 Project，又会留下无法安全产品化的旁路。

## 决策

先在明确标记的 Validation Mode 中完成 V0–VG。V0 在 MySQL 注册一个固定 Enterprise、一个 Validation Project 和一个 typed Validation Actor Principal；服务端 `ScopeProvider` 只产生固定 `ProjectScopeContext`，`AuthorizationPolicy` 仍在所有 Project 读写和 Provider 副作用前执行。客户端不能覆盖 Enterprise、Project 或 Actor，所有新增资产、Revision、事件与审计记录从第一天保存稳定 `project_id`、`actor_id` 和 `identity_mode=validation`。

VG 通过且产品负责人明确决定继续后，P0 才实施 User、密码登录、Session、Membership、简单 RBAC、多 Project 管理和逐用户审计，并以同一 `ScopeProvider`/`AuthorizationPolicy` 契约替换 Validation Adapter。Validation 来源的执行配置和已发布测试资产不得直接晋级产品 Revision；Project Admin 必须审查并重新发布 `PRODUCT` Revision。P1 再完成生产安全、容量、备份恢复与客户 Pilot。

Validation Mode 只允许 loopback 或具有独立基础设施访问控制的隔离验证环境，禁止生产数据、生产 Secret 和生产被测目标。Validation 构建与配置不得直接晋级 Staging 或 Production。

## 考虑过的方案

- **先完成完整身份与 RBAC**：产品边界最早完整，但会把尚未验证的账号和管理能力置于知识与自动化闭环之前。
- **无身份、无 Project 的临时 Demo**：实现更快，但资产、事件和授权无法平滑迁移，也容易被误部署为共享服务。
- **由前端传入固定用户和 Project**：界面简单，但客户端可扩大范围，不形成可信授权边界。

## 后果

- 方案验证可以聚焦核心功能，同时保留目标产品所需的 Project、Actor 和 Policy seam。
- V0 必须先完成最小注册事实、数据库外键、授权契约、Validation 标识和跨范围负测试，不能把这些工作推迟到 P0。
- VG 只能证明固定 Scope 下的功能价值和技术可行性，不能证明多用户隔离、个人归因或生产安全。
- P0 需要显式迁移与 origin quarantine，不能把固定 Actor 的历史事件伪造成真实用户行为。
