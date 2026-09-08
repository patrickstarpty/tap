# V0 Validation Scope 与可靠性门禁

评审日期：2026-09-06。唯一结论：**pass**。Task 5B 与 V0 出口通过，可进入 V1。源码提交 `f71f06c`；RFC-009 保持 accepted，平台实施计划保持 active，后续里程碑尚未实现。

## 验收范围

V0 建立固定 Validation Actor/Project、实时共同授权、Project 数据/HTTP/Origin 隔离、统一事件与 Problem、同事务 Audit/Outbox、有界恢复、独立对象存储和容器解析。此前确认的 FWD 浅色产品原型保持为入口，Library 实际上传和 Project API 持久验证均纳入确定性浏览器旅程。

本门禁不代表企业登录/RBAC、多项目切换、持久 Conversation、Source ledger、模型质量、Graph、Test Design、Recorder、Jenkins 或生产就绪。固定 Actor 不代表真实个人身份；Milvus 本地 doc 不代表企业 Azure 四索引。只有本门禁通过才进入 V1。

## 固定执行集

- schema-drift 与 `0006_validation_identity`、`0007_project_scope_backfill`、`0008_project_audit`、`0009_outbox_operations` 四项非空迁移/重放检查。
- 完整显式文件组：scope、project-audit、recovery-operator、storage、parser-security。两种授权共同 conformance、真实 Redis/SQL 隔离与恢复、MinIO/Azurite 原生契约及真实 Parser 安全检查均须有原生 testcase 身份证据。
- 原型完整 E2E：journey、app-restart、compose-restart、verify，包含恶意上传后恢复与最终真实 SQL/MinIO/Milvus 持久断言。模型使用明确 fake。

任一必需项跳过、xfail/XPASS、重试/flaky、非零退出、缺文件/阶段/身份、计数不符、证据摘要不符、源码漂移或清理失败均为 fail。零 skip 仅适用于固定必需集合，不通过移除全仓独立 opt-in 门禁制造零 skip。

## 来源与资源边界

每次运行创建私有全新报告目录，保存闭集命令/退出状态/原生证据摘要、规划基线及实际 HEAD/dirty/路径-模式-字节摘要；命令结束和资源清理后重新比较源码。含新建未提交 gate 源码，不能仅以 HEAD 冒充所执行的内容。

所有 SQL、Redis、MinIO、Azurite、Parser 和 E2E 测试使用确切可验证的独占资源。legacy relay 在导入前注入新独占数据库；其他 fixture 保留各自归属。原始 Secret、业务内容、浏览器 private state、receipt 凭据不进入报告；失败阶段缺少安全投影时明确 absent/fail，不伪造摘要。清理必须确认而非推断。

## 首轮失败与修正

首轮运行 `1491e93a0e2341988df94195fd533de1` 结论 fail，make 退出 2；执行前后源码一致。五项 schema/migration 均退出 0；scope/project-audit/recovery-operator/storage 分别 83/29/47/17 passed；parser-security 为 84 passed、4 failed、0 errors、0 skipped；完整 E2E 为 2/1/1/28 passed。该失败不会被第二轮覆盖。

四项解析探针失败源于测试镜像文件权限：gate 固定 umask077，使非敏感 probe child.py 成为 root0600，生产 UID65532 无法读取。测试 helper 现在显式固定0644，宿主父目录仍私有，生产镜像、用户、网络和资源限制未变。真实077 constraints 复现1 failed/3.53s，原四项实际用例修复后4 passed/39.09s；022/077 的无服务文件模式回归同时覆盖。

独立初审另发现：非零子命令结束后，外层 context 正常退出不能证明子进程嵌套 fixture 清理完成；旧 runner 仍标记 complete 并执行后续命令。最终 verdict 本来就是 fail，问题是清理推断与调度。现在所有非零子命令保守记为 unresolved，并在创建后续资源前停止，剩余命令显式 not-run；失败原生 artifact 仍保留。首轮旧 cleanup 字段不是独立证明，实际探针资源经过精确只读检查确认不存在。原生 XML 收据原本即存在，不声称修复了不存在的收据遗漏。

修复前惰性回归2 failed/1 passed，修复后完整 gate fixture41 passed/0.44s。同一独立审查者对三文件170行修复范围复审 Approved，无未关闭问题；Root 最终 make check 退出0。固定九文件摘要已核对，第二轮执行期间保持源码不变。

## 第二轮原生名称问题

第二轮 `9229f70e4e394b55a1b490bbe16d4742` 的 11 项命令均退出0，原生 pytest 264 passed，E2E 2/1/1/28 passed，各自零失败/跳过；执行前后源码一致，清理 complete。Root 核对25项日志/制品实际字节摘要和所有原生计数，并读取69项精确资源检查，全部剩余资源为空。但唯一总 verdict 仍为 fail：一个既有恶意上传测试将 oversized-header 字节自动拼入参数 ID，名称长8,367字符，超出报告1,024字符上限。

Root 裁定仅给该既有参数化测试的两个输入设置稳定短 IDs，保持原字节、断言、用例数量与报告长度上限。这个元数据问题之前被首轮更早的实际解析失败遮住。第二轮报告保持原始 fail，不修改 XML、源码摘要或 verdict；修正后重新执行固定门禁。

## 最终实际证据

最终运行 `872399d5e19a48198cdcd8fda2b31674`：`make gate-v0` 退出 0，11 项固定命令全部退出 0，cleanup 为 complete。五组原生 pytest 合计 264 passed；E2E 四阶段合计 32 passed；必需集合共 296 passed，0 failed、0 error、0 skipped，且无 xfail/XPASS、flaky 或 retry。

| 检查                                                 | 最终证据                                                                         |
| ---------------------------------------------------- | -------------------------------------------------------------------------------- |
| schema drift / 0006–0009                             | 21 张 authoritative tables，4 项迁移保留原 14 张非空业务表与身份、约束、重放断言 |
| scope                                                | 83 passed，0 failed/error/skipped                                                |
| project-audit                                        | 29 passed，0 failed/error/skipped                                                |
| recovery-operator                                    | 47 passed，0 failed/error/skipped                                                |
| storage                                              | 17 passed，0 failed/error/skipped                                                |
| parser-security                                      | 88 passed，0 failed/error/skipped                                                |
| E2E journey / app-restart / compose-restart / verify | 2 / 1 / 1 / 28 passed；当前 Library 上传与正式 Project API 持久断言              |

规划基线为 `a54ab433eae52500683a5ff6ff9d79466a30e1ca`；实际执行时 HEAD 为 `e377ee41b22b1cde0a865b4a424399252eb21911`、dirty=true，报告包含全部 379 个执行源码路径/模式/字节摘要，含十个 Task 5B 源码文件。资源清理后 sourceBefore 与 sourceAfter 完全相同；Root 再次核对当前字节和最终源码提交，不能只以 HEAD 代替工作区实际内容。

本地证据目录为 `.tapper/v0-gate/872399d5e19a48198cdcd8fda2b31674/`；闭合报告 SHA-256 为 `b61eddb8dccd3421f0f162e90fd7d5329d3b8bbc628edc4ccd3b931cf094c886`。Root 读取原生 JUnit 的 testcase/失败/跳过结构，并核对 25 个日志与制品的实际字节 SHA-256、四阶段 E2E 身份/计数/重试与清理。schema/migration、Project/Audit、恢复、存储均保留封闭拥有权证据；恢复组包含 16 对 nested MySQL 与 3 对 Redis start/complete 记录。

原生 pytest 摘要：

- `scope.xml`：`86a827ba0e877589854ea5e1045675d821185d2feaf58d5c941392fd2f63ab4f`。
- `project-audit.xml`：`89319abff59105908cb306a83d792206b30b17885fb6410156ac236e04562e97`。
- `recovery-operator.xml`：`3477e54ac629ee7f39c4632f3da86e28b54ccc4b980a3d1f4ddc0e945a3397bf`。
- `storage.xml`：`ef22bb21db99a15e67566ed8cf3310be0287dc0d58183d330edcef4576404ca2`。
- `parser-security.xml`：`0b1d8c6125557f7297ecc8c6020208d41bfe82bd30dee1bc75dc2850aa21f579`。

独立 validate 只检查报告结构与内部一致性，不把调用者提供的报告本身当作真实执行的认证证明；真实运行来源来自本次已观察到的固定生产者、原生制品和冻结源码。初始修复复审 Approved 与完整 make check 退出 0 发生于第二轮 gate 前；后续仅一行测试参数 ID 修改，经34项原生用例验证、独立范围复审及定向 Ruff/format 检查，最后十文件冻结后执行第三轮 gate。这些证据不冒充另一轮全仓 Backend 回归。

## 验证边界

Task 4 populated-ready/limit+1 SQL snapshot 由本项补充真实 SQL 断言；仍不是实际 Milvus operator rebuild。迁移和 nested cleanup 原始失败不得以最终重跑覆盖。本次 V0 pass 不覆盖或改写各历史 Review 中已有的完整回归失败与修正时序；不声称此前 source-mixed 的全仓回归已经重新完整通过。
