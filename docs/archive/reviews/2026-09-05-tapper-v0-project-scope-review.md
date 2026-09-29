# Tapper V0 Project 数据隔离验收

评审日期：2026-09-05。结论：**Task 2B 实现与定向验收通过**，提交 `4160984`。完整 Backend 回归有一项未改动的启动脚本测试失败，单独复跑两次通过，原因未确定；本记录不将完整回归标为通过，也不代表 V0 出口通过。

## 实现范围

`0007_project_scope_backfill` 将原 14 张业务表的既有记录绑定到固定 Validation Enterprise、Project、Actor，补齐 identity mode/origin、复合 FK 和 Project 范围唯一约束。迁移按 nullable columns、每批最多 500 行回填、孤儿与重复检查、约束、non-null 的顺序执行，保留原 ID、digest、revision、sequence、时间戳与对象 locator。未登记的历史事件在 DDL 前拒绝。

Chat、Document、Answer/Citation、Outbox 和 Projection 仓储强制绑定不可变 `ProjectScopeContext`，读写包含 Enterprise/Project 条件。相同内容 digest 可以在不同 Project 共存；Turn 同一幂等键但不同 message 会冲突。Cursor、Redis 去重/唤醒与保留锁包含 Project。Projection 仍使用物理 alias 的全局锁，另一 Project 在 Provider I/O 前被拒绝。

历史 Citation 保留当前 Answer 的同 Project 归属约束；可缺失的 Document/Revision/Chunk 通过带 scope 的 outer join 查询，不因旧 Revision 缺失而隐藏 Citation。该场景在真实 MySQL 中先观察失败，再修正并通过。

依照[核心契约 §2.1](../reference/2026-09-04-tapper-platform-contracts.md#21-现有唤醒的迁移兼容契约)，本项提前建立四种 transport compatibility event 的封闭注册表、同事务 value builder、非空 canonical envelope 与内容摘要。信封仅使用可恢复的旧事实，Redis 不复制内部 payload。Task 2C 继续扩展领域事件、Problem 与持久拒绝处理。

## 验证证据

| 检查 | 实际结果 |
| --- | --- |
| Task 2B 字面量定向选择 | 29 passed，11 deselected，0 skipped，owned wrapper exit 0 |
| `make migration-check MIGRATION=0007_project_scope_backfill` | passed；14 张表非空数据保留、身份 seed、范围约束、DDL 前拒绝、downgrade/replay 通过 |
| 真实 MySQL 多批次/中间态探针 | 503 条 Outbox 与 503 条复合主键 lineage，实际批次 `[500, 3]`；检查回填前 nullable、完成后 NOT NULL 与全部旧字段不变 |
| 修正后 `make schema-drift` | passed；17 张表，0 differences |
| 修正后 `make check` | passed，exit 0 |
| 完整 owned `make test` 的 Backend 阶段 | 2393 passed，9 skipped，1 failed；6 条已有 Alembic 配置弃用警告，完整 target exit 2 |
| 相同失败测试单独复跑 | 两次分别 1 passed，exit 0；未修改脚本、测试主体或 timeout |
| 单独执行 Make 的 Web 测试命令 | 250 passed，14 files，exit 0 |
| 测试隔离审查修正 | 3 个真实 owned MySQL 场景与 12 个 unsafe URL 拒绝用例共 15 passed；最终同 engine 绑定后复跑 13 passed |
| CHECK 比较器审查修正 | RED 8 failures；修正后整个比较器文件 21 passed，真实 schema drift 再次通过 |
| 独立审查及定向复审 | 两项问题均关闭，Approved |
| `git diff --check` | passed |

完整回归唯一失败为 `test_dev_supervisor_does_not_accept_http_200_with_unready_body`：退出码和拒绝假 ready 的断言通过，但四个 stub 子进程的终止记录为空；保存的日志也没有任何 stub 启动记录。该 supervisor 脚本、fixture helper 和测试主体均未被本项修改。两次单独复跑通过不足以证明失败原因，不能把时间竞争当作已证实结论；下一次必需完整回归仍需复核。

审查发现新增 Turn/Projection 测试接受任意调用者 DB URL 后清理数据，现改为每个场景自行持有一次性 MySQL 生命周期，并在 engine 创建前验证 ownership receipt。另修正 CHECK SQL 比较器，保留引号内空白、反引号、括号与转义，避免把不同字面量误判为相同约束。两项修正均先观察 RED，再完成定向 GREEN 与复审。

## 证据位置与后续边界

复跑命令见[实施计划 Task 2B](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md#task-2b-backfill-every-existing-business-row-into-the-validation-project)。实际 MySQL/Redis/Azurite 使用本次创建的隔离实例；Azurite 内外端口一致，退出仅清理自己创建的资源。没有连接默认或共享 Demo 数据库。

本机日志：`/private/tmp/tap-v0-task2b-final-migration.log`、`tap-v0-task2b-final-narrow.log`、`tap-v0-task2b-isolated-make-test.log`、`tap-v0-task2b-review-fixes-check.log`、`tap-v0-task2b-web-final.log`；修正证据为同目录的 `task2b-review-fix1-owned-green.log`、`task2b-review-fix1-final-turn-green.log`、`task2b-review-fix2-tests.log` 与 `task2b-review-fix2-schema-drift.log`。临时日志不是跨机器永久 CI 制品，稳定结果与可复跑范围保存在本记录和代码中。

裁定：在上述定向验收和复审通过后继续 V0 Task 2C；完整回归失败保留为待复核观察，不扩展为无证据的启动脚本修改。未执行的 9 项 opt-in 验证及真实模型、Recorder/Jenkins、生产环境均不算通过。RFC-009 保持 `accepted`；HTTP Project 路径、Audit、可靠性恢复与 V0 完整出口继续按计划实施。
