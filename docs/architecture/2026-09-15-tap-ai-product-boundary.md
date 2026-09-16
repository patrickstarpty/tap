# Tap AI 产品边界与本机独立部署

本设计记录 2026-09-15 确认的产品拆分。Tap AI 拥有知识问答、文档与图谱、模型目录、Agent/Skill 资产，以及与会话和引用关联的 AI 测试方案生成、保存和评审。TAP 保留非 AI 产品面，包括现有低代码自动化和测试分析原型。两者在同一仓库中分别构建和启动。

## 应用归属

| 应用 | 归属 | 独立入口 |
| --- | --- | --- |
| `apps/tap-ai-frontend` | Tapper、Knowledge、Graph、Conversation、Model/Agent/Skill、AI Test Design | Vite 构建和 Web 启动 |
| `apps/tap-ai-backend` | 上述功能的 HTTP API、持久化、迁移、Relay 与 workers | Python 包、Alembic 与多个进程入口 |
| `apps/web` | TAP 非 AI 页面与应用壳 | 单独的 Web 构建和启动 |
| `apps/backend` | TAP 非 AI 后端应用壳 | 单独的 Python 包和 API 入口 |

现有业务 HTTP 路由几乎都属于 AI 功能。拆分后的 TAP 后端不虚构非 AI API；其独立入口仅承担后续 TAP 业务的应用边界。AI 测试方案的完整生命周期归 Tap AI，因为其生成任务通过外键关联 Conversation、Turn 与 Citation。

## 运行与数据

Tap AI 前端仅调用 Tap AI 后端；不依赖 TAP 前后端进程。Tap AI 后端通过配置连接 MySQL、Redis、对象存储、LiteLLM 和 Milvus，独立执行当前迁移链并启动 API、Relay、ingestion、graph、test-design 和 generation workers。现有数据和对象引用保持可读；拆分不执行卷重置。部署入口沿用当前本机回环、无认证的 Demo 安全边界；远程访问和生产部署需要单独的安全设计。

迁移时保留现有 `tap` Python 包、公开 HTTP 路径、数据库表名、事件格式及 `TAPPER_*` 配置名，以降低数据和客户端兼容风险。产品名与应用目录可变，公开契约不因目录迁移而改名。Tap AI 品牌素材必须随 Tap AI 前端构建包含，不依赖仓库根目录的额外运行文件。

## 验收

两个前端、两个后端分别构建或导入。仅启动 Tap AI 应用进程与已配置的基础服务，可完成知识问答、文档操作、图谱、AI 资产和 AI 测试方案关键路径。原 TAP 非 AI 页面仍能启动。迁移检查、契约生成、目标测试、仓库检查和差异检查通过，并且保留工作区已有未提交改动。

## 浏览器原型工作区升级

TAP 的 Automation、BDD 编辑和模拟 Run 属于浏览器本地原型数据，不是后端生产数据。旧版保存在 `tap.prototype.workspace.v2.artifacts`。TAP 现在使用 `tap.automation.workspace.v1`；当前源没有新快照时，会校验并恢复旧版 artifacts，随后仅写入新键，保留旧键原文。已存在的新快照优先；如果此前已保存过初始工作区，可在 `Local workspace` 中选择 `Review pre-split workspace`，预览后明确恢复旧版。

浏览器存储按协议、主机、端口和浏览器配置文件隔离。TAP 默认使用 `http://127.0.0.1:5174`，无法读取旧默认 `http://127.0.0.1:5173` 的存储；`localhost` 与 `127.0.0.1` 也不是同一主机。不会尝试绕过浏览器同源限制或借助 Tap AI 运行时读取数据。旧配置文件或旧源的数据已清除时，必须使用此前导出的文件；应用无法重新生成丢失的浏览器状态。

从旧默认端口转移：

1. 保持原浏览器配置文件不变。如 Tap AI 正占用 5173，先停止该前端进程，再从仓库根目录临时启动 TAP：

   ```sh
   corepack pnpm --filter @tap/web dev --port 5173 --strictPort
   ```

2. 打开之前保存原型时使用的**完整源**，默认是 `http://127.0.0.1:5173`。检查自动恢复的 Automation 编辑和 Run；如果新工作区已经存在，使用 `Local workspace` → `Review pre-split workspace` → `Restore imported workspace` 恢复旧版。此操作先备份当前工作区，不修改旧键。
3. 在 `Local workspace` 中选择 `Export workspace`，保存 JSON 文件。停止临时 TAP 进程，执行 `make tap-web-dev`，打开 `http://127.0.0.1:5174`。
4. 选择 `Local workspace` → `Import workspace`，选中 JSON 文件，检查数量后选择 `Restore imported workspace`。该操作校验嵌套数据，先保留目标源的当前工作区，再替换；解析、备份或写入失败时不会替换当前工作区。旧版完整 v2 JSON 快照也可直接导入。
5. 刷新并检查编辑和模拟 Run。`Export previous workspace` 可导出最近一次恢复之前的目标工作区，再通过相同导入流程回退。备份保存在目标源的 `tap.automation.workspace.backup.<时间>.<唯一标识>` 键下，新的恢复不会覆盖旧备份。确认完成后可重新启动 Tap AI。

上述流程保留两个产品的默认独立端口，不改公共 API、存储服务或回环绑定，也不传输真实执行证据或后端数据。
