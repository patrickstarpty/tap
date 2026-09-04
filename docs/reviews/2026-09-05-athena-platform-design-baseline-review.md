# Athena 知识与 Web 自动化平台设计基线评审

| 字段     | 结论                                                                                                                                                                                                                                                                                                                      |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 评审对象 | [RFC-009](../proposals/2026-09-04-rfc-009-athena-knowledge-web-automation-platform.md)、[架构总览](../architecture/2026-09-04-athena-knowledge-web-automation-overview.md)、[核心契约](../reference/2026-09-04-athena-platform-contracts.md)、[实施计划](../plans/2026-09-04-athena-knowledge-web-automation-platform.md) |
| 评审日期 | 2026-09-05                                                                                                                                                                                                                                                                                                                |
| 评审范围 | 产品边界、领域模型、数据权威、集成 Port、安全边界、迁移顺序、TDD 任务、里程碑门禁、客户文档一致性                                                                                                                                                                                                                         |
| 结论     | **READY**：可以把本设计作为 V0 方案验证实施基线；尚未实施，不代表 P0 身份已完成，也不代表 Production ready                                                                                                                                                                                                                |

## 执行摘要

当前基线已把 Athena 的知识问答放在交付顺序最前面，并把 Knowledge Graph、AI Test Design、Web Low Code Automation、Web Recorder、Playwright、Jenkins Pipeline Agent 与 Test Plan 结果回填组织为一条可逐阶段验收的主链路。Mobile 明确后置，不进入当前 Schema、迁移或代码路径。

方案验证使用固定 Validation Enterprise、Project 与 Actor，先验证平台功能价值。用户、Session、RBAC 和多 Project 产品化只在 VG 书面结论为 `continue` 后进入 P0；这项延后不允许把固定 Actor 包装成真实用户认证。P0 完成后仍需 P1 的 TLS、Secret 轮换、审计导出、备份恢复、容量和受控 Pilot，才能声明 Production ready。

## 已关闭的关键问题

| 主题                     | 最终处理                                                                                                                                                                                              |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 问答上下文时序           | 拆分不可变 Turn Input Snapshot 与完成时 Answer/Evidence Snapshot；requested/completed 事件分别引用对应 digest，生成任务同时固定同一 Turn 的双 digest。                                                |
| Knowledge 数据身份       | 固定 `Knowledge Source → stable Document → immutable Document Revision`，Legacy migration 保留 Document ID，不把历史 `source_id=document_id` 带入新合同。                                             |
| Knowledge Graph 交付状态 | 图内明确 Milvus `doc` 本地切片已实现；MySQL Knowledge Graph 已选型但 V2 尚未实现，避免把目标架构描述为当前能力。                                                                                      |
| 模型出口                 | V1 的 Knowledge、Graph、Test Plan 和 Automation generation 共用唯一 LiteLLM `ModelGateway`；现有 Codex CLI 直连只作为历史 loopback Demo，实施时移入显式 legacy composition，不能计入 V1/VG。          |
| Test Plan 与 Automation  | 可不关联；关联后严格双向 `1:1`。两类 BDD Step 使用独立稳定 ID，通过显式 Step Mapping 对齐；Run 开始时有关联才把同一个 Run 投影到 Test Plan Execution History。                                        |
| Automation 与生成草稿    | Web Test IR Action 使用封闭 vocabulary；`0015` 同时建立 generation job、proposal 和 append-only decision，固定双 snapshot digest、base Draft、request/idempotency digest，支持 crash/replay。         |
| Jenkins 装配             | Provider-neutral Port 的首个 Adapter 是 Jenkins；Validation bootstrap 必须完成真实 connection verify 与 callback nonce handshake 后才能 enable，测试 Fake 只允许进入一次性 isolated E2E composition。 |
| Jenkins Secret 边界      | Automation Secret 只由 Jenkins Credentials Binding 注入 Pipeline Agent；TAP `SecretLeaseResolver` 只供可信 TAP worker 的 `PROVIDER` 与 `CALLBACK_VERIFY`，不把 TAP SecretRef/lease 发给 Agent。       |
| P0 审计与管理控制面      | `0022` 包含 Platform/Auth Audit 和 MySQL 权威登录限流事实；`0023` 包含 Support Access 双人审批、PRODUCT Agent/Skill/Execution 配置、envelope-encrypted SecretRef 管理和 Validation 资产采用。         |
| 事件与生成合同           | Knowledge accepted/ready、Test Plan published、Automation published 及后续事件都明确归属唯一 registry、生成 Schema、payload test 和 `make contracts` 门禁。                                           |
| 实施计划可执行性         | 55 个唯一 Task；每个 Task 都有精确 Files、预期失败的 RED、复跑同一命令的 GREEN 和一个 Commit；19 个迁移从 `0006` 到 `0024` 连续，11 个实际 Review 日期留到执行时生成。                                |

## 里程碑授权边界

```text
V0 Scope/Reliability
  → V1 Trusted Knowledge
  → V2 Knowledge Graph
  → V3 AI Test Design
  → V4 Web LCA + Recorder
  → V5 Jenkins Result Loop
  → VG Solution Validation
       ├─ continue → P0 Identity/RBAC/Multi-Project → P1 Production/Pilot
       ├─ revise   → 返回明确的 V 里程碑修订
       └─ stop     → 封存证据并停止产品化投入
```

不得因为 P0/P1 已写入计划而提前实施。VG 的 `continue` 只授权开始 P0，不授权导入生产数据、真实客户 Secret 或生产 Jenkins 目标，也不等于生产就绪。

## 结构与一致性复核

- RFC、ADR、Plan 生命周期及 ADR 双向 supersession 一致。
- README、客户演示指南、路线图、当前架构、核心契约和实施计划对“现有原型 / 本地已实现 / 目标设计 / 计划实施”的表述一致。
- Markdown 相对链接、目录索引、Mermaid/代码围栏、draw.io 与 SVG XML 均可解析。
- 实施计划保持 55 个 Task、19 个连续 migration 和 11 个执行期 Review；无重复 Task、迁移缺口、提前 Modify、`TODO`、`TBD` 或 `FIXME`。
- 当前 React 原型回归与 Web check 用于证明文档所引用的现有原型没有因本次文档收口退化；它们不证明 RFC-009 后端能力已经实现。

## 评审结论

本基线可以进入实施计划的 V0。执行者必须从已提交的 planning baseline SHA 创建隔离分支/worktree，严格按 V0 → VG 顺序交付并在每个里程碑生成实际日期 Review。任何真实模型、Graph、Recorder、Jenkins 或业务验收门禁缺配置、skip、flake、使用 Fake 或证据不完整，都不能标记为通过。
