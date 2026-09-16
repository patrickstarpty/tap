---
id: ADR-027
status: accepted
date: 2026-09-15
supersedes: []
superseded-by: []
related-rfcs: [RFC-009]
---

# ADR-027：Tap AI 采用独立前后端应用边界

## 背景

当前可运行的 Tapper 知识、图谱、模型与 Agent/Skill、AI 测试方案能力与 TAP 非 AI 低代码和测试分析原型混在同一前端产品壳。后端的测试方案生成与 Conversation、Turn、Citation 和 Project 数据有关联。只复制页面或移动生成按钮无法提供独立运行与迁移能力。

## 决策

将全部现有 AI 用户面迁到 `apps/tap-ai-frontend`，其 API、迁移链、Relay 和 workers 迁到 `apps/tap-ai-backend`。`apps/web` 和 `apps/backend` 保留 TAP 非 AI 应用入口。Tap AI 前后端可以在不启动 TAP 前后端的情况下本机运行；MySQL、Redis、对象存储、LiteLLM 和 Milvus 仍通过配置连接。公开 HTTP 路径、数据库表名、事件格式和 `TAPPER_*` 配置名保持稳定；迁移不重置现有数据。当前运行仍遵守回环、无认证 Demo 边界，远程与生产部署另行设计安全入口。

## 考虑过的方案

- 复制整个现有应用再分别维护两份 AI 代码：启动较快，但业务合同和修复容易分叉。
- 只重命名原应用目录：不能让 TAP 非 AI 应用继续单独构建，也无法明确产品责任。

## 后果

两个产品分别声明依赖并构建。既有 Python 包和 Alembic 链由 Tap AI 持有；TAP 非 AI 后端当前仅保留独立应用入口，不宣称已有未实现的业务 API。原组合式 Web 原型必须按产品导航拆分；跨产品的未来数据和身份集成需要正式合同。
