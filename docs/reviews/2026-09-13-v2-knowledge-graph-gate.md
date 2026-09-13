# V2 Grounded Knowledge Graph 门禁评审

评审日期：2026-09-13。结论：**PASS / V3 已放行**。Task 11–13 的版本化 Graph 存储、独立耐久 Graph Worker、Project-scoped API、回答增强和 WebGL 探索器均已实现并通过本地全链路验证；`QUALITY-GRAPH-01` 的 200 条判断已由 `human:patrick` 独立复核并全部批准，最终真实百炼运行满足全部硬阈值。

## 已验证实现

- `0013_knowledge_graph` 增加不可变 Snapshot/Revision、唯一 active pointer、Node/Edge Evidence、INFERRED provenance 和 extraction job；迁移保持、44-table schema drift 与真实 MySQL Adapter 集成测试通过。
- Document READY、Graph CANDIDATE、Graph job、Audit 与 Outbox 在同一 MySQL 事务创建。独立 `tapper_graph_worker` 使用专属 Redis consumer group 唤醒，以 MySQL job ledger、claim token、lease 和失效恢复作为权威状态；Graph 失败不回滚已 READY 的文档。
- Graph extraction 只通过共享 ModelGateway 调用 schema-locked structured output；Evidence 必须解析到授权 chunk/digest，INFERRED edge 必须携带输入事实与规则 provenance，候选发布后才原子切换 active pointer。
- 六类 Graph HTTP 操作保持 Project/Snapshot 边界、两跳和 500-node 上限；Knowledge Answer 只使用当前选中 revision 的有界 Graph Context，并持久化 `APPLIED | NOT_READY | FAILED | UNAVAILABLE | NOT_SELECTED` 与实际 Snapshot ID。
- Library 使用真实 Source Revision、Graph API、Sigma.js、Graphology 与独立 ForceAtlas Worker，提供搜索、Inspector、Evidence 深链、非颜色来源标识和 reduced-motion。Graph 503 时明确显示 unavailable，不伪装成空图或无限 loading。
- 隔离 E2E 覆盖真实 API、bounded graph、Evidence、WebGL UI、Graph unavailable fallback、应用重启、Compose 重启及 Graph 持久化，报告 zero skipped/flaky。

## QUALITY-GRAPH-01 正式证据

数据集包含 20 份生成的结构化 Markdown 文档与 200 条逐项人工复核的 Node/Edge/归并判断。正式运行通过本地 LiteLLM 调用百炼 `qwen-plus`，每份输出经固定 Prompt/Schema 校验并发布到隔离 MySQL Graph；最终报告固定 dataset、Prompt、Schema 与 evaluator digest。

| 指标 | 实际结果 | 要求 |
| --- | --- | --- |
| 文档数 | 20 | ≥20 |
| 判断数 | 200 | ≥200 |
| Evidence/Provenance 可解析 | 200/200 | 100% |
| EXTRACTED Edge–Evidence precision | 80/80 | 100% |
| relation precision | 100/100 | ≥90% |
| 错误实体归并 | 0/20 | ≤1% |
| INFERRED provenance 完整 | 20/20 | 100% |

`quality-graph-real` 先验证 `reviewStatus=approved` 且每条判断具有独立人类 reviewer，再进行任何 provider I/O；运行完成后验证实际非 fake 模型及当前 Prompt、Schema、evaluator digest。首次正式运行暴露一次节点类型退化，只有 196/200 条 Evidence/Provenance 可解析；根因是类型枚举缺少具体分类约束。新增契约测试并在 Schema 与 Prompt 明确 `ACTOR | SYSTEM | REQUIREMENT | CONCEPT` 优先、`ENTITY` 仅兜底后，重新创建隔离数据库并完整重跑，最终达到 200/200，未降低任何阈值。

## 当前证据

| 检查 | 实际结果 |
| --- | --- |
| Graph quality evaluator contract | 10 passed；包含错误合并、dangling Evidence、伪 EXTRACTED、未复核和陈旧 digest 负矩阵 |
| 离线候选 evaluator | passed；20 documents、200 labels，全部数值阈值满足 |
| 正式真实百炼门禁 | passed；20/20 文档、200/200 判断，实际模型 `dashscope/qwen-plus`，真实 MySQL Graph 发布成功；reviewer `human:patrick` |
| MySQL Graph | migration-check passed；schema drift passed，44 tables；lease recovery 与原子完成集成测试 passed |
| Web Graph 与请求审计 | 364 passed；Graph unavailable 状态机定向测试 passed |
| isolated E2E | passed；28 persistence checks，zero skipped/flaky；应用与 Compose 重启后 Graph Snapshot/Evidence 可读 |
| `make check` / `make test` | passed；Backend 3075 passed / 143 environment-skipped；Web 364 passed |

V2 的实现、人工复核、真实质量门禁、完整回归和隔离 E2E 均已通过；V3 可开始。
