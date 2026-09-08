# Tapper V0 恢复与有界运维验收

评审日期：2026-09-06。结论：**Task 4 实现与定向验收通过**，提交 `a0f892f`，独立审查及测试修正的复审均 Approved。原始完整 Backend 回归的一项既有测试失败已用有界同步修正，并通过完整受影响测试文件；未重新运行整个 Backend，不将原始结果改写成全通过。V0 完整出口仍待后续任务。

## 实现范围

四项命令为 recover-uploads、scavenge-staging、rebuild-milvus 和 reconcile-all。CLI 要求明确的固定 Validation Project，limit 为 1–500，支持输出并复用原调用 key；参数及非 loopback 数据库/Redis 地址在 Provider I/O 前拒绝。每次执行重新经过 knowledge.operate 共同 Policy，校验当前有效身份和实际绑定范围。

0009 保存 operation receipt、archive 和 dead-letter，authoritative registry 共 21 张表。操作 receipt 持有原身份、参数摘要、幂等键、correlation、稳定 ID、数据库时间 lease 与 fencing；相同请求在 Provider 操作前读取原结果，内容冲突拒绝，失效持有者不能完成。过期接管以 partial 记录本次观察到的计数，不推断先前效果已经完成。最终结果、Project Audit 与 knowledge.operator.completed 在同一连接提交/回滚；外部 Provider 和既有 reservation 事务仍属于分别可恢复的效果。

Redis reclaim 只消费可信范围，trim 保留全部 consumer group 的 pending/未读消息，Redis 不可用时 MySQL 仍是事实源。归档保留原 envelope、identity、digest 和私有证据，批量复制/删除同事务；归档后重试仍返回原事实，改变内容或原 Chat message 的请求冲突。未知主版本和非法事件不被重投递成已知事件。

staging 以可信 Enterprise/Project 派生 namespace，清理只扫描当前范围并尊重 pins、grace 和 ETag。已持久化旧 reservation 可恢复，归属不明的 legacy orphan 保留。Milvus rebuild 在已有全局 alias lock 内新开 SQL 快照；遇到尚未完成的 publishing、缺失 manifest 或超限完整语料时中止，避免遗漏刚完成索引写入但尚未提交 SQL ready 的版本，不以截断语料替换完整索引。

## 验证证据

| 检查 | 实际结果 |
| --- | --- |
| 组件 RED / GREEN | Operator、scope staging、index loader、event digest、Redis capacity 与归档后 writer replay 均先观察到目标失败后修正；未捕获单次全文件字面量 RED |
| 最终恢复/归档/0009 字面量选择 | 27 passed，14 deselected，306.02 秒，exit 0，独占资源清理完成 |
| 真实 SQL Operator | receipt/Audit/Outbox 三写、重放、冲突、并发、过期接管、失效取消与 publishing 空档通过 |
| 真实存储与运行时组合 | 9 passed，2 deselected；8 项真实独占 Azurite，1 项真实 SQL + 明确的 Artifact double，非跨 Provider 事务证明 |
| 归档后 writer 回归 | 4 passed，8 deselected；原事实、内容冲突、并发归档/写入及 Chat message 等价性通过 |
| `make migration-check MIGRATION=0009_outbox_operations` | passed；原 14 张非空表数据保留，新约束及降级/重放通过 |
| `make schema-drift` | 21 tables，differences=[]，exit 0 |
| `make contracts`、`make check`、`git diff --check` | passed |
| `make knowledge-recover ARGS='--help'` | exit 0，显示四项命令及 Project/limit/retry key 参数 |
| 完整 Web 回归 | 修正已有测试的 20 毫秒瞬时进度竞争后，289 passed，17 files，21.20 秒，exit 0 |
| 原始完整 Backend 回归 | 2592 passed，9 skipped，1 failed，6 条已有警告，848.39 秒，exit 1；唯一失败为既有启动测试的 term 记录断言 |
| 独立审查与复审 | 均 Approved；无 Critical/Important，保留一项非阻断 SQL snapshot 覆盖建议 |
| 启动测试修正 RED / GREEN | 受控三秒启动先 1 failed；有界 trap-ready 后 0/3 秒两例通过，完整文件 89 passed，check/diff 通过 |

首次完整 Web 回归为 288 passed、1 failed，上传已完成时测试仍等待瞬时 52% 进度。修正仅让该测试使用已有 deferUpload/finishUpload 控制，在断言进度后才完成上传；不改变产品组件、共享 fake 或 timeout。定向文件 24 passed 后完整 Web 通过，原始失败保留为证据。

第一次 schema drift 指出复合 CHECK 的括号与关键字大小写差异；在独占数据库中核对 MySQL 实际规范化表达式后，使 metadata 使用等价形式，未更改谓词。原 0001–0008 与 14 表 baseline fixture 未改写。迁移和 drift 的最终结果对应加入归档幂等查询索引后的 schema。

独立审查指出：现有 SQL snapshot 回归覆盖 publishing 空档和空 ready 集合，尚未以非空 ready manifests 直接验证重建及 limit + 1 分支。代码明确拒绝超限而非截断；本项作为非阻断覆盖建议保留，不等于已完成该测试。

完整 Backend 回归唯一失败与 Task 2B 的既有观察一致：拒绝假 ready 的断言通过，但四个替身没有 start/term 记录。受控三秒初始化先复现相同 term 集合失败，再仅在目标测试副本中等待 trap-ready 后启动原有两秒窗口；正常/慢启动两种情况均通过。该修正证明并消除了测试对未同步初始化时序的依赖，不将受控复现称为原始运行唯一根因；实际启动脚本、就绪超时与十秒 subprocess 上限不改动。修正后两个参数化场景 2 passed，完整 `test_demo_commands.py` 89 passed（39.04 秒），`make check`、`git diff --check` 与 61 行修正的独立复审通过。此前批准的 39 个文件哈希保持不变，最终提交增加这一个测试文件；没有把原始完整回归改写成全通过。

九项跳过仍为已有真实 Milvus/Entra/Azure、隔离持久化 E2E 阶段、Codex capability 与真实模型 opt-in；六条警告仍为已有 Alembic path_separator 提示。主 owned wrapper 已返回 cleanup_complete，另按本轮 MySQL/Redis/Azurite 项目标签只读核对，容器、卷、网络均为空。

## 验收边界

尚未执行真实 Milvus rebuild；锁、别名和完整快照断言使用确定性 Provider double，并以真实 SQL 验证发布空档。未运行真实模型、Recorder/Jenkins、默认 Demo 或生产服务。组件 RED 证据与后来的完整选择分开记录，不制造未运行的命令。V0 完整出口仍等待 MinIO、隔离 Parser 和 Task 5B；RFC-009 保持 accepted，计划保持 active。

本机日志保存在 /private/tmp/，稳定命令、结论和限制由本记录与[实施计划](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md)保留；临时日志不是永久 CI 制品。

本轮日志：`tap-task4-literal-final-green.log`、`tap-task4-literal-final-cleanup.log`、`tap-task4-migration-final.log`、`tap-task4-drift-final.log`、`tap-task4-backend-full.log`、`tap-task4-broad-wrapper.log`、`tap-task4-cleanup-receipt.json`、`tap-task4-web-final.log`、`tap-task4-supervisor-delay-red.log`、`tap-task4-supervisor-delay-green.log`、`tap-task4-supervisor-file-final.log`、`tap-task4-supervisor-check-final.log`。独占主项目为 `tap-schema-f3136896d662` 与 `tap-task4-tests-5b3241d060cd`，最终无残留容器、卷或网络。
