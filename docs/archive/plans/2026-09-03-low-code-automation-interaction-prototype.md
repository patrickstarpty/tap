---
status: completed
date: 2026-09-03
---

> **命名归一化说明（2026-09-05）：** 本文只对产品和仓库标识做 Tapper 命名归一化，原日期、状态、决策、范围与评审结论未改变。命名归一化前的字节级原文以 Git commit `0eab801` 为准；下列命令或证据文本属于 identifier-normalized transcription，不再声明与该提交逐字节相同。

# Low Code Automation 交互原型实施计划

> **执行方式：** 按用户要求在当前任务内联完成，只做纯前端原型，不使用 Subagent-Driven Development，也不把原型扩展为真实执行平台。

## 目标

把原有“单份临时自动化草稿”升级为资产优先的交互原型，并验证以下已确认旅程：

1. Low Code Automation 默认进入资产列表；用户可打开既有 Automation 或创建新资产。
2. Automation 详情以 BDD 为主结构，每个业务步骤直接关联有序的 `Click`、`Send keys` 等实现动作。
3. Test Plan 与 Automation 可选、严格双向 `1:1`。
4. Web Run 选择 Azure DevOps Pipeline Agent；Mobile Run 选择平台和设备。
5. 已关联 Run 以同一个 Run ID 投影到 Automation 与 Test Plan 两侧历史。
6. Tapper 识别自动化意图后先询问是否创建 Test Plan，再交付相应资产入口。
7. Conversation 首轮后进入历史，同一会话多轮合并，跨模块与刷新后恢复。

产品事实源为 [RFC-008](../proposals/2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md)。

## 原型边界

- 只修改 React/TypeScript/CSS、前端 fixture、测试与文档。
- 使用版本化 `localStorage` 模拟 Conversation、Automation、Test Plan 和 Run 的刷新恢复；不声称服务端持久化。
- 所有 Run 固定为 `simulated`，`evidenceKind` 为 `none`；不连接 Azure DevOps、浏览器、真机或设备云。
- Tapper 的 `AI Agent` 与 Web 的 `Execution Agent / Azure DevOps Pipeline Agent` 分开建模和呈现。
- 不新增依赖，不修改 backend 或 contracts。
- 真实 URL 深链、浏览器前进后退、权限、凭据、调度、Evidence、重试与并发不属于本原型。

## 状态模型

```text
ArtifactState
├── Automation[]
│   └── Feature → Scenario[] → BddStep[] → ImplementationAction[]
├── TestPlan[]
└── AutomationRun[]
```

### 关联规则

- `Automation.testPlanId` 与 `TestPlan.automationId` 原子更新。
- 已被其他资产占用的对象不出现在可选列表中。
- 未关联是合法状态。

### 运行规则

- `AutomationRun` 是唯一运行事实。
- Run 开始时固化 `automationId`、`testPlanIdAtRun`、`triggeredFrom` 与执行目标。
- `testPlanIdAtRun` 非空时，两侧历史读取同一 Run 对象。
- 未关联 Run 不因后来建立关联而回填；解除关联不删除历史快照。

### Conversation 规则

- 空白 New Chat 不显示为历史记录。
- 首条消息发送后，以首条消息作为历史标题。
- 后续 Turn 追加到同一历史项。
- 当前历史项始终可见并高亮。
- 本地数据损坏或版本不兼容时安全回退到初始状态。

## 实施清单

### 1. 产品文档与验收合同

- [x] 在 RFC-008 记录 `AUT-011`、`AUT-012`、`ATH-005`。
- [x] 记录 BDD/动作映射、共享 Run 和 Conversation 恢复语义。
- [x] 明确纯前端模拟与真实 Provider/Evidence 的边界。

### 2. 资产与持久化模型

- [x] 新增 Automation、Test Plan、BDD、Implementation Action、Execution Target 与 Run 类型。
- [x] 新增稳定 fixture 和严格 `1:1` reducer。
- [x] 新增共享 Run selector，并保留运行开始时的 Test Plan 快照。
- [x] 新增版本化本地快照读写与损坏回退。
- [x] 以测试覆盖动作归属、关联冲突、双视图 Run、禁止回填和动态 Trace ID。

### 3. 核心交互

- [x] Automation Library：总数、搜索、既有资产、创建入口。
- [x] New Automation：Goal、显式类型、无歧义类型推断和可选 Test Plan。
- [x] Automation Detail：Scenario 导航、BDD 编辑、内联动作映射、脚本折叠视图。
- [x] Detail AI Agent：建议先展示，用户 Apply 或 Reject。
- [x] Web/Mobile 目标选择与模拟运行历史。
- [x] Test Plan 详情：双向关联、场景覆盖、运行入口与执行记录。
- [x] Tapper Yes/Skip 编排与 Test Plan/Automation 资产卡片。
- [x] Conversation 当前项显示、模块切换和刷新恢复。

### 4. 视觉与验证

- [x] 使用资产列表与三栏详情的行业常见信息架构。
- [x] 桌面检查 Automation Library、BDD/动作映射和 Test Plan 执行历史。
- [x] `390 × 844` 检查并确认无页面横向溢出。
- [x] 检查浏览器 console error/warning。
- [x] 通过相关 Vitest、ESLint 和 Web production build。
- [x] 完成仓库级最终检查并将本 Plan 更新为 `completed`。

## 主要文件

```text
apps/web/src/widgets/tap/
├── TapProductPrototype.tsx
├── TapProductPrototype.css
└── prototype/
    ├── PrototypeSidebar.tsx
    ├── model.ts
    ├── artifacts/
    │   ├── fixtures.ts
    │   ├── model.ts
    │   ├── persistence.ts
    │   └── state.ts
    ├── automation/AutomationWorkspace.tsx
    └── testManagement/TestManagementWorkspace.tsx
```

## 验证命令

```sh
corepack pnpm --dir apps/web exec vitest run \
  src/pages/TapperPage.test.tsx \
  src/widgets/tap/TapProductPrototype.interactions.test.tsx \
  src/widgets/tap/prototype/model.test.ts \
  src/widgets/tap/prototype/artifacts/state.test.ts \
  src/widgets/tap/prototype/artifacts/persistence.test.ts

corepack pnpm --dir apps/web run lint
corepack pnpm --dir apps/web run build
git diff --check
```

最终结果：相关 Vitest `101 passed`，Web 全量 Vitest `226 passed`；ESLint、Prettier、架构边界、production build 与 `git diff --check` 均通过。桌面及 `390 × 844` 浏览器路径完成复核，console warning/error 为 0。

## 后续但不在本原型范围

- 正式 URL 深链与浏览器 History。
- 服务端 Conversation 和资产持久化。
- 真实 Azure DevOps Pipeline、浏览器或移动设备执行。
- 权限、凭据、队列、重试、并发和 Evidence Manifest。
- 大列表筛选、分页、矩阵执行与跨平台实现差异管理。
