# Tapper V0 事件与错误契约验收

评审日期：2026-09-06。结论：**Task 2C 实现与定向验收通过**，提交 `2728053`；独立复审 Approved。完整 Backend 回归的 4 个旧断言失败已通过定向复跑关闭，但没有将原始失败运行改记为通过，也未重新运行最终源码的完整 Backend suite。V0 完整出口仍待后续门禁。

## 实现范围

- 在同一 registry 中登记 18 种领域事件与 4 种历史 compatibility event，校验类型/版本、payload、aggregate 身份与 sequence，保持 payload 不可变和内容摘要稳定。私有事件 Schema 与公开 HTTP/SSE 分离。
- 通用 Outbox writer 使用调用者的活跃事务；相同 Project 幂等键与相同事件内容返回原事实，不同内容冲突。业务请求是否相同仍由对应应用命令判断。
- 非法持久事件在 claim 事务中记为 `delivery_failed`，保留原始事实，写固定安全原因并继续处理合法记录。未知版本、非法 shape、范围矛盾和不可转换为 canonical UTC 的时间戳均有验证。
- 29 种 Problem 使用唯一注册表约束绝对 URI、status、safe title/detail、retryable 与条件性 failureStage。HTTP 每个请求生成关联 ID，响应 body/header 一致；SSE 使用同一安全定义。真实授权拒绝为 403，旧文档状态变化保留 409。
- Web 从生成的错误定义验证响应，将传输/非法响应与注册 Problem 分开，保留取消语义。OpenAPI 使用单一 Problem component，运行时与离线生成结果一致。

生成入口仍为 `make contracts`，产物为 [OpenAPI](../../contracts/openapi/api.json)、[公开 SSE](../../contracts/events/chat-stream.schema.json)、[私有 Project event](../../contracts/events/project-event.schema.json)、[Problem registry](../../contracts/problem-types.json) 和生成的 Web 类型。没有修改已完成的 `0007` migration 或重新设计产品界面。

## 验证证据

| 检查 | 实际结果 |
| --- | --- |
| 最终 Task 2C 字面量 event/problem 选择 | 68 passed，0 skipped，exit 0 |
| HTTP/Problem/event/generated 与 architecture 组合 | 180 passed；之后时间戳修正另跑覆盖选择 |
| 最终时间戳修正后的 event/generated 选择 | 60 passed，exit 0 |
| 真实 owned MySQL writer 验证 | 3 passed；覆盖事务回滚、重放/冲突、并发、Project 隔离、领域事件与非法行处理 |
| 时间戳真实 claim 回归 | RED 1 failed，复现 OverflowError；GREEN 1 passed，2 deselected，exit 0，合法同批记录继续处理 |
| 时间戳边界单测 | 上下界各一例，RED 2 failed；修正后已计入最终通过选择 |
| 旧 runtime 断言定向修正 | RED 4 failed；GREEN 4 passed，172 deselected |
| 最终完整 runtime unit 模块 | 176 passed；6 条已有 Alembic 配置弃用警告 |
| 最终完整 Web 测试 | 267 passed，14 files，exit 0 |
| `make contracts` 与最终 `make check` | passed；生成制品字节、类型、lint、format、架构、build、品牌守卫通过 |
| 独立审查 / 定向复审 | 时间戳 P2 修正后 Approved，无剩余阻塞项 |
| `git diff --check` | passed |

完整 owned `make test` 的原始 Backend 结果为 **2487 passed、9 skipped、4 failed**，6 条已有警告，target exit 2；因此 Make 未运行 Web 阶段。四个失败均为 `test_unavailable_codex_discovery_keeps_api_live_and_answers_closed` 的参数场景，旧断言仅接受 type/title/status/detail，而正确的新 503 响应增加 correlationId、failureStage 和 retryable。修正后的测试保留完整安全 body 比较、32 位小写十六进制关联 ID 与 body/header 一致性，不放宽脱敏断言。其定向 4 例和整个 runtime 模块均通过，Web 另行完整运行并通过。

该完整 Backend 运行在最后的 OpenAPI component 去重和时间戳修正之前启动；最终改动以其覆盖的 HTTP/generated、event、真实 claim、runtime、Web 与 check 补验。上表不构成“最终源码完整 Backend suite 已重跑”的声明。上一项 Task 2B 的 supervisor 失败在本轮完整运行中通过，但没有据此断言其根因。

## 审查修正与后续边界

复审关闭的 P2 是持久时间戳 `9999-12-31T23:59:59-23:59` 可触发 UTC 转换溢出，逃出非法事件处理边界并回滚整批 claim。现在信封构造先验证 canonical UTC 可表示性，仅将该转换的 OverflowError 变为既有 ValueError；不扩大 claim 的异常捕获。真实 MySQL 回归证明异常记录单独终止，原事实和安全原因保留，合法同批事件可继续处理。

本项是契约与持久事件基础。引用资源的 Project 归属仍由拥有该资源的应用事务验证；事件登记不等于生产流程已经实现。现有 Turn HTTP 路径仍是明确的 501 占位；后续流程负责完整业务请求幂等、Audit/Worker/Provider 的关联传播和 Manifest 前置条件。Task 4 继续实现归档与受控 redrive。

本机复核日志位于 `/private/tmp/`：`task2c-literal-final.log`、`task2c-final-contracts.log`、`task2c-owned-full.log`、`task2c-fix1-unit-green.log`、`task2c-fix1-db-green.log`、`task2c-final-runtime.log`、`task2c-full-web.log` 与 `task2c-final-check.log`。临时日志不作为永久 CI 制品；代码、命令和稳定结果保存在本记录及[实施计划](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md)。所有真实 DB 操作使用本次持有的一次性 MySQL/Redis/Azurite，均完成自己的资源清理；未连接默认 Demo、真实模型、Recorder/Jenkins 或生产环境。

RFC-009 保持 `accepted`。Task 3 开始接入 Project HTTP 路径、运行环境查询、Origin 与当前浅色原型中的 Validation Mode 提示。
