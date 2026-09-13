# V2/V3 门禁更正评审

评审日期：2026-09-14。结论：**V2、V3 Gate 重新打开；V4 暂不放行**。

本评审是对 [V2 Gate Review](2026-09-13-v2-knowledge-graph-gate.md) 和 [V3 Gate Review](2026-09-14-v3-ai-test-design-gate.md) 的后续独立审查。两份原评审保留为历史记录，但其中的 `PASS` 与下一阶段放行结论自本评审起失效。已经实现并通过测试的能力仍然有效；不得把“门禁重新打开”误读为代码全部不可用，也不得把已有代码量误读为里程碑出口已满足。

## 重新打开原因

### V2 Knowledge Graph

- Graph extraction job 与 active snapshot 当前按单个 Document Revision 建立，而回答和 Web 查询允许携带多个 Revision；多文档选择时无法证明 Graph Snapshot 与完整选择集一致。
- 隔离 E2E 将 Graph 查询收窄到单个文档，没有覆盖多 Revision 输入、Snapshot 一致性和跨文档 Evidence 回溯。因此原 `V2 PASS` 证据不足。

### V3 AI Test Design

- 原质量 profile 生成器预填 `pass` 指标和具名 reviewer，候选运行器可跳过已有 digest 的 case，且没有保存供人审的完整模型输出；原 `QUALITY-TEST-01` 报告不能证明 50 个本次候选逐例经过真实调用和人工复核。
- 原候选 evaluator 只检查汇总字段，不能把 reviewer 判断绑定到当前输出 digest。修正后，生成 profile 默认为 `pending`，每次运行必须调用全部 case、保存 `generatedOutput`，且只有 `reviewedOutputDigest` 与当前输出一致时才接受具名批准。
- Test Plan Web 已有列表、详情和发布入口，但尚未完成编辑、并发冲突后的 reload，以及 generation job 的轮询、失败反馈和生成结果深链接；原 E2E 的直接 HTTP 发布不能替代这些 UI 旅程。
- 原发布校验允许把 Citation 的各字段从不同 Evidence 行拼接，并允许 generation 请求改变冻结 Input Snapshot 中的 model/Agent/Skill；这些路径已经收紧并增加负向测试。

## 已立即修正

- `/knowledge/sources` 与旧上传入口共同使用有界 multipart 读取，避免路由改名后绕过上传大小保护。
- Test Design 候选运行不再复用旧 digest 或预造通过结论；正式 evaluator 会重建并校验完整输出，要求每例唯一 Provider request ID，并把人工复核同时绑定到 intent、source、关键要求、request digest 与 output digest。
- Citation 必须精确匹配同一条授权 Evidence；生成参数必须等于冻结 Input Snapshot；发布时 Citation 的 Source/Document Revision、chunk 与 digest 必须属于当前授权 Revision。
- 持久 API 模式不再显示只存在于页面内存的浮动助手原型回复；Test Plan Review 现在展示 Citation、Assumption、Unknown 和 Coverage Gap 明细，而不只显示计数。

## 重新关闭门禁的必要证据

| 门禁 | 必须补齐的证据 |
| --- | --- |
| V2 | 明确并实现多 Revision Graph Snapshot 契约；增加至少两个 Document Revision 的 API、回答、Web 与重启 E2E；重新执行并独立核验 `QUALITY-GRAPH-01` 的候选输出与 reviewer 绑定。 |
| V3 | 用当前 runner 对全部 50 case 产生并保存新输出；人工逐例复核并绑定 `reviewedOutputDigest`；真实模型门禁重新通过；完成生成 job 成功/失败/深链接、编辑和 `revision-conflict` reload 的浏览器旅程。 |

在上述证据进入新的 Gate Review 前，文档、Issue、PR 和演示不得宣称 V2/V3 `gate-passed`、`QUALITY-TEST-01 passed` 或 V4 已放行。V0/V1 状态不受本次更正影响。
