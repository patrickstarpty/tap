# TAP AI 产品边界与本机独立部署

本设计记录 2026-09-15 确认的产品拆分。TAP AI 拥有知识问答、文档与图谱、模型目录、Agent/Skill 资产，以及与会话和引用关联的 AI 测试方案生成、保存和评审。TAP 保留非 AI 产品面，包括现有低代码自动化和测试分析原型。两者在同一仓库中分别构建和启动。

## 应用归属

| 应用 | 归属 | 独立入口 |
| --- | --- | --- |
| `apps/tap-ai-frontend` | Tapper、Knowledge、Graph、Conversation、Model/Agent/Skill、AI Test Design | Vite 构建和 Web 启动 |
| `apps/tap-ai-backend` | 上述功能的 HTTP API、持久化、迁移、Relay 与 workers | Python 包、Alembic 与多个进程入口 |
| `apps/web` | TAP 非 AI 页面与应用壳 | 单独的 Web 构建和启动 |
| `apps/backend` | TAP 非 AI 后端应用壳 | 单独的 Python 包和 API 入口 |

现有业务 HTTP 路由几乎都属于 AI 功能。拆分后的 TAP 后端不虚构非 AI API；其独立入口仅承担后续 TAP 业务的应用边界。AI 测试方案的完整生命周期归 TAP AI，因为其生成任务通过外键关联 Conversation、Turn 与 Citation。

## 运行与数据

TAP AI 前端仅调用 TAP AI 后端；不依赖 TAP 前后端进程。TAP AI 后端通过配置连接 MySQL、Redis、对象存储、LiteLLM 和 Milvus，独立执行当前迁移链并启动 API、Relay、ingestion、graph、test-design 和 generation workers。现有数据和对象引用保持可读；拆分不执行卷重置。部署入口沿用当前本机回环、无认证的 Demo 安全边界；远程访问和生产部署需要单独的安全设计。

[ADR-029](../decisions/2026-09-18-adr-029-langgraph-ai-interaction-task-orchestrator.md) 进一步固定目标 AI 运行边界：TAP AI 的 Project Chat 与测试管理等 TAP AI 自有入口发起的 AI Task，由 TAP AI 后端内同一版本化 LangGraph 编排，明确支持 Fast Chat、Durable Workflow 和 Bounded Agentic Task，并只通过 ModelGateway、SearchPort、TAP Insights API 和 TAP Domain APIs 访问模型、Milvus/MySQL Graph 知识、ClickHouse 历史指标及业务动作。LangGraph 是后端库内编排边界，不改变本文的应用归属，也不新增独立 Query Router、Model Router 或跨产品通用 Agent 平台。RFC-006/ADR-018 的 legacy loopback Codex 回答组合不挂载 Project API，不在该目标图作用域内。当前代码尚未实现该目标图。

[RFC-011 主动 Agent](../proposals/2026-09-17-rfc-011-rag-test-design-cross-platform-automation.md#2-主动-agent) 补充主动测试运营 Agent 的 **draft 功能目标**：项目事件准入、工作记忆、建议收件箱、订阅、批准与 AI Task 归 TAP AI，主动任务进入上述同一 LangGraph；正式 Run、执行证据、Insights 与 Jira/外部动作保持各自领域归属，经正式 API 和服务身份再授权。工作记忆不替代批准知识，启用订阅默认只授权分析及独立待审草稿；该目标不增加第四种执行剖面，不建立通用桌面监听服务，也不意味着当前 TAP 非 AI 后端已有相应接口。

迁移时保留现有 `tap` Python 包、公开 HTTP 路径、数据库表名、事件格式及 `TAPPER_*` 配置名，以降低数据和客户端兼容风险。产品名与应用目录可变，公开契约不因目录迁移而改名。TAP AI 品牌素材必须随 TAP AI 前端构建包含，不依赖仓库根目录的额外运行文件。

## 验收

两个前端、两个后端分别构建或导入。仅启动 TAP AI 应用进程与已配置的基础服务，可完成知识问答、文档操作、图谱、AI 资产和 AI 测试方案关键路径。原 TAP 非 AI 页面仍能启动。迁移检查、契约生成、目标测试、仓库检查和差异检查通过，并且保留工作区已有未提交改动。

`make check` 和 `make test` 执行双向 Python 产品导入检查，禁止 `tap_platform` 与 `tap` 相互导入，也禁止通过对方源码目录导入。检查器只解析源码，不加载另一产品包；`make tap-ai-check` 只扫描 AI 源码，并用临时独立产品目录执行正反例。TAP 后端测试对完整路由表、处理器归属和公开 OpenAPI 做显式白名单校验，目前只允许 `/health/live` 和 FastAPI 文档入口；隐藏路由、挂载子应用与 WebSocket 也不能绕过。新增 TAP API 必须同时更新归属白名单。

`corepack pnpm --dir apps/tap-ai-frontend run prototype:capture` 仅采集当前 AI 自有页面，在隔离浏览器中使用确定性 API 示例。产物位于该应用的 `test-results/prototype-capture/`，不覆盖拆分前的客户演示截图。它是 UI 截图 smoke，不是后端或真实模型验证；现行采集说明见[历史演示指南顶部](../reference/2026-09-04-customer-prototype-demo-guide.md)。

## 浏览器原型工作区升级

TAP 的 Automation、BDD 编辑和模拟 Run 属于浏览器本地原型数据，不是后端生产数据。旧版保存在 `tap.prototype.workspace.v2.artifacts`。TAP 现在使用 `tap.automation.workspace.v1`；当前源没有新快照时，会校验并恢复旧版 artifacts，随后仅写入新键，保留旧键原文。已存在的新快照优先；如果此前已保存过初始工作区，可在 `Local workspace` 中选择 `Review pre-split workspace`，预览后明确恢复旧版。

浏览器存储按协议、主机、端口和浏览器配置文件隔离。TAP 默认使用 `http://127.0.0.1:5174`，无法读取旧默认 `http://127.0.0.1:5173` 的存储；`localhost` 与 `127.0.0.1` 也不是同一主机。不会尝试绕过浏览器同源限制或借助 TAP AI 运行时读取数据。旧配置文件或旧源的数据已清除时，必须使用此前导出的文件；应用无法重新生成丢失的浏览器状态。

从旧默认端口转移：

1. 保持原浏览器配置文件不变。如 TAP AI 正占用 5173，先停止该前端进程，再从仓库根目录临时启动 TAP：

   ```sh
   corepack pnpm --filter @tap/web dev --port 5173 --strictPort
   ```

2. 打开之前保存原型时使用的**完整源**，默认是 `http://127.0.0.1:5173`。检查自动恢复的 Automation 编辑和 Run；如果新工作区已经存在，使用 `Local workspace` → `Review pre-split workspace` → `Restore imported workspace` 恢复旧版。此操作先备份当前工作区，不修改旧键。
3. 在 `Local workspace` 中选择 `Export workspace`，保存 JSON 文件。停止临时 TAP 进程，执行 `make tap-web-dev`，打开 `http://127.0.0.1:5174`。
4. 选择 `Local workspace` → `Import workspace`，选中 JSON 文件，检查数量后选择 `Restore imported workspace`。该操作校验嵌套数据，先保留目标源的当前工作区，再替换；解析、备份或写入失败时不会替换当前工作区。旧版完整 v2 JSON 快照也可直接导入。
5. 刷新并检查编辑和模拟 Run。`Export previous workspace` 可导出最近一次恢复之前的目标工作区，再通过相同导入流程回退。备份保存在目标源的 `tap.automation.workspace.backup.<时间>.<唯一标识>` 键下，新的恢复不会覆盖旧备份。确认完成后可重新启动 TAP AI。

上述流程保留两个产品的默认独立端口，不改公共 API、存储服务或回环绑定，也不传输真实执行证据或后端数据。
