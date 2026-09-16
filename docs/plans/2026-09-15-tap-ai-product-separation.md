---
status: completed
date: 2026-09-15
---

# Tap AI 产品拆分实施计划

**目标**：将现有 AI 用户面、API、持久化和后台任务迁到 `apps/tap-ai-frontend` 与 `apps/tap-ai-backend`，同时让 `apps/web` 和 `apps/backend` 保留 TAP 非 AI 应用入口，并提供本机独立启动路径。

**设计**：[Tap AI 产品边界与本机独立部署](../architecture/2026-09-15-tap-ai-product-boundary.md)。

## 任务 1：固定现状与边界

- [x] 记录已有未提交改动；不得覆盖 `retrieve.py`、知识/模型/测试方案界面和已有测试的工作区内容。
- [x] 运行现有前后端目标测试，记录基线；确认每个 HTTP 路由和 Web feature 的产品归属。
- [x] 写入可失败的产品边界验证：Tap AI 应用必须提供 AI 路由，TAP 应用不得提供 AI 路由；两端不得跨应用源目录导入。

## 任务 2：后端应用

- [x] 将现有 `tap` 包、Alembic 链和 AI 测试迁到 `apps/tap-ai-backend`，保留 Python import、表名、API operation ID、公开路径和配置键。
- [x] 更新 uv workspace、冻结锁、契约导出、迁移和运行脚本的目录引用；使 AI 包无需 `apps/backend` 即可安装、迁移和启动。
- [x] 在 `apps/backend` 建立独立的 TAP 非 AI FastAPI 包、健康入口和独立项目声明；不复制 AI 模块或迁移链。
- [x] 对应验证：两个 Python 包分别可导入；AI 合同、迁移和关键 worker 测试通过；TAP 应用没有 AI 路由。

## 任务 3：前端应用

- [x] 将 Knowledge、Conversation、Graph、Runtime、Agent/Skill、AI Test Design 与 Tapper 用户面迁到 `apps/tap-ai-frontend`，并使 Tapper 品牌素材纳入其构建上下文。
- [x] 从混合产品壳抽出 TAP 低代码自动化和测试分析原型，留在 `apps/web`；TAP 应用不得直接导入 AI 应用源码。属于 Tapper 编排的测试方案流程留在 Tap AI。
- [x] 更新 pnpm workspace、冻结锁、合同生成、Vite 代理和运行脚本；两个项目分别构建，Tap AI 前端只连接 Tap AI API。
- [x] 对应验证：Tapper/Knowledge/Graph/AI Test Design 目标测试通过，TAP 非 AI 页面目标测试通过，两个 Vite 构建分别通过。

## 任务 4：本机独立部署与文档

- [x] 提供独立的 Tap AI bootstrap、migration、dev/check 命令。`dev` 在前台启动并在退出时清理 Tap AI 应用进程；基础服务由配置提供，继续使用本机回环地址和当前 Demo 安全边界。
- [x] 更新 README、Makefile、配置说明、当前架构和实施入口，区分现有本机行为与未来远程部署；现有 `.env.example` 键名保持不变，不改写历史 ADR/RFC 的决策语义。
- [x] 运行目录路径检查、契约检查、迁移检查、前后端目标测试、`make check`、`make test`、隔离 `make demo-e2e`、`git diff --check`，检查原有工作区改动是否保留。

## 约束

不得重置卷、丢弃现有数据或把本机 Demo 宣称为生产部署。按最小范围更新路径，保留当前公开合同。已完成步骤经验证后再进入下一任务。
