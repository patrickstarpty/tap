# Tapper V1 AI Agent 与 Skill Revision 验收

评审日期：2026-09-09。结论：**Task 7A 实现与验收通过**。主实现提交为 `4a4e814150ea303c21d7cc5216a0148c1a0bd1c0`，四轮审查修正最终至 `4f95e8b8430db486ce91e796a8b784f19d90c2e6`。独立审查最终为 Critical 0、Important 0、Minor 0；该结论只覆盖服务器批准的 AI Agent/Skill 不可变 Revision、Validation seed、只读 API 与选择器，不代表 Conversation 持久化、真实模型质量或生产治理完成。

## 实现范围

`0011_ai_agent_skill_catalog` 新增 Project-scoped `AiAgent`、`AiAgentRevision`、`Skill` 与 `SkillRevision` 账本。Revision 固定 creator、identity origin、content digest、Agent system-instruction/tool allowlist/output-schema digest、Skill instruction/template digest，以及从创建时即存在的可选 adoption lineage；领域模型拒绝可变集合、可执行内容、自引用和跨 Project predecessor。

Validation catalog 由版本化服务器配置幂等写入。MySQL Adapter 共用启用/历史解析语义，支持后续不可变 revision、对称禁用和保留历史引用；seed 重放保留 lifecycle status，并校验 Project、Enterprise、creator 和 identity。并发 seed 与 revision 分配使用 Project 级 MySQL advisory lock，锁绑定同一物理连接直至事务提交或回滚并完成释放；失败批次不留下部分记录，取消和超时也不把锁遗留在连接池。

只读 Agent/Skill list/detail API 在服务器端重新执行 scope 与 capability 授权，只返回已启用 Revision，并以 Problem Details 对齐生成 OpenAPI/TypeScript 合同。Web 选择器处理加载、空、错误重试、禁用、失效 Agent、退休 Skill 与 null Agent；加载或错误转换不会静默清空已选 Skill。

## 审查与修正

首轮独立审查发现 8 项 Important、2 项 Minor，涉及禁用后 seed 重放、集合可变性、历史 Skill 解析、revision 编号、并发 seed、adoption 自引用、选择器恢复态、错误合同、creator 身份和迁移证明。第二轮修正暴露 advisory lock 的连接生命周期与失败事务提交风险；第三轮关闭这些问题后，审查又定位到 `RELEASE_LOCK` I/O 期间的窄取消窗口。第四轮显式保留释放任务，在取消后等待释放完成或安全失效连接，再退出 pinned connection 并恢复 cancellation 语义。

同一审查者逐轮复核全部原始与新增问题。最终真实 MySQL 回归暂停 `RELEASE_LOCK`，证明释放完成前外层任务和连接均不退出，恢复后锁变为可用并重新抛出取消；最终复核未发现新 Critical、Important 或 Minor。

## 最终验证

| 检查                            | 实际结果                                                                                             |
| ------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Task 7A 最终完整默认 Backend    | 2943 passed、126 skipped；独占 owner `tap-schema-bb62d7733f5a` 完成并清理                            |
| Task 7A 最终完整 Web            | 319 passed                                                                                           |
| MySQL catalog 定向回归          | 并发 seed、v1/v2 持久编号、原子回滚、获取超时、取消与暂停释放窗口均通过                              |
| Migration / schema              | `0011` migration gate、`0005 → 0011` Source/identity 保持、非空 downgrade 拒绝与 schema drift 均通过 |
| `make contracts` / `make check` | exit 0；生成合同、Ruff、format、mypy、架构、Web build 与品牌检查通过                                 |
| 最终工作树                      | `git diff --check` 通过；只保留既有未跟踪 `node_modules`                                             |

完整默认套件在第二轮修正后的 `55b9cd7` 上运行；随后第三、四轮只改变已独立复核的锁生命周期、对应回归与选择器格式，并重新通过直接覆盖集、migration/schema gate、`make check` 和 diff check。本验收不把 opt-in skip、fake provider 或历史原型数据计为真实模型/产品证据。Task 8 继续实现 Conversation/Turn 双快照、generation worker 与可恢复 SSE。
