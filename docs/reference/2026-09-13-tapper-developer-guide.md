# Tapper 开发者指南

本文面向继续开发 TAP 内部 AI Chatbot、Knowledge Graph 和 AI Test Design 的工程人员。它是任务导航，不替代架构、契约、ADR、实施计划或 Gate Review。

## 1. 当前基线

截至 2026-09-14：

| 里程碑 | 状态          | 已实现能力                                                                                                              |
| ------ | ------------- | ----------------------------------------------------------------------------------------------------------------------- |
| V0     | `gate-passed` | 固定 Validation Scope、Project 授权、Audit/Outbox、MinIO、隔离 Parser 与恢复门禁                                        |
| V1     | `gate-passed` | Source/Document Revision、Milvus 检索、统一 ModelGateway、Agent/Skill、持久 Conversation、SSE、Citation 与真实 Web 接线 |
| V2     | `gate-passed` | 版本化 Knowledge Graph、真实抽取 Worker、有界 Graph Context、Evidence API 与 WebGL Explorer                             |
| V3     | `gate-passed` | Test Plan Draft/Revision、grounded 生成、Review、人工发布和 `QUALITY-TEST-01` 已通过                                    |

V3 已通过[AI 测试设计门禁](../reviews/2026-09-14-v3-ai-test-design-gate.md)。最新状态以[实施计划状态表](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md#执行状态2026-09-14)和[评审索引](../reviews/index.md)为准，不根据目录名、原型页面或计划复选框推断。

## 2. 建议阅读顺序

1. [README](../../README.md)：能力边界、本地启动和日常命令。
2. [当前架构](../architecture/2026-09-04-tapper-knowledge-web-automation-overview.md)：模块职责、数据主权和安全不变量。
3. [核心契约](2026-09-04-tapper-platform-contracts.md)：Project、Conversation、Graph、Test Plan 和错误语义。
4. [当前实施计划](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md)：当前 Task 的精确文件、RED/GREEN 命令与提交边界。
5. [V1](../reviews/2026-09-09-v1-trusted-knowledge-gate.md)、[V2](../reviews/2026-09-13-v2-knowledge-graph-gate.md)和[V3](../reviews/2026-09-14-v3-ai-test-design-gate.md) Gate Review：已经通过的实际证据和限制。

历史 RFC、被替代 ADR 和旧独立 `TapperWorkspace` 只用于追溯，不是当前产品入口。

## 3. 本地启动

从仓库根目录执行：

```sh
cp .env.example .env
# 填写 DASHSCOPE_API_KEY，并替换 DASHSCOPE_API_BASE 中的 Workspace ID
make bootstrap
make object-store-build PLATFORM=linux/arm64
TAPPER_PARSER_PLATFORM=linux/arm64 make parser-build
make demo-up
make demo-check
make demo-dev
```

`linux/arm64` 是已验证示例；其他机器必须使用匹配平台并重新完成本机验证。Web 位于 `http://127.0.0.1:5173/`，API 位于 `http://127.0.0.1:8000/`。`make demo-dev` 同时运行 API、Relay、Ingestion、Conversation Generation、Graph、Test Design Worker 和 Web；任一受监管子进程退出都会使 supervisor 失败。

不要把 `.env`、真实业务数据、Provider payload 或质量门禁 observation 提交到 Git。普通停止使用 `make demo-down`；只有明确需要删除默认 Demo 全部具名卷时才使用 README 中受保护的 `demo-reset`。

## 4. 真实业务链路

```text
Runtime Mode / Validation Project
  → Source 上传与隔离解析
  → Document Revision + Chunk Manifest + Milvus 投影
  → Conversation Turn Input Snapshot
  → 检索 + 可选有界 Graph Context
  → Answer/Evidence Snapshot + Citation + SSE
  → AI Test Design Job
  → Test Plan Draft + Citation/Assumption/Unknown/Coverage Gap
  → 人工 Review + 确定性发布门禁
  → 不可变 Published Revision
```

浏览器先读取 `/api/v1/runtime-mode`，再使用服务端返回的可信 `projectId` 创建 Project-scoped client。业务 API 位于 `/api/v1/projects/{project_id}` 下；浏览器不能提交或覆盖 Actor、Role、Enterprise 或权威 Scope。

默认页面入口为：

```text
apps/web/src/app/App.tsx
  → apps/web/src/pages/TapperPage.tsx
  → apps/web/src/widgets/tap/TapProductPrototype.tsx
```

当前产品壳通过 `conversationSource="api"` 使用真实 Conversation、Knowledge、Graph 和 Test Plan API。`apps/web/src/widgets/tapper/TapperWorkspace.tsx` 是旧兼容入口，不应作为新页面或视觉基线。

## 5. 代码入口

| 关注点                    | Backend                                                                         | Web                                           |
| ------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------- |
| Runtime/装配              | `apps/backend/src/tap/entrypoints/tapper_runtime.py`                            | `apps/web/src/app/providers.tsx`              |
| HTTP 与 Scope             | `apps/backend/src/tap/interfaces/http/app.py`、`dependencies.py`                | `apps/web/src/features/runtime/`              |
| Knowledge/Source          | `apps/backend/src/tap/modules/knowledge/`                                       | `apps/web/src/features/knowledge/`            |
| Conversation/SSE          | `apps/backend/src/tap/modules/chat/`、`routes/conversations.py`                 | `apps/web/src/features/conversations/`        |
| Model Gateway/Agent/Skill | `apps/backend/src/tap/modules/ai/`                                              | Tapper composer/context picker                |
| Knowledge Graph           | `apps/backend/src/tap/modules/graph/`、`tapper_graph_worker.py`                 | `apps/web/src/features/graph/`                |
| Test Plan                 | `apps/backend/src/tap/modules/test_management/`、`tapper_test_design_worker.py` | `apps/web/src/features/testManagement/`       |
| 公共契约                  | `apps/backend/src/tap/contracts/`                                               | `apps/web/src/shared/api/generated/schema.ts` |
| 浏览器 E2E                | `scripts/run-tapper-e2e.sh`                                                     | `apps/web/tests/e2e/`                         |

Backend 依赖方向保持 `domain → application/ports ← adapters/interfaces`。Domain 不得依赖 FastAPI、Pydantic HTTP DTO、SQLAlchemy 或 Provider SDK。Web 保持 `app/pages → widgets → features → shared`，Feature 不读取 Prototype 状态。

## 6. 扩展方式

### 修改模型或生成能力

- 从 `apps/backend/src/tap/modules/ai/ports/gateway.py` 的统一 `ModelGateway` 进入。
- Knowledge、Graph 和 Test Design 共用 Gateway 的 alias、超时、脱敏和审计边界；不得增加绕过 LiteLLM 的第二个正式模型出口。
- 结构化输出必须有封闭 Schema、确定性 Validator 和契约测试。模型只能创建 Draft/Proposal，不能直接发布 Revision。

### 修改检索或 Graph Context

- 保持 Project/Source/Document Revision 过滤由服务端权威上下文生成，不能接受浏览器传入的 ACL 事实。
- Milvus 是可重建投影，不是权限或业务事实源。
- Graph 扩展必须保持当前 Snapshot、最多两跳和节点预算；Graph 失败应保留文档问答并记录明确状态，不能伪装成空结果。

### 修改 Conversation 或 SSE

- Turn 接受时冻结 Input Snapshot；完成时另存 Answer/Evidence Snapshot，不能回写输入事实。
- SSE sequence 单调递增并支持 `Last-Event-ID` 恢复；未知事件或版本不能直接进入浏览器状态。
- 取消、重试和 Worker 恢复必须依赖 MySQL ledger、幂等键和 lease/fencing，不能把 Redis 当事实源。

### 修改 Test Plan 生成

- Generation Job 必须绑定同一 Project/Turn 的 Input 与 Answer/Evidence digest。
- 来源事实、Graph inference、Assumption、Unknown 和 Coverage Gap 必须保持不同语义；INFERRED edge 不能作为来源事实发布。
- Edit 使用版本条件，Publish 必须经过确定性门禁并生成不可变 Revision、Audit 和 Outbox 事件。

修改公共 DTO、事件或错误语义时，同步 Backend models、OpenAPI/SSE/Project event schema、生成的 Web 类型和对应契约测试，然后运行 `make contracts`。

## 7. 验证路径

先运行当前改动的窄测试，再按风险扩大：

```sh
make contracts
make check
make test
make demo-e2e
git diff --check
```

质量门禁分为离线确定性检查和显式授权的真实模型检查：

```sh
make quality-kb
make quality-graph
make quality-test-design
```

`quality-*-real` 会产生真实 Provider 调用，必须使用对应 opt-in、隔离环境和人工复核输入；默认 CI 和普通 `make test` 不得触发。完成声明必须链接实际 Gate Review，不能用 Fake Adapter、页面 fixture、skip 或单次 happy path 代替。

## 8. 常见判断错误

- **看到 UI 就认为功能已实现：** 原型 fixture 和模拟 Run 不构成后端能力证据。
- **看到计划复选框未勾选就认为尚未开发：** 查看计划顶部状态表、Git 提交和 Gate Review。
- **看到代码已提交就宣布里程碑完成：** 只有真实质量门禁和 Review 为 `pass` 才能推进下一阶段。
- **把 Validation 身份当作公司账号体系：** P0 前没有产品登录、Session、Membership、RBAC 或多 Project 产品化。
- **把本地 GREEN 当作生产就绪：** TLS、Secret 轮换、备份恢复、容量、审计导出和受控 Pilot 属于 P1。

## 9. 当前下一步

V3 已完成。下一步从 V4 Task 18 开始，先实现 authoritative Test IR、Automation Revision 和确定性 Playwright 生成器；继续遵守实施计划的顺序与门禁，不能把现有原型 fixture 当作 V4 实现。
