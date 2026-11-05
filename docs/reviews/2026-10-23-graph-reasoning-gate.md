# 图谱脉络分析真实门禁记录（2026-10-23）

## 结论

**FAIL**：关系类 golden set 2/25 通过（要求 ≥ 80%），边抽样准确率 13/50（要求 ≥ 85%），跨来源误合并 9/30（要求 ≤ 5%）；有依据率回归与性能基准通过。

- 执行日期：2026-10-23；执行人：代理（经项目负责人授权代为执行）。
- 模型：别名 `qwen-plus`，实际 `dashscope/qwen-plus`；嵌入 `text-embedding-v4`。
- `graphVersion`：53（项目图，含本次语料与演示环境原有来源）。
- `GRAPH_EXTRACTION_PROFILE_DIGEST`：`sha256:4256b1e9f33008fa8685c4f3f8476bc39baeea4a244c7e1251cac87209247d38`。
- 代码：`develop` `420d2f68` 加本次两处门禁脚本修复（见“偏差与后续”）。

## 语料

- 香港公开保险产品说明书 8 份（癌症与重疾、定期寿险、两份储蓄计划、一份终身储蓄计划、三份自愿医保计划）与公开理赔申请表 2 份（重疾、住院与意外），均为英文或中英双语、可抽取文本的 PDF。
- manifest、golden set、回归题与 `corpus.json` 只保存在被 git 忽略的 `.local/graph-real/`，不提交：语料文件名与正文含出品方名称。仓库内 `tests/fixtures/quality/graph-real/` 仍为占位。
- 上传：`scripts/load-graph-real-corpus.py upload`；演示模式下文档就绪即进入来源范围，未经 Library 人工审核。
- 抽取：demo-dev 以 `TAPPER_GRAPH_EXTRACTION_MODE=model` 重启后全部修订重抽，耗时约 3 小时；项目图 4135 节点、13115 边、51 个社区；本次语料抽出 3974 个节点、10999 条边。
- 8 个修订为部分成功：45 次抽取分批被网关脱敏检查（`_check_redacted`，`model-request-rejected`）拒绝，涉及几乎每份说明书 1–3 个分批。

## 关系 golden set

- 标注：代理标注（经项目负责人授权，未经人工复核），2026-10-23，25 题，其中 8 题 `expectedSources` 跨 2–4 份文档。期望边以文档原文为准，图谱实际标签只作为别名与等价替代。
- `summary.actual`：2/25（q-05、q-08 通过；q-21 出现错误边；其余 22 题遗漏）。
- 原因归类：
  - 抽取缺边：标注时核对 v53，25 题中 11 题的期望边在图中不存在（q-04、q-06、q-16 及全部 8 道跨文档题）。
  - 模型未引用：其余 14 题的期望边存在于图中，回答平均引用约 11 个原文片段，只有 2 题引用了期望关系边。
  - 错误边：q-21 引用了出品方实体 `RESPONSIBLE_FOR` 自愿医保的泛化边。
- 标注局限：8 道跨文档题把期望边写成两端分属不同文档的单条边；项目图每条边只来自一份文档，此类边无法出现。更合理的标法是每份文档各一条边、经合并实体相连，复验前应重标。
- 真实对话路径（`make quality-chat-relations-real`，不带 Agent，report-only）：`graph.relations` 调用 0/25，有依据 23/25，关系边命中 0/25，p50 9.2 s。
- 同一路径带自定义 Agent（系统提示词要求关系问题调用 `graph.relations`，8 道跨文档题）：调用 5/8，有依据 8/8，关系边命中 0/8，p50 18.2 s。

## 有依据率回归

| 组 | 图谱推理开启 | 基线（`TAPPER_GRAPH_REASONING=0`） | 结论 |
| --- | --- | --- | --- |
| corpus-questions-a-2026-10-23 | 9/10 | 9/10 | 通过 |
| corpus-questions-b-2026-10-23 | 9/10 | 9/10 | 通过 |

两组各 10 题，取自同一语料起草的问答题。演示环境当时显示的推荐问题仍针对旧语料，未用作回归组。

## 抽样核对

- 边 50 条：正确 13/50（26%），未通过。核对人：独立评审代理，2026-10-23。
- 跨来源合并实体 30 个：误合并 9/30（30%），未通过。
- 主要错误：
  - 关系类型错误或方向颠倒（约 18 条），例如定义句被存为 `PART_OF`、包含关系颠倒；
  - 两端是章节标题或泛化节点的空洞边（约 13 条）；
  - 原文不支持的边（约 8 条）；
  - 合并时把子类并入上位词，`EXACT` 规则把“180 days”“Standard”这类短词跨文档误合并。
- CSV：`.local/graph-real/edge-sample.csv`、`.local/graph-real/merge-sample.csv`（不提交）。

## 性能基准

`make graph-bench`（本机 Docker Desktop MySQL，种子 20261006，每类 200 次采样）：`loadMs` 853（上限 1000），邻居 / 路径 / 总览 `p95Ms` 为 5.8 / 2.9 / 5.3，`passed: true`。

## 偏差与后续

- 与 spec 4.2、4.3 阈值差距大：抽取质量（边准确率、合并）与回答中关系边的采用都远低于门槛；能力 3 工程完成不能勾选。
- PR 5 起关系分析只在模型调用 `graph.relations` 时发生，`qwen-plus` 默认从不调用；自定义 Agent 的提示词可让调用率升到 5/8，但工具结果仍未成为回答引用。
- 门禁脚本修复：`run-graph-relations-candidate.py` 真实模式此前从未跑通——`dry_run_answer` 已返回 HTTP 答案（`fix: read the http answer in graph relations real mode`），且 HTTP 答案不带模型来源，`model.actual` 恒为空（`fix: record the upstream model in graph relations real mode`）。
- 其他发现：
  - 抽取分批被脱敏检查拒绝（按 2026-10-18 决定暂不阻塞）；
  - PDF 连字未还原（`bene/f_it`），影响检索与标签；
  - 出品方名称实体成为最大社区（1819 节点）；
  - 中文问题检索英文资料时召回不足（见 V1 总纲能力 5 替代验收）。
- 复验：修复抽取与关系采用后，重标跨文档题，再按本记录同样的命令重跑。

## 命令与产物

1. `TAP_RUN_GRAPH_BENCH=1 make graph-bench` → `.local/graph-bench/report.json`（`sha256:3a241b96a798f667…`）
2. `scripts/load-graph-real-corpus.py validate|download|upload --manifest .local/graph-real/manifest.json`
3. `TAP_RUN_GRAPH_SAMPLE_EXPORT=1 make graph-sample-export`；`scripts/evaluate-graph-samples.py` → `.local/graph-real/sample-report.json`（`sha256:9ea449ca4a195846…`）
4. `TAPPER_GRAPH_REASONING=0 scripts/run-graph-relations-candidate.py --mode real …` → `.local/graph-relations/baseline-observations.json`
5. `make quality-graph-relations-real TAP_GRAPH_REAL_MANIFEST=… TAP_GRAPH_RELATIONS_GOLDEN=… TAP_GRAPH_RELATIONS_REGRESSION=…` → `.local/graph-relations/report.json`（`sha256:93166ed5113b3af1…`）
6. `make quality-chat-relations-real …` → `.local/chat-relations/report.json`（`sha256:1cfe88d6b4e9fb77…`）；带 Agent → `.local/chat-relations/agent-report.json`（`sha256:eceac278feccded6…`）

本次真实模型调用合计约 3.8 美元（抽取约 2.5 美元，对话与评测约 1.3 美元）。
