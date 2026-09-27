# 可信知识、Graph 与测试设计质量重新验收

评审日期：2026-09-26。结论：**真实验收 NOT RUN；新增可信知识、V2 与 V3 均 PENDING，未关闭重新打开的 Gate**。

本轮只完成可重复的确定性 evaluator、anti-forgery 负矩阵与真实 Gate 的 fail-closed 入口。执行环境未提供真实数据集授权、真实模型执行授权或具名独立 reviewer 授权；`TAP_RUN_QUALITY_KB_01`、`TAP_RUN_QUALITY_GRAPH_01`、`TAP_RUN_QUALITY_TEST_01` 及新增授权变量均未设置。因此没有调用外部或付费模型，没有生成本次候选、request ID、output/digest、reviewer、评审摘要或 PASS。

## Gate 状态

| Gate | 2026-09-26 状态 | 本轮事实与待补输入 |
| --- | --- | --- |
| 历史 V1 `QUALITY-KB-01` | 历史 PASS 保留；本轮未重跑 | 2026-09-09/13 的旧结论不被新指标覆盖，也不作为本轮新增可信知识能力的通过证据。 |
| 新增可信知识 | **NOT RUN / PENDING** | 需要获授权的至少 100 份真实文件、至少 200 问、按文件隔离的调参与验收集、重点启用类型各至少 20 问、真实模型输出和逐例具名人审。OCR、图像、Excel 不在本批。 |
| V2 Graph | **NOT RUN / PENDING** | 需要当前真实多 Revision 候选、每次调用的唯一真实 request ID 和完整输出、当前数据集/配置/模型/output digest、`reviewedOutputDigest`，并绑定 contract、API、问答、浏览器和重启五类真实执行证据。 |
| V3 Test Design | **NOT RUN / PENDING** | 需要当前 runner 对全部至少 50 个业务意图重新调用真实模型，保存完整输出，随后逐例由具名 reviewer 复核；不得复用旧 digest 或旧批准。Task 7 的浏览器闭环实现证据保留，但不替代本次真实模型质量复核。 |

## 已实现的确定性防伪契约

- 每个真实候选必须绑定完整 dataset/config/model/output digest、唯一 provider request ID、provider/model receipt 与 receipt digest、完整原始 output、当前 run 的具名人工批准、`reviewedOutputDigest` 与完整 candidate digest。request ID、receipt、评分结果或具名判断发生变更都会使批准失效；run ID 从整批 candidate digest 重新计算。缺一项即拒绝。
- 预填 reviewer、旧 run reviewer、旧 output digest、跳过已有 case、缺原始 output、重复 request ID、修改 output 后保留旧批准、`reviewedOutputDigest` 不匹配，以及 dataset/config/model digest 缺失或漂移均有负向测试。
- `skipped`、`simulated`、`prebuilt` 或非完整真实执行不能进入 PASS。候选生成与人工验收拆成两个命令；重新生成会清空旧 reviewer 和旧批准。
- Graph evaluator 要求每个候选精确绑定两个 source/document Revision，且每个 Revision 都有对应 evidence/chunk；contract、API、answer、browser、restart 使用各自不可互换的 pytest/Playwright suite schema，保存命令、producer/binary digest、起止时间、零 skip/failure 结果、原始输出及 producer receipt，由 evaluator 重算 receipt/artifact digest，并绑定同一 run、candidate、request 与 dataset/config/model digest。官方真实 Gate 还要求非空的 journey producer 授权。
- Test Design 继续分别执行 Schema/BDD 100%、无来源事实 0、关键需求覆盖至少 90%、无 Critical Correction 草稿至少 80% 的既有门槛。
- 知识报告将 V1 的泄漏、anchor、Claim–Citation precision、recall@10、abstain 指标与新增 Recall@50、前 10 证据覆盖、正确且充分回答、引用定位、无答案/冲突提示和关键事实错误分别呈现；任一旧指标失败仍使整体失败。

## 真实执行前置条件

真实命令除各自 opt-in 外，必须提供带非空标识的 `approved:` 数据集、模型执行和 reviewer 授权，并指向已存在的外部候选/观察文件。Graph 候选还要求显式 MySQL、Milvus 和模型抽取模式。缺授权或文件时 Make target 在任何 runner/provider I/O 前退出。Graph/Test Design 候选命令只产出 `awaiting_review`，真实 Gate 命令只评估已经由外部具名 reviewer 绑定到当前 run/output/评分结果的观察文件。

新增可信知识目前没有获授权的真实 100 文件/200 验收问候选 producer。为避免把手工拼装或 prebuilt JSON 当成真实运行，官方 `quality-kb-trusted-real` target 即使授权变量和文件均存在也会以 `PENDING` 退出，不会调用 evaluator 或产生 PASS。仓库只保留确定性 schema、阈值、分集和 anti-forgery synthetic contract；必须先另行实现并批准真实 producer，才能启用该 Gate。V1 指标只从完整 `quality-kb-report-v2` 的 `metrics` 派生；该 report 必须是当前 V1 evaluator 生成的 PASS，绑定 dataset/config/governance、真实 provider execution 和 approved routes，包含至少 100 个 evaluated case、零 skip/failure 与不可变 report digest，不能由新指标中的自报计数替代。

后续只有在上述真实输入与授权齐备、三类真实命令完成且无 skip 后，才能新增 Review 判定是否关闭新增可信知识、V2 或 V3 Gate。当前 [V2/V3 更正评审](2026-09-14-v2-v3-gate-correction.md)继续有效，V4 仍不放行。
