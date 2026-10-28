# Agent 团队视图业务验证交接

创建日期：2026-10-27。接续 [Agent 团队视图交接](2026-10-25-agent-team-view-handoff.md)，归属[产品路线图](../superpowers/plans/2026-10-01-product-roadmap.md) V2.1。口径以[Agent 团队视图设计](../superpowers/specs/2026-10-22-agent-team-view-design.md)为准。

## 1. 状态

- 业务验证（spec 7.4）尚未完成。演示环境已装入验证数据（2026-10-28 按香港语料重建，见第 2 节）：当天的真实模型运行，加上过去两周的仿真历史。仿真数据不能替代"真实使用一周"，路线图 V2.1 行保持"待业务验证"。
- 按业务复核结论调整的口径已合入本地 `develop`（合并提交 `206729da`），**尚未推送远端**。
- `make demo-dev` 当前未运行。在 `.worktrees/develop` 执行 `make demo-dev` 即可，运行的就是 `206729da`。
- 2026-10-28 晚：真实 Agent 名册与 Skills 名册设计的接手说明见 [Agents 名册与 Skills 名册交接](2026-10-28-agents-skills-handoff.md)。
- 2026-10-28 完成一轮实现审查、缺陷修复与 Agents 页界面重做，并用真实模型跑通完整业务流程；过程、截图与修复清单见 [Agent 团队视图业务流程演示](2026-10-28-agent-team-business-demo.md)。演示新增了 Agent `理赔指引助手`（`agent-698bbbd066a44725`，负责人已转交 `chen.jing`）与 2026-10-28 的 2 轮真实运行，不在第 2 节的验证数据清单内；核对 10-18 至 10-24 周期时不受影响。

## 2. 验证数据（2026-10-28 重建）

2026-10-28 按产品负责人决定全量重建：删除 8 份 AIA 公开条款 PDF、全部对话历史、10-25 的仿真 trace 与模拟用户，知识库只保留香港公开产品小册子 8 份、理赔表 2 份与 3 份合成流程文档（理赔、保全、测试环境），然后用香港语料的问题重新生成验证数据。两类数据都只在本地演示环境（`tapper-demo` 项目、Langfuse 环境 `local`）中，脚本与清单不入库，存放在本机 `~/tap-synthetic-2026-10-28/`（10-25 的目录保留作历史）。

| 类别 | 时间 | 内容 | 是否经过模型 |
| --- | --- | --- | --- |
| 真实运行 | 2026-10-28 | 经演示 API 发起的 178 轮（119 个会话，59 轮为追问）：完成 131、拒答 43、取消 3、失败 1；评分 47 条（赞 40、踩 7） | 是（qwen-plus） |
| 仿真历史 | 2026-10-11 至 2026-10-24 | 397 次运行：完成 321、拒答 54、失败 10、取消 12；评分 78 条（赞 62、踩 16），其中 6 条踩已处理 | 否 |

真实运行：

- 题库 67 道首问（9 道应拒答）加 20 道追问，简体、繁體与英文混合，按 Agent 定位分配：Tapper 默认 50、Knowledge agent 35、Plan Comparison Assistant 34、Web Test Design Assistant 29、Claims FAQ Helper 30。
- 维护动作均通过接口完成：两个自定义 Agent 设置岗位；Plan Comparison Assistant 修订一次（`agent-rev-3e2ab4a82b91407d`）；处理踩 4 条；Web Test Design Assistant 与 Claims FAQ Helper 转交给 `chen.jing`。
- 调用者全部是 `tapper-local-user`：演示身份在代码中固定。

仿真历史：

- Langfuse：每次运行克隆一条同 Agent 的当日真实 trace，只改时间与调用者；失败克隆自当日唯一一条真实失败 trace。全部带 `synthetic:2026-10-28` 标签。
- 演示库：补写会话、Turn、输入快照（指向当时存在的 Agent 修订），部分完成的 Turn 带评分；处理过的踩写 `feedback_resolved` 维护事件（Tapper 默认不写事件，故 6 条已处理只产生 3 条事件）。主键以 `syn` 开头，维护事件以 `mevt-syn` 开头。
- 8 个模拟用户写入 `actor_principal`：`li.na`、`wang.lei`、`zhang.min`、`chen.jing`、`liu.yang`、`zhao.qian`、`sun.hao`、`zhou.ting`。
- 自定义 Agent 创建时间被提前：Web Test Design Assistant 改为 2026-10-08，Plan Comparison Assistant 改为 2026-10-14；Claims FAQ Helper 保持 2026-10-28。原值备份在 `backup-ai_agent-dates.tsv` 与 `backup-ai_agent_revision-dates.tsv`。
- 自定义 Agent 的名称与岗位已直接在库中改为英文（演示默认语言为英文），原中文名备份在 `rename/backup-names-before.tsv`。

演示流程另外新增的数据见[业务流程演示](2026-10-28-agent-team-business-demo.md)第 5 节。

## 3. 清理与恢复

在仓库根目录加载 `.env` 后，于 `~/tap-synthetic-2026-10-28/` 执行（顺序不限，可加 `--dry-run` 先看 SQL）：

```sh
python3 -I cleanup.py synthetic   # 删除仿真 trace 与 syn 前缀行，恢复两个 Agent 的创建时间与负责人，删除 8 个模拟用户
python3 -I cleanup.py real        # 经接口删除真实运行的会话及其 trace、评分，撤销当天 7 条维护事件、岗位、负责人与新修订
```

- Langfuse 删除是异步的，ClickHouse 完成删除前查询仍能看到旧数据。
- 经接口删除会话是软删除（写 `deleted_at`），行仍在表中。
- 需要恢复中文名时按 `rename/README.txt` 回写。

## 4. 核对方法与结果

`verify.py` 逐项比对团队视图接口与投入的数据：

- 两周历史：预期值 = 装入仿真数据前保存的基线报表 + 仿真清单的合计，覆盖运行、失败、拒答、取消、耗时、成本、模型与工具调用、服务人数。
- 真实运行：预期值来自运行清单与演示库的 `trace_span`。
- 满意度：在演示库中按 Turn 冻结的 Agent 归属独立重算。
- 人员表：每人调用次数。

2026-10-28 在重建后的数据上 252 项全部一致（`verify-result.txt`），覆盖 10-11 至 10-17、10-18 至 10-24 与当天。

## 5. 2026-10-27 口径调整

业务复核后已改，spec 已同步：

| 问题 | 结论 |
| --- | --- |
| 状态与周期无关 | 花名册保留近 30 天状态；汇报页花名册改为"本期运行／本期未运行"，已停用仍显示"已停用" |
| 内置 Agent 与 Tapper 默认无负责人 | 规则不变，界面显示"平台维护"，接口中负责人仍为空 |
| 删除会话后评分仍计入满意度 | 保留，spec 2.3 写明理由 |
| 人员表只认演示库调用者 | 人员范围并入 Langfuse 中本期的调用者；Langfuse 不可用时只用 MySQL |
| 人均指标分母为全部人员 | 改为"每位负责人管理的 Agent 数"（分母为负责人）与"每位使用者用到的 Agent 数"（分母为本期调用者）；接口字段名 `ownersAvg`、`servedAvg` 不变 |

## 6. 过程中发现的问题

- 演示 API 的写请求需要 `Origin: http://127.0.0.1:5173`，否则返回 403。
- 对话不带 `sourceRevisionIds` 时，Tapper 直接回复"请选择有权限的项目来源"，不调用模型；单次最多 20 个来源，超出返回 409。
- ClickHouse 诊断日志表 `system.trace_log`、`system.text_log`、`system.metric_log` 曾累计约 21 GB 并占满 Docker 虚拟机磁盘；`metric_log` 合并时超出容器 2 GB 内存，阻塞了 Langfuse 的删除任务。清空方法见上一份交接文档第 4 节。
- 由 Claude Code 后台启动的 `make demo-dev` 最长运行 2 小时会被终止，长期运行请在自己的终端启动。
- `develop` 上已有 37 个后端测试失败，与本功能无关：34 个知识库集成测试在清理阶段报外键约束错误，3 个 `test_knowledge_review.py` 用例写死了日期。

## 7. 待办

| 事项 | 负责 | 说明 |
| --- | --- | --- |
| 业务核对 | 负责人 | 在团队页与汇报页（建议周期 10-18 至 10-24，对比 10-11 至 10-17）核对数字与口径 |
| 真实使用一周 | 负责人 | spec 7.4 仍需真实使用数据；完成后把路线图 V2.1 改为完成 |
| 推送 `develop` | 负责人 | 本地领先远端，含口径调整与本文档 |
| 清理验证数据 | 负责人 | 业务核对结束后执行第 3 节命令（10-28 目录） |
| 修复已有测试失败 | 开发 | 知识库集成测试外键清理与写死日期的用例 |
| 清理演示 Agent | 负责人 | 业务核对结束后停用 `理赔指引助手` 并删除其 2 个会话（见演示文档第 5 节） |
