# V3 AI 测试设计门禁评审

评审日期：2026-09-14。结论：**PASS / V4 已放行**。Task 15–17 的不可变 Test Plan Revision、确定性发布门禁、独立 Test Design Worker、grounded Draft、Web Review/发布与 `QUALITY-TEST-01` 均已完成；50 个业务意图由 `patrick` 具名复核，真实百炼运行满足全部硬阈值。

## 已验证实现

- `0014_test_management` 保存 Project-scoped Test Plan、不可变 Revision、Case/Scenario/BDD Step、Citation、Assumption、Unknown、Coverage Gap 与 generation job；Published/Superseded Revision 不可编辑，修改 Published 内容必须 fork 新 Draft。
- Test Design Worker 同时冻结 Conversation Input Snapshot 与 Answer/Evidence Snapshot digest，只通过共享 ModelGateway 的固定 Prompt/Schema 生成 Draft；模型不能发布或改变工作流状态。
- 发布前确定性校验 BDD 顺序、Then expected result、稳定身份、当前授权 Citation、Assumption/Unknown/Coverage Gap 与 optimistic version；并发编辑稳定返回 `revision-conflict`。
- Web Test Management 使用真实 API 完成列表、详情、Review、冲突和人工发布；Tapper Artifact Link 可深链到对应 Draft/Revision。
- 隔离 E2E 的闭集 journey 已包含 `tapper-test-plan.spec.ts`，并覆盖跨 Source/Project 拒绝、Test Plan publish、应用重启、Compose 重启和持久化验证，zero retry/flaky/skip。

## QUALITY-TEST-01 正式证据

候选集固定 50 个业务意图、来源事实和 100 条关键需求，每例均由具名 Reviewer `patrick` 批准。正式运行通过隔离 LiteLLM 调用百炼 `qwen-plus`，并固定 dataset/observation、Prompt、Schema 与 evaluator digest。

| 指标                         | 实际结果  | 要求                        |
| ---------------------------- | --------- | --------------------------- |
| 业务意图                     | 50        | ≥50                         |
| Schema 与 BDD                | 50/50     | 100%                        |
| 无来源事实                   | 0         | 0                           |
| 关键需求覆盖                 | 100/100   | ≥90%                        |
| 无 Critical Correction Draft | 50/50     | ≥80%                        |
| 具名 Reviewer                | `patrick` | 每例至少一名已批准 Reviewer |

正式报告记录实际模型 `dashscope/dashscope/qwen-plus`，dataset/observation digest 为 `sha256:5d44f21148022d2039e10063bbf12dd235e0b7e7464ecbb55a148aed5d512e72`，Prompt digest 为 `sha256:37001c112a5f9ca449a907dcea76b770203eb9f98ef91aec462f02b1a113fbfe`，Schema digest 为 `sha256:4ef670689e4c1e6e970ba9de97298895e0738f373a0f3f186b16a3cebadbce27`，evaluator digest 为 `sha256:bc031aa6b59cb180c29f10c505f4a705dcd43bd234bd8653c22ab5ad00a4430c`。

首次全量真实运行沿用普通 Demo 的 15 秒模型截止时间，仅 3/50 请求完成；单例诊断在 60 秒预算下 18.5 秒成功，确认失败源于质量运行预算而非 Schema 或模型路由。`quality-test-design-real` 随后固定使用允许上限 60 秒，完整重跑达到 50/50；没有降低质量阈值、跳过 case 或复用旧观察。

## 当前证据

| 检查                              | 实际结果                                                                                                        |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Test Design quality contract      | 10 passed；包含 Schema/BDD、无来源事实、覆盖、Critical Correction、Reviewer 和可重建 profile 负矩阵             |
| 离线候选 evaluator                | passed；50 intents、100/100 requirements、全部硬阈值满足                                                        |
| 正式真实百炼门禁                  | passed；50/50 Schema/BDD、100/100 覆盖、0 unsupported facts、实际模型 `dashscope/dashscope/qwen-plus`；reviewer `patrick` |
| isolated E2E                      | passed；28 persistence checks，包含 Test Plan publish，zero retry/flaky/skip；应用与 Compose 重启后状态可读     |
| `make test`                       | passed；Backend 3102 passed / 150 environment-skipped；Web 29 files / 366 passed                                |
| `make check` / `git diff --check` | passed                                                                                                          |

V3 的实现、具名复核、真实质量门禁、完整回归和隔离 E2E 均已通过；V4 Task 18 可开始。该结论不代表 Web Recorder、正式 Playwright Bundle、Jenkins 闭环、产品身份/RBAC 或生产加固已经完成。
