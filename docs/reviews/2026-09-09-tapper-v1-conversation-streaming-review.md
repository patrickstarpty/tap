# Tapper V1 Conversation 持久化与可恢复流验收

评审日期：2026-09-09。结论：**Task 8 实现与验收通过**。主实现提交为 `22b288b5969fffafdffc6ece52fd6005dc5c8e07`，五轮审查修正最终至 `1d249610f5fe7f1cfae5283889e6ebcba12cf380`。独立审查最终为 Critical 0、Important 0、Minor 0；该结论覆盖持久 Conversation/Turn、不可变双快照、generation worker、事件/Outbox、SSE 恢复和已知迁移形态收敛，不代表 Task 9 产品壳接线或真实模型质量门禁完成。

## 实现范围

`0012_conversations` 建立 Project-scoped Conversation、Turn Input Snapshot、Answer/Evidence Snapshot 与 Artifact Link，并为旧 `chat_turn.chat_id` 回填同 ID Conversation。新 Turn 接受时由服务器解析 model、Source/Document Revision、AI Agent、Skill、ACL 与 retrieval policy，再固化不可变 Input Snapshot；首条消息与 Conversation 原子创建，空白 New Chat 不落库，同一 idempotency key 只接受完全相同的权威输入。

Generation worker 只消费冻结事实，不在执行时重新解析 current Revision。冻结的 Agent system instruction、Skill template、tool allowlist、output schema 与治理 digest 实际进入 ModelGateway 验证、传输元数据和审计；Source 晋级或禁用只影响未来 Turn。完成事务保存绑定相同 Project、Turn、trace、冻结资源与 content hash 的 Citation/Graph/Answer Evidence，并写 Artifact Link；请求、状态、Audit、Outbox 与终态保持事务一致。

Worker 使用有界 lease、stale reclaim、attempt 与 token fencing。用户可取消 running Turn，旧 worker 在 lease 被回收后不能再写增量或终态；retry 创建新的处理 attempt，不覆盖已完成事件。SSE 输出完整 `ChatEventEnvelope`，以 Conversation 级单调 `stream_sequence` 支持 `Last-Event-ID + 1`、断线恢复和有界实时等待，HTTP 与生成合同统一使用 Problem Details。

## 迁移兼容

Task 7A 的 `0011` 与 Task 8 初始 `0012` 保持原提交字节不变。新增线性 `0012a_conversation_governance` 兼容并收敛三种已知 stamped-0012 结构：原始 0012、早期 a22 lease 结构和 464 governance/lease/cursor 结构。迁移只补缺列与 NULL cursor，不重写已有 lease、cursor、instruction、schema 或 template；无法恢复治理内容的旧 Agent 可历史读取但不能执行。

真实 owned MySQL 测试分别按 commit-accurate schema 构造 a22 与 464，保持 Alembic stamp 为 `0012_conversations` 后升级到 head，并通过当前 `create_api_runtime`、Validation catalog seed/resolve 和 Conversation load。原始/a22/464 三条路径均通过，未来 `0013` 从 `0012a` 线性承接。

## 审查与修正

初审发现 1 项 Critical、6 项 Important：默认 worker 丢失选择、检索事实未解析、旧 Conversation 不可读、claim 不可恢复、幂等输入不足、Citation/Graph 未绑定，以及 SSE 运行时/合同不一致。后续复审继续定位 Agent/Skill 未治理真实调用、冻结 Revision 的 current-state TOCTOU、取消与旧 worker fencing、legacy sequence 冲突、默认 v2/v1 policy 错配和已应用迁移被改写。

五轮修正依次关闭运行链路、冻结事实、lease/evidence/SSE、默认 v2 与 Gateway authority、线性兼容迁移，以及 exact historical-schema 证明。最后一轮只修改集成测试；同一审查者确认全部原始与新增问题已解决，规格与代码质量均 Approved。

## 最终验证

| 检查                   | 实际结果                                                                                            |
| ---------------------- | --------------------------------------------------------------------------------------------------- |
| 最终完整 owned Backend | 2965 passed、137 skipped                                                                            |
| 最终完整 Web           | 20 files、320 passed                                                                                |
| Task 8 字面量与 Outbox | 字面量 22 passed；Outbox 5 passed                                                                   |
| 直接与扩展覆盖         | 核心 100 passed；扩展 117 passed、7 skipped、1 deselected                                           |
| 真实 MySQL             | lease/cancel/fencing、Evidence、legacy SSE、原始/a22/464 升级及 runtime/catalog/repository 路径通过 |
| Migration / schema     | `0012a` migration gate 通过；34 tables、schema diff 为空                                            |
| Contracts / quality    | `make contracts`、`make check`、`git diff --check` 通过                                             |
| 清理                   | 所有 owned MySQL 资源已清理；仅保留既有未跟踪 `node_modules`                                        |

完整 Backend/Web 套件在 `63f2783` 上运行；随后 `77887ce` 仅调整 0012a 的已知结构条件收敛，`1d24961` 仅修正历史结构集成测试，并重新通过三条真实升级路径、migration/schema gate、`make check` 与 diff check。验证未迁移或改写共享数据库，也未把 opt-in skip、fake provider 或本地旧 schema 计为通过证据。Task 9 继续把 Tapper 产品壳接到真实 Conversation、SSE 与 Citation API。
