# Tapper V0 Project Audit 账本验收

评审日期：2026-09-06。结论：**Task 3A 实现与验收通过**，提交 `a7cb719`，独立审查 Approved。冻结后的完整 Backend 回归 2560 passed、9 skipped，Web 289 passed；V0 完整出口仍待后续任务。

## 实现范围

ProjectAuditPort 要求显式 ProjectScopeContext、correlation_id 与业务 idempotency_key，返回不可变 Audit fact。MySQL Adapter 绑定调用者已有的活跃 AsyncConnection，不自行开启或提交事务，也不生成新的关联 ID。事实保存稳定 ID、UTC 时间、Enterprise/Project/Actor、identity mode/origin、action/resource/outcome、原 correlation、幂等键、内容摘要和安全 metadata。

当前闭集只有后续四项 Operator action（recover-uploads、scavenge-staging、rebuild-milvus、reconcile-all），资源为有明确 Project 身份的 project-maintenance，结果为 completed/partial/failed。metadata 仅允许六项有界整数计数、apply/dry-run mode 与 64 位小写 SHA-256 digest，最多八个键和 512 UTF-8 字节；不接受自由文本、正文、query、Prompt、Secret、locator 或 Provider payload。传入数据被复制为不可变值。

同 Enterprise/Project 的相同 key 与相同业务内容返回原 ID、时间和 correlation；不同 action/outcome/metadata/Actor provenance 冲突。MySQL 唯一键与 no-op upsert 处理并发，读取时校验持久事实并带 Project 条件。运行时只提供 connection-to-port 装配入口；各领域应用仍负责资源归属和合法状态转换。

## 验证证据

| 检查 | 实际结果 |
| --- | --- |
| Audit contract RED / GREEN | 缺少模块时 21 failed；实现后 21 passed |
| 最终字面量 Audit/0008 选择 | 23 passed，13 deselected，0 skipped，exit 0 |
| 真实 MySQL 事务与重放 | 同一 connection 三写提交/回滚、稳定重放、不同内容与 Actor 冲突、并发同/异内容、Enterprise/Project 隔离及大小写不同 key 均通过 |
| `make migration-check MIGRATION=0008_project_audit` | passed；原 14 张表每条旧记录所有字段保留，identity/backfill 与 Audit 约束通过 |
| 0008 降级/重放 | 0008→0007 只移除自己的 Audit 表；完整 0005 降级/重放通过 |
| `make schema-drift` | 18 tables，differences=[]，exit 0 |
| `make check`、`git diff --check` | passed |
| 冻结后完整 Backend suite | 2560 passed、9 skipped、6 条已有警告，543.16 秒，exit 0 |
| 完整 Web suite | 289 passed，17 files，exit 0 |
| 独立 Task 3A 审查 | Approved，无 Important/Minor findings |

Backend 完整回归在源码冻结后启动，直至运行结束源码未变。九项跳过均是已有的真实 Milvus/Entra/Azure、持久化 E2E 阶段、Codex capability 和真实模型 opt-in 门禁，不能据此声明这些门禁通过。六条警告为已有 Alembic path_separator 弃用提示。Task 3 的旧 Vite 断言修正已在本轮完整回归中通过。

owned wrapper 的最终 exit 为 0，并返回 cleanup_complete；该次 MySQL project 为 tap-schema-d32bc857436c，Redis/Azurite project 为 tap-task3a-tests-1f0f38a3bf48，均由本次运行创建并清理。未复用默认数据库，也未运行纯 make test 指向默认环境。

真实三写测试在一次性 MySQL 中创建实际 queued chat_turn、维护 Audit fact 和对应 turn.process_requested Outbox，验证一起提交或一起回滚。这是持久事务组合证明，不是 Operator 执行或 Chat Audit 产品语义的实现；Task 4 仍需用真实有界操作结果完成自己的三写验证。

新 migration 冻结自身 DDL，显式声明 Project/Actor FK、非空 provenance、Project 幂等唯一键和时间排序索引。原 0001–0007 与非空旧数据 fixture 未改写。迁移负向探针曾因重复幂等键遮蔽目标 FK 失败，已仅修正探针键并重新通过全部迁移验证。

## 后续边界

Task 4 才会接入 Operator、Redis/Outbox 恢复和完成事件。本项不提供 Audit API/UI、登录/个人身份审计，也不代表 RFC-009 的所有领域审计已经实现。固定 Validation Actor 的记录不反向归属于个人。V0 完整出口仍未通过，RFC 保持 accepted、实施计划保持 active。

本机日志在 `/private/tmp/`：task3a-contract-red.log、task3a-literal-red.log、task3a-literal-green-final.log、task3a-migration-check.log、task3a-schema-drift.log、task3a-check-final.log、task3a-web-tests.log、task3a-broad-wrapper.log、tap-task3a-backend-full.log。临时日志不作为永久 CI 制品；稳定结果与命令保存在本记录和[实施计划](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md)。所有真实测试只使用本次持有的一次性中间件；未运行默认 Demo、真实模型、Milvus Provider、Recorder/Jenkins 或生产操作。
