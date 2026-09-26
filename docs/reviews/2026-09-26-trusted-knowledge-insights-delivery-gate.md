# 可信知识、测试设计与基础 Insights 联合交付门禁

评审日期：2026-09-26；最终复验日期：2026-09-27。结论：**隔离结构验收 PASS；M1、M2、M3、M4 业务交付均 PENDING，计划保持 `active`，不宣称生产就绪。**

本评审汇总 [Task 14 计划](../plans/2026-09-23-trusted-knowledge-test-design-insights.md#task-14联合验收保留式发布与交付记录) 的联合 runner、保留式升级/恢复和产品原型证据。验收使用仓库内合成脱敏 fixture、回环服务和每次运行独立的 Compose 资源；没有外部真实 CI、真实业务资料、真实模型、生产身份、签名硬件或具名业务/运维/安全签字。因而下列 PASS 只证明结构、故障恢复和产品交互证据，不关闭真实业务 Gate。

## 联合验收结果

最终 runner 代码 SHA 为 `03ca16e446e3d9b6faa8172a19e69f9a33d9dc84`，入口为 [`run-task14-acceptance.sh`](../../scripts/run-task14-acceptance.sh)。runner 每次生成独立项目名与随机临时凭据，凭据不打印并在日志管道中脱敏；启动前拒绝复用同标签资源，只有 preflight 后写入的本次随机所有权标记才允许退出清理，退出只停止本次运行拥有的容器/网络并保留具名数据卷和备份卷，不执行 `down -v`。执行收据采用 `task14-acceptance-v2`，其最终摘要如下；本地原始日志与收据为忽略产物，不作为稳定链接。

| 阶段 | 结果 | 证据与边界 |
| --- | --- | --- |
| Tapper 三旅程 | `PASS`（530 秒） | 6 个浏览器规格、应用重启、Compose 重启、29 个持久化断言；涵盖资料核对/发布/问答引用和需求→草稿→编辑→独立评审→发布。使用合成 fixture 与本地 LiteLLM stub，不是本轮真实模型质量 Gate。 |
| Insights 三旅程 | `PASS`（39 秒） | 同一真实 JUnit 依次通过上传/查询/钻取、应用重启、MySQL/ClickHouse 重启，3 个 Playwright 阶段均通过；来源仍是仓库 fixture，不是获授权外部 CI。 |
| TAP AI 故障/保留矩阵 | `61 passed, 3 skipped`（1007 秒） | API/Worker/Redis 中断、租约/重复唤醒/响应未知、撤回/过期、跨项目拒绝、Graph/Test Plan/发布恢复及旧 `0005` 数据升级至 `0022`；3 个 skip 是既有可选恢复变体，不计作真实 Gate。 |
| TAP Insights 故障/恢复矩阵 | `34 passed`（61 秒） | 重复/冲突/乱序/更正、Worker 恢复、投影水位与重建、ClickHouse 独立备份恢复；只删除标签验证后的本次运行数据卷，并从单独保留的备份卷恢复。 |
| 原型与安全交接 | `60 + 9 passed`（10 秒） | TAP AI Insights Tool/HTTP 交接 60 项，完整原型与详情 9 项；无 URL 数值信任、无写权限扩大。 |

联合 runner 最终收据 SHA-256：`983caf28f62cb3c1db879e219778508d63979c4bbb84b03358c8e46f90eb2cda`。阶段日志 SHA-256 由收据逐项记录；保留卷名称带随机运行项目标签，未在文档固化为可复用资源名。收尾检查确认本次两个项目均无残留容器或网络，各自只保留 5 个带正确 Compose label 的数据/备份卷。

## 产品基准对照

截图使用同一 `/prototype` fixture、1280×720 布局视口和 2× 像素密度。基准为 [Task 14 前的 Test Insights](../assets/prototype-current/test-insights.png)，最终证据为 [Task 14 后的 Test Insights](../assets/task-14/2026-09-26-test-insights-after.png)。两张图片均为 2560×1440 PNG，SHA-256 均为 `e01a6d36f0ec4d129173f60947e67409af5e4506cf204b32e471f27e67e15fe7`；稳定采集后的像素完全一致。

人工对照确认统一壳、Test Insights 主入口、项目/构建筛选、时间范围、指标卡和可见图表没有被 Task 14 破坏。自动化回归逐项覆盖 Tapper、Agents、Skills、Library/Knowledge Graph、Test Management、Test Analytics、Low Code Automation 的可达性，以及 Tapper→Test Plan→Automation 双向关联和悬浮助手→Tapper 会话/草稿交接。截图采集先等待 `Test Analytics` 成为 `aria-current=page`，并禁用有限动画，避免把侧栏过渡帧当成产品差异。

## 保留式发布清单

| 项目 | 已核事实 | 待验证/限制 |
| --- | --- | --- |
| 代码 | runner SHA `03ca16e446e3d9b6faa8172a19e69f9a33d9dc84`；Task 14 变更从 `cbe3373a396ee94c73944c42eb2e5400ac70385b` 开始 | 最终文档/复审提交 SHA 在合并记录中追踪；未部署到生产。 |
| TAP AI 镜像与解析 | Parser 本地镜像 digest `sha256:4ef38e54f7bbc87ee5735132be927db1352bf27ca5cf772e5641f9d09887cb27`；运行收据 digest `sha256:ba4eb93b9afde7d57c6cb98f50786791e20b1eb764ef5a3f1c96bdfad1947d75`；`tapper-parser-v1`、`doc-schema-v2` | 没有生产镜像签名、SBOM/漏洞 Gate 或目标硬件签字。 |
| TAP/ClickHouse 镜像 | `clickhouse/clickhouse-server:25.8.33.6-alpine`，已验证 registry digest `sha256:87e0a5b72f5465b18eacca7c76850e7ff551c9795c50e451f5646299e5e24146` | 没有生产镜像签名、容量/保留策略和运维签字。 |
| 契约 | TAP OpenAPI `04e2226e15987d75e55bc6dbc753fab5f102dfaa25410dd46041442b8a67a87e`；TAP AI OpenAPI `197ce546b4a451b86533e7453530f68dfc01abeb01d6f7b960e43c66c8215e1b`；生成 Web/TAP AI schema 分别为 `d584feaf…e9a7`、`04487b01…9bef` | 仅仓库生成/漂移检查；外部消费者兼容签字待补。 |
| 迁移 | TAP AI 旧 `0005_projection_lineage` 数据追加升级至 `0022_test_design_review` 并可由当前 repository 读取；TAP 为 `base → 0004_insights_queries`。回退只撤可见指针/使用兼容旧应用，保留新账本，不自动降库或清卷。 | 生产数据抽样、窗口和回退演练待运维批准。 |
| 模型/图/指标配置 | `fast-chat-v1`、`test-design-generation-v1`，state schema `1`；Insights `junit-v1` 与 `insights-metrics-v1` | 真实模型、provider request/receipt、输出 digest 和具名 reviewer 均待本轮真实 Gate。 |
| 冻结输入 | `trusted-checkout-v1.json` `2206bc7f…f436`；`task0-inputs-v1.json` `17f9b022…01b`；`metrics-oracle-v1.json` `9878df03…398`；旅程 `retry.xml` `661f9d25…804e` | 均为 `synthetic-development-fixture` / `pending-real-input`，不能替代真实业务资料或外部 CI。 |
| 格式/来源 | 知识仅支持可提取文字的 PDF、DOCX、MD、TXT；Insights 当前启用 JUnit XML 上传/重试/更正 | 无 OCR、图片/Excel；无获授权外部 CI 连接器或真实来源负责人签字。 |
| 数据量/性能 | 固定本地 fixture；Task 11 本地 smoke 为 5 次、每次 20 查询、并发 10，观测 P95 `109.945 ms` | 100 万 attempt/90 天、汇总 P95≤2s、钻取 P95≤3s 的签名硬件 Gate 均 `NOT RUN / PENDING`，不得外推。 |
| 备份/恢复 | 故障矩阵总阶段 41 秒，已验证单独备份卷→全新 ClickHouse 数据卷恢复并校验投影 | 未单独采集生产级备份时长/RPO/RTO；生产保留、加密、异地副本及恢复签字待补。 |
| 身份/签字 | 固定验证身份和服务 token 只限回环隔离环境 | 真实 IdP/RBAC、多 Project、具名业务复核、数据源负责人、运维、安全和发布签字全部 `PENDING`。 |

## 仓库级检查

| 命令 | 截至 2026-09-27 的结果 |
| --- | --- |
| `make contracts` | `PASS`；将 `UV_CACHE_DIR` 指向隔离临时目录后完成，生成契约无漂移。首次运行只因默认 uv cache 不可访问而中止，不是产品失败。 |
| `make check` | `PASS`；产品边界 23 项、Ruff、格式、mypy 209 项、shell 语法、契约漂移、两套 Web lint/format/type/build、TAP backend 103 项及品牌检查均通过。 |
| `make test` | 最近一次全量结果仍为 `FAIL`：`3405 passed, 226 skipped, 49 failed`。其中 15 项为未改动 supervisor/parser/generated-contract 既有失败，34 项为当次沙箱拒绝连接 `127.0.0.1` MySQL 的集成测试；最终兼容性修复后未用窄测试改写该历史全量结果，改为重跑上述联合 runner 与 `make check`。 |
| `make demo-e2e` | `PASS`；由最终联合 runner 以该原始 Make target 执行：6 个浏览器规格、应用重启、Compose 重启和最终 29 个持久化断言通过。 |
| `git diff --check` | `PASS`。 |
| `git diff -- README.md docs/ AGENTS.md` | 已人工检查；只包含本 Gate、索引、状态同步与稳定截图链接，`AGENTS.md` 未改。 |

仓库级失败只按实际输出分类，不用 Task 14 的窄测试覆盖或改写。任何既有失败保留原样并列为 pre-existing；Task 14 范围内失败必须修复后重跑。

## M1–M4 结论

| 批次 | 结构结论 | 业务 Gate 结论 |
| --- | --- | --- |
| M1 可信知识 | 隔离资料核对、独立复核、发布、问答/引用、撤回与重启恢复 `PASS` | **PENDING**：新增可信知识真实 100 文件/200 问、真实模型和逐例具名复核未运行。 |
| M2 测试设计 | 隔离需求冻结、生成、编辑/冲突恢复、业务评审、发布与旧数据升级 `PASS` | **PENDING**：V2 Graph 与 V3 Test Design 当前真实候选、真实模型执行和具名 reviewer 未运行；V2/V3 仍 `gate-reopened`。 |
| M3 基础 Insights | JUnit→账本→ClickHouse→查询/钻取及重启、重建、备份恢复 `PASS` | **PENDING**：获授权外部真实 CI 来源、语义/凭证/保留签字和签名硬件规模 Gate 未运行。 |
| M4 联合解释与交付 | 只读跨产品工具、事实/假设/缺失分离、恢复/隔离与安全交接 `PASS` | **PENDING**：依赖 M1/M3 且完整范围还依赖 M2；生产身份、安全、运维、发布签字均未取得。 |

因此本轮不改变 [可信知识、Graph 与测试设计质量重新验收](2026-09-26-trusted-knowledge-graph-test-design-gate.md) 的 `NOT RUN / PENDING` 结论，不关闭 [V2/V3 更正门禁](2026-09-14-v2-v3-gate-correction.md)，不放行 V4，也不把 RFC-011 标为 `implemented`。
