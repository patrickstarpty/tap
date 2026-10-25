# Agent 团队视图交接

创建日期：2026-10-25。归属[产品路线图](../superpowers/plans/2026-10-01-product-roadmap.md) V2.1。设计见[Agent 团队视图设计](../superpowers/specs/2026-10-22-agent-team-view-design.md)，实施计划见[Agent 团队视图实施计划](../superpowers/plans/2026-10-23-agent-team-view.md)，部署决策见 [ADR-033](../decisions/2026-10-23-adr-033-langfuse-default-runtime-source.md)。

## 1. 状态

- 工程完成，已合入本地 `develop`（合并提交 `b45b3398`），**尚未推送远端**。
- 路线图 V2.1 行：工程完成；待业务验证（spec 7.4）。
- ADR-033 状态仍为 `proposed`，待负责人接受。
- 演示环境已迁移到 `0037_agent_team_fk_name`，`make demo-dev` 运行的是合并后的代码。

## 2. 代码位置

| 层 | 位置 | 内容 |
| --- | --- | --- |
| 迁移 | `apps/tap-ai-backend/migrations/versions/0036_agent_team.py`、`0037_agent_team_fk_name.py` | `ai_agent` 人事字段、`answer_feedback` 处理字段、`agent_maintenance_event` 表；0037 修正外键名并重置 `trace` 同步游标 |
| 领域 | `apps/tap-ai-backend/src/tap/modules/agent_team/domain/` | `Period` 与粒度、维护事件、指标口径纯函数、修订分类 |
| 端口 | `apps/tap-ai-backend/src/tap/modules/agent_team/ports.py` | `AgentRuntimeMetrics`、`AgentTeamStore` 与读模型 |
| 适配器 | `apps/tap-ai-backend/src/tap/modules/agent_team/adapters/` | Langfuse Metrics API v1、e2e 用 Turn 表、缓存装饰器、MySQL 存储、Turn→Agent 归属 |
| 应用 | `apps/tap-ai-backend/src/tap/modules/agent_team/application/` | `AgentTeamService`、CSV 导出 |
| 接口 | `apps/tap-ai-backend/src/tap/interfaces/http/routes/agent_team.py` | 9 个读接口、3 个写接口，前缀 `/api/v1/projects/{project_id}/ai/` |
| 同步 | `apps/tap-ai-backend/src/tap/modules/ai/adapters/mysql_langfuse_sync.py`、`application/langfuse_mapping.py` | Turn trace 打 `agent:`、`outcome:` 标签；工具调用 span 在 `entrypoints/tapper_chat_turn.py` |
| 前端 | `apps/tap-ai-frontend/src/widgets/tap/workspace/agents/team/` | 四标签页、花名册、人员表、组织架构图、档案抽屉、我的 Agent、汇报页、手写 SVG 图表、打印样式 |
| 数据钩子 | `apps/tap-ai-frontend/src/features/knowledge/api/agentTeam.ts` | 读写钩子；能力 4 的写操作同时让团队查询失效（`aiAssets.ts` 的 `agentTeamRootKey`） |
| E2E | `apps/tap-ai-frontend/tests/e2e/agent-team.spec.ts`、`agentTeamCaptureFixture.ts` | 旅程已列入 `scripts/tapper-e2e-specs.json`；`ui-capture.spec.ts` 有 5 张团队截图 |

## 3. 运行数据与口径要点

- 运行 = 根 span 为 `turn.request` 的 trace，按 `agent:` 标签归属；失败按 `outcome:failed`，拒答与取消计入运行、不计失败。
- 运行数据只查 Langfuse：统计用 Metrics API v1，"最近运行"用 Traces 列表 API；按 `TAP_LANGFUSE_ENVIRONMENT` 过滤。
- 整个视图共享 10 秒期限，单次查询取 `min(8 秒, 剩余时间)`；成功结果缓存 300 秒，失败缓存 30 秒；人事字段、维护、反馈与满意度每次实时查 MySQL。
- Langfuse 不可用时所有运行派生字段为 `null`（界面显示"—"与"运行数据暂不可用"），状态为空（已停用除外），CSV 留空并在三个文件首行注明 `# runtime=unavailable`。
- "运行数据同步中"：已超过同步沉淀窗口（`TAP_LANGFUSE_SYNC_SETTLE_SECONDS`，默认 60 秒）仍未同步的最早 span 超过 5 分钟时显示。
- 时区固定 UTC（尚无项目时区设置）。

## 4. 运维操作

```sh
# 启动（默认包含 Langfuse 与 ClickHouse），检查六项依赖：
make demo-up
make demo-check

# 应用进程：
make demo-dev

# 隔离 E2E（含 agent-team 旅程；e2e 模式下同步 worker 不写共享 Langfuse）：
make demo-e2e

# 真实 Langfuse smoke（读 .env 中 LANGFUSE_*）：
set -a; . ./.env; set +a
TAP_RUN_LANGFUSE_SMOKE=1 TAP_LANGFUSE_SMOKE_PROJECT_ID=tapper-demo \
  uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/smoke/test_langfuse_metrics_smoke.py -q
```

- 同步游标行名为 `trace`（不是 `traces`）。需要让 worker 带标签重推历史时，删除 `langfuse_sync_cursor` 中 `name = 'trace'` 的行；重推期间团队页显示"运行数据同步中"。
- `make demo-dev` 启动失败并提示 `Parser supervisor unavailable` 时，先确认解析器镜像存在；镜像被清理后用 `make parser-build` 重建（按 `.tapper/parser-build.json` 固定版本）。
- `make demo-check` 的磁盘提示测量的是 Docker 虚拟机内 ClickHouse 所在文件系统；低于 5 GB 时 Langfuse 与 ClickHouse 可能无法启动。不要用 `docker image prune` 清理：解析器镜像按 ID 固定、可能没有标签，会被一并删除。
- 运行数据来源由 `TAP_AGENT_TEAM_RUNTIME_METRICS` 控制：默认 `langfuse`；`turn_table` 只在 `TAP_DEMO_MODE=e2e` 时允许。

## 5. 验证记录（2026-10-25，合并后代码）

| 项 | 结果 |
| --- | --- |
| `make check` | 通过 |
| 后端单元、契约、架构测试 | 通过；`tests/unit/knowledge/test_knowledge_review.py` 3 个用例因写死日期在 `develop` 上同样失败，与本功能无关 |
| 隔离 MySQL 集成测试 | `test_agent_team_migration.py` 3、`test_agent_team_store.py` 17、`test_ai_asset_catalog.py` 19，全部通过 |
| 前端 Vitest | 通过 |
| `make demo-check` | 六项 ok |
| `make demo-e2e` | 通过（含 agent-team 旅程）；本次运行未向共享 Langfuse 写入任何 trace |
| 真实 Langfuse smoke | 通过 |
| 线上接口 | `summary`、`members`、`report` 返回 `ok`；非法筛选返回 400 `filter_invalid`；导出为含 BOM 的三文件 zip |
| 截图 | 原有模块截图与改动前一致（配置页仅多出顶部标签栏）；团队、组织架构图、档案、我的 Agent、汇报页与打印版逐张核对 |

## 6. 实施中的决定

以下决定已同步进 spec，列出供复核：

- 修订只换成同一 Skill 的新修订时记 `skill_upgraded`，其余记 `revised`；创建 Agent 不记维护动作。
- Tapper 默认与未知归属的反馈处理后不写维护事件（没有 Agent 记录）；重复处理返回首次结果，并发处理只记一条事件。
- 转交负责人的请求字段为 `newOwnerActorId`：现有身份防伪检查会拒绝任何形如 `actorId` 的请求体字段。
- 档案接口返回 `{row, currentRevisionId, served, pendingFeedback}`；汇报接口返回 `previousPeriod`；履历接口不带读取信封。
- 新增错误码：`cursor_invalid`、`job_title_too_long`、`sort_invalid`、`note_too_long`、`filter_invalid`（均 400）。
- 人均负责／被服务 Agent 数以全部人员范围为分母；报告对比中 Agent 总数与闲置数不可按周期比较，显示"—"。
- 卡片趋势线固定为近 30 天（单独查询），卡片数字为所选周期运行次数。
- 花名册各列可排序；并列按名称稳定，状态按 活跃 < 闲置 < 已停用，类型按 自定义 < 内置 < Tapper 默认。

## 7. 已知限制

- 汇报页抬头显示项目 ID：V4 接入身份与项目元数据前没有显示名；人员同样只显示 `actor_id`。
- 按周、按月趋势依赖 Langfuse 的周一起始与 UTC 分桶，已在 3.225.11 实测；升级 Langfuse 前先重跑 smoke（v4 将移除 Metrics API v1，见 ADR-033）。
- 界面语言沿用仓库现有做法，由文案对象推断。

## 8. 待办

| 事项 | 负责 | 说明 |
| --- | --- | --- |
| 删除共享 Langfuse 中的 e2e trace | 负责人 | 2026-10-24 08:35–09:43 UTC 的 654 条 trace 来自隔离 e2e，演示库无对应记录，会抬高内置 Agent 与 Tapper 默认的运行次数；删除需人工执行（自动化删除被权限策略拦下） |
| 清理 Docker 磁盘 | 负责人 | 剩余约 2.9 GB；只清理确认无用的镜像与卷 |
| 业务验证（spec 7.4） | 负责人 | 用真实模型积累至少一周数据，核对汇报页数字与口径后把路线图 V2.1 改为完成 |
| 接受 ADR-033 | 负责人 | 当前 `proposed` |
| 推送 `develop` | 负责人 | 本地领先远端，含本功能及后续修复 |
| V2 后续 | 开发 | 按路线图进入 LangGraph 收尾（PR 6/7），再进入 V2.2 主动框架 |
