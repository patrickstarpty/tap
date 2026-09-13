# V1 可信知识质量门禁评审

评审日期：2026-09-09；复核日期：2026-09-13。结论：**PASS / QUALITY-KB-01 通过**。Task 10 的离线 evaluator、真实 runner、硬阈值、Project/Source 负矩阵、模型路由批准、逐调用预算/重试/超时收据与 fail-closed Make gate 均已通过，V1 出口解除阻塞，可进入 V2 Task 11。

## 历史阻塞与解除

2026-09-09 首轮评审因缺少至少 100 条可追溯人工标注 case、合法测试语料、批准 artifact/route 与可用凭据而保持 `BLOCKED`；当时 8 条 Milvus synthetic query 未被冒充为人工标注集，逻辑 catalog 标签也未被冒充为 actual model 证据。

2026-09-13 已补齐 100 条人工复核 case、11 份合成测试语料和有效期至 2026-09-16T14:17:29Z 的批准映射；真实运行使用 `dashscope/text-embedding-v4` 与 `dashscope/qwen-plus`，逐调用实际身份与批准映射完全一致。语料、profile、observations、批准 artifact 与报告保留在忽略的本地质量工作区，不提交凭据或运行正文；Review 只记录不可逆摘要与指标。

## 已完成实现

- 人工标签 dataset 与 runtime observations 分离，dataset digest 覆盖授权、conflict、abstain、anchor 和 Claim–Citation support labels。
- 四类 case 具有闭合语义、分布与唯一性约束；leakage、anchor、precision、recall 和 abstain 的空分母均 fail closed。
- real runner 逐 case 经过 production Knowledge/ModelGateway path，从实际 scope、ACL、Citation resolver、response 与 audit 生成证据。
- canonical approval v2 对 embed/chat/structured 的 alias、provider、model、operation、scope、digest 与有界 expiry 逐路由校验；production config 不匹配时 zero provider I/O。
- LiteLLM quality runtime 禁止内部隐藏重试；runner 在每次 HTTP attempt 前执行原子预算与 expiry 检查，并记录成功/失败、时长、重试因果与 actual identity。V1 gate 禁止预置 cache 并要求 zero cache hit。

## 实现复核状态

第五轮独立复审曾发现 1 项 chronology Important：evaluator 分别验证 case 与 run 的时间格式、顺序和 expiry，却未验证两者嵌套。后续恢复修正以稳定 RED 证明整体移动 run 时间仍会伪通过，再要求每个实际 observation 满足 `run.start <= case.start <= case.finish <= run.finish`；缺 observation 仍计为 skipped，不伪造 execution。独立复核最终确认 Task 10 implementation 为 Critical 0、Important 0、Minor 0。

## 通过证据

- dataset `sha256:f55cb7a19fe8be4d54069258520668ac5d32ad24a003df7738f638ec16047269`
- config `sha256:c076f5f516a3364441dd8cbc715b077799059601ba1807021cf1158ca97e7e7e`；evaluator `sha256:8798870694ade8957e3c3f442c549d89f816bdb656e3b2c922bb10ccb1b64a09`
- approval `sha256:bc68c03cf603f97c805fc3b95749d2a9866a812d7a1642d579bc146f5bab243c`；prompt `sha256:e11c1bc55ceb764433dc2215c4f9c5f33e2127576a1140a770c74f8960024437`
- 真实观察窗口：2026-09-13T07:01:28.753092Z 至 2026-09-13T07:03:34.525769Z；130 次 provider call，0 retry，0 cache hit，预算 500。

| 检查                                              | 实际结果                                                                                        |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| QUALITY-KB-01 real                                | exit 0；100 cases、0 skipped、0 leakage                                                         |
| Anchor / grounded precision                       | 750/750；30/30                                                                                  |
| Retrieval recall@10 / abstain accuracy            | 69/70；70/70                                                                                    |
| Project / Source negative matrix                  | 12 / 13                                                                                         |
| Offline replay (`make quality-kb`)                | exit 0；同一 100-case observations 可重复评估                                                   |
| Backend / Web                                     | 3036 passed、139 skipped / 354 passed                                                           |
| Milvus / isolated E2E                             | 19 passed / 28 passed、2 条既有 Alembic warnings                                                |
| `make check` / `git diff --check`                 | passed                                                                                          |

所有硬阈值均未下调；真实运行没有 skip、缓存命中或越权泄漏。该结论只放行 V1 → V2，不构成企业 Azure 四索引、生产安全或客户数据验证声明。
