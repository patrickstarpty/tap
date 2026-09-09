# V1 可信知识质量门禁评审

评审日期：2026-09-09。结论：**BLOCKED / 未通过 QUALITY-KB-01**。Task 10 已实现离线 evaluator、真实 runner、硬阈值、Project/Source 负矩阵、模型路由批准、逐调用预算/重试/超时收据与 fail-closed Make gate，但不能签发 V1 `pass`，V2 Task 11 不得开始。

## 外部输入阻塞

仓库当前没有至少 100 条可追溯人工标注的 QUALITY-KB-01 case 与合法测试语料；现有 Milvus fixture 只有 8 条 synthetic query，不能冒充人工标注集。仓库也没有 canonical approved provider/model/operation artifact、approval digest、有效期和可用凭据。逻辑 catalog 标签 `GPT-5.6 Sol` 与历史 `dashscope/qwen-plus` 路由均不构成批准的 actual provider/model 证据。

`profile-v1.json` 因此诚实记录 0 case。离线 `make quality-kb` 以 0/100、空分母和缺治理绑定非零退出；显式 `TAP_RUN_QUALITY_KB_01=1 make quality-kb-real` 在 provider I/O 前因缺批准 routes/artifact 退出。没有调用真实 provider，也没有生成 pass Review。

## 已完成实现

- 人工标签 dataset 与 runtime observations 分离，dataset digest 覆盖授权、conflict、abstain、anchor 和 Claim–Citation support labels。
- 四类 case 具有闭合语义、分布与唯一性约束；leakage、anchor、precision、recall 和 abstain 的空分母均 fail closed。
- real runner 逐 case 经过 production Knowledge/ModelGateway path，从实际 scope、ACL、Citation resolver、response 与 audit 生成证据。
- canonical approval v2 对 embed/chat/structured 的 alias、provider、model、operation、scope、digest 与有界 expiry 逐路由校验；production config 不匹配时 zero provider I/O。
- LiteLLM quality runtime 禁止内部隐藏重试；runner 在每次 HTTP attempt 前执行原子预算与 expiry 检查，并记录成功/失败、时长、重试因果与 actual identity。V1 gate 禁止预置 cache 并要求 zero cache hit。

## 实现复核状态

第五轮独立复审曾发现 1 项 chronology Important：evaluator 分别验证 case 与 run 的时间格式、顺序和 expiry，却未验证两者嵌套。后续恢复修正以稳定 RED 证明整体移动 run 时间仍会伪通过，再要求每个实际 observation 满足 `run.start <= case.start <= case.finish <= run.finish`；缺 observation 仍计为 skipped，不伪造 execution。独立复核最终确认 Task 10 implementation 为 Critical 0、Important 0、Minor 0。

## 验证证据

| 检查                                              | 实际结果                                                                                        |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| 最终 quality focused                              | 49 passed                                                                                       |
| LiteLLM contract                                  | 41 passed                                                                                       |
| 最近相关 runtime/composition/Conversation/quality | 234 passed；3 条既有 Alembic warnings                                                           |
| 最近完整 Backend                                  | 2992 passed、140 skipped；后续一次环境受扰运行有 59 项 supervisor/live-service 失败，未计为通过 |
| Web                                               | 354 passed                                                                                      |
| Milvus / isolated E2E                             | 19 passed / 28 passed、2 条既有 warnings                                                        |
| `make check` / diff                               | passed                                                                                          |
| Gate                                              | offline exit 1；real exit 2 before provider I/O；0/100 labeled cases                            |

只有在补齐人工数据、合法语料、批准 artifact/route/凭据，并真实运行全部 case 通过硬阈值与 zero-skip 后，才可将本结论改为 `pass`。任何阈值下调、synthetic 数据冒充人工标注、逻辑模型标签冒充实际身份或 skip 都不能解除门禁。
