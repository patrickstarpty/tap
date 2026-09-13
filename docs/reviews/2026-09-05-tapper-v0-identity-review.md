# Tapper V0 固定验证身份验收

评审日期：2026-09-05。结论：**Task 2A 通过**。实现提交 `e206538`；事件迁移接口补全提交 `32839c0`。本次只完成固定验证身份与共同授权基础，V0 完整出口仍待后续任务验证。

## 实现范围

- 不可变 `AnonymousContext`、`PlatformScopeContext`、`ProjectScopeContext` 与 `IdentityMode`；服务端 `ValidationScopeProvider` 固定返回 `local / tapper-demo / tapper-local-user`，拒绝其他 Project 路径。
- `AuthorizationPolicy` 与 `IdentityRegistry` Port、固定验证 Adapter 和共同授权 gate。动作/资源种类使用闭集；每次授权从 MySQL 核对 Enterprise、Project 与 Actor 的当前有效状态。无数据库失败后的固定身份回退。
- `DemoCurrentPolicyVerifier` 在加载可检索版本前使用共同授权 gate，并保留原有 revision/hash/grant 检查。API runtime 显式注入 ScopeProvider 与 MySQL-backed policy。
- `0006_validation_identity` 创建 Enterprise、Project、Actor registry 与自然键 seed，并提供后续范围 FK 所需的 composite candidate keys。Authoritative metadata 现有 17 张表；原 14 张业务表数据不变。

尚未在此项实现登录、Session、Membership、RBAC、Project 切换器、所有业务表的 Project 回填或所有 HTTP 路径的范围 gate。业务仓储范围由 Task 2B 负责，HTTP 接入由 Task 3 负责。现有浅色产品原型保持不变。

## 验证证据

| 检查 | 结果 |
| --- | --- |
| Task 2A 字面量 RED 选择 | exit 2，因新 AuthorizationPolicy Port 尚不存在而收集失败；运行时装配与 composite candidate key 另有针对性 RED → GREEN |
| Task 2A 字面量 GREEN，开启一次性 MySQL 验证 | 46 passed，10 deselected，0 skipped，exit 0 |
| `make migration-check MIGRATION=0006_validation_identity` | passed；14 张旧表全部保留非空行，seed 精确验证，downgrade/replay 通过 |
| `make schema-drift` | passed；17 张表，0 differences |
| `make check` | passed，exit 0 |
| 最终隔离 `make test` Backend | 2343 passed，9 skipped；6 条已有 Alembic path 配置弃用警告 |
| 最终隔离 `make test` Web | 250 passed，14 files；完整 target exit 0 |
| 测试隔离修正后的真实 MySQL 定向复跑 | 4 passed；调用者 DB URL 使用不可连接哨兵，测试自行创建并清理 3 个一次性数据库 |
| 独立任务审查 / 定向复审 | 功能符合；测试隔离 P2 修正后 Approved，无剩余阻塞项 |
| `git diff --check` | passed |

可复核命令为计划 Task 2A 中的原始选择；实际数据库操作通过同一 owned middleware wrapper 注入一次性 MySQL、Redis、Azurite URL。Azurite 内外端口保持一致，退出时只删除本次创建的资源。没有更改默认 Demo 或共享数据库。

首次普通 `make test` 未注入隔离 URL，得到 2262 passed、30 skipped、54 failed；失败均为沙箱在执行 SQL 前拒绝 MySQL 连接。该次运行不算通过。随后完整 target 在一次性 MySQL/Redis/Azurite 环境重跑，取得上表最终通过结果。

审查还发现新 registry 测试会接受调用者 URL 并无条件恢复 `enabled=True`。现已改为测试自身持有 `isolated_mysql()` 生命周期，在有界 setup 中排除调用者 URL，并移除对既有身份状态的恢复写入；独立复审与真实数据库测试均通过。

9 项 skip 为需单独 opt-in 的 Milvus、Entra/Azure ACL、独立持久化 E2E、Codex conformance 和真实模型验证；不把它们计作本次能力证明。未运行真实模型、Recorder/Jenkins、容量或生产化验证。

## 证据定位与后续边界

本机临时日志为 `/private/tmp/tap-v0-task2a-literal-green.log`、`/private/tmp/tap-v0-task2a-check-final.log`、`/private/tmp/tap-v0-task2a-isolated-make-test.log`、`/private/tmp/tap-v0-task2a-identity-fix.log`。它们用于本次本地复核，不作为跨机器或永久 CI 制品；稳定结果、代码与可复跑命令记录在本 Review 和[实施计划](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md)中。

Task 2B 已开始，执行[核心契约 §2.1](../reference/2026-09-04-tapper-platform-contracts.md#21-现有唤醒的迁移兼容契约)明确的兼容事件映射与 Project 数据隔离。RFC-009 保持 `accepted`，整个 V0 仍在实施中。
