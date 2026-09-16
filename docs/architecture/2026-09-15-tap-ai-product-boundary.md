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
