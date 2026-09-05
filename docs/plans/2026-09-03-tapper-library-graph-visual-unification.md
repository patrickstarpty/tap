---
status: completed
date: 2026-09-03
---

> **命名归一化说明（2026-09-05）：** 本文只对产品和仓库标识做 Tapper 命名归一化，原日期、状态、决策、范围与评审结论未改变。命名归一化前的字节级原文以 Git commit `0eab801` 为准；下列命令或证据文本属于 identifier-normalized transcription，不再声明与该提交逐字节相同。

# Tapper Library、知识图谱与视觉统一实施计划

> **执行方式：** 用户已明确要求在当前任务内联完成纯前端原型，不使用 Subagent-Driven Development。实现遵循测试先行，并保留当前工作区中的既有原型改动。

**Goal:** 交付可删除的 Tapper 消息上下文、双层导航 Tapper 标识、Codex 式模型选择、可搜索筛选的 Library、Graphify 式交互知识图谱，以及与 Tapper 一致的 Test Management/LCA 视觉。

**Architecture:** 在现有 React 页面状态中扩展筛选与图谱视图，不新增后端、路由或第三方图布局依赖。Knowledge Graph 使用确定性的 SVG 图数据和本地交互状态，以便测试稳定并明确保持 `prototype-only` 边界。

**Tech Stack:** React 19、TypeScript、Ant Design 6、原生 SVG/CSS、Vitest、Testing Library。

**Spec:** [RFC-008](../proposals/2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md)

## 全局约束

- Tapper 当前视觉是唯一基线；LCA 与 Test Management 不建立独立主题。
- 不新增 npm 依赖，不修改 backend 或 contracts。
- Graph 数据、布局、关系来源和执行结果均为确定性原型 fixture。
- 所有新增交互可键盘操作，状态不只依赖颜色，并尊重 reduced motion。
- 不提交或覆盖工作区中的无关改动。

## Task 1：建立交互验收测试

**Files:**

- Modify: `apps/web/src/widgets/tap/TapProductPrototype.interactions.test.tsx`

**Produces:** 覆盖 `ATH-006`、`ATH-007`、`LIB-001`、`LIB-002` 的失败测试。

- [x] 增加测试：选择 Knowledge、Agent、Skill 后分别出现 `Remove …` 按钮，点击后标签消失且 picker 状态恢复为未选择。
- [x] 增加测试：Tapper 两级导航使用 `A` 标识，空白欢迎区和 Assistant Turn 不再包含头像标识。
- [x] 把 Library 默认页签断言改为 `All`；增加 Type、Status 组合筛选、结果计数和清空筛选断言。
- [x] 增加图谱测试：Community 开关、节点搜索高亮、节点 Inspector、缩放与重置控件均通过可访问名称操作。
- [x] 运行：

  ```sh
  corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx
  ```

  预期：测试因上述控件或行为尚未实现而失败，而不是编译或测试夹具错误。

## Task 2：实现 Tapper 上下文与 Library 筛选

**Files:**

- Modify: `apps/web/src/widgets/tap/prototype/TapperChat.tsx`
- Modify: `apps/web/src/widgets/tap/prototype/PrototypeSidebar.tsx`
- Modify: `apps/web/src/widgets/tap/prototype/LibraryWorkspace.tsx`
- Modify: `apps/web/src/widgets/tap/prototype/copy.ts`
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.css`

**Produces:** 可移除上下文标签、两级导航品牌标识，以及 `All` 搜索筛选工具栏。

- [x] 将每个已选上下文渲染为带类型样式和删除按钮的标签，调用既有 `onToggleSource`、`onToggleAgent`、`onToggleSkill`。
- [x] 移除 Welcome 与 Assistant Turn 中的 `A`，在一级 Tapper 入口与二级 Sidebar 标题增加不会污染可访问名称的字母标识。
- [x] 为 Library 增加 `typeFilter`、`statusFilter` 状态；以查询、类型、状态的交集计算 `visibleSources`。
- [x] 增加结果计数和仅在存在条件时可用的 `Clear filters`。
- [x] 运行 Task 1 的定向 Vitest，预期新增上下文、标识和列表筛选测试通过。

## Task 3：实现 Graphify 式交互图谱

**Files:**

- Create: `apps/web/src/widgets/tap/prototype/KnowledgeGraph.tsx`
- Modify: `apps/web/src/widgets/tap/prototype/LibraryWorkspace.tsx`
- Modify: `apps/web/src/widgets/tap/prototype/copy.ts`
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.css`

**Produces:** 确定性社区图谱、图谱工具栏和节点 Inspector。

- [x] 用稳定 `GraphNode` 与 `GraphEdge` fixture 替代矩形思维导图，并为来源节点接入当前 Library 结果。
- [x] 使用 `viewBox` 变换实现 zoom in、zoom out、reset；使用指针事件实现画布平移，按钮路径保持完整键盘替代。
- [x] 根据 Community 开关和搜索词计算节点显隐与高亮；关系只在两端可见时绘制。
- [x] 节点按钮支持点击和键盘激活，Inspector 展示类型、Community、连接数与相邻关系；边显示 `EXTRACTED / INFERRED` 来源。
- [x] 运行 Task 1 的定向 Vitest，预期全部 Library/Graph 测试通过。

## Task 4：统一 LCA 与 Test Management 视觉

**Files:**

- Modify: `apps/web/src/widgets/tap/TapProductPrototype.css`

**Produces:** 业务结构不变、与 Tapper 共用 token 和组件材质的两个工作区。

- [x] 将 `--tap-workbench-*` 映射到 Tapper 的中性背景、边框、文字和蓝青强调色。
- [x] 将列表、详情、BDD Step、Copilot/Run、Test Plan 记录统一为 Tapper 卡片、圆角、阴影和按钮节奏。
- [x] 保持现有响应式折叠、触控尺寸、焦点和内容溢出规则。
- [x] 运行定向 Vitest、ESLint 与 build，预期无回归。

## Task 5：验证并记录结果

**Files:**

- Modify: `docs/plans/2026-09-03-tapper-library-graph-visual-unification.md`
- Modify: `docs/reviews/2026-09-03-low-code-automation-prototype-review.md`
- Modify: `docs/plans/index.md`

**Produces:** 可复核的自动化与浏览器证据，并把 Plan 更新为 `completed`。

- [x] 运行相关 Vitest、Web 全量 Vitest、lint、format、architecture、build 与 `git diff --check`。
- [x] 对变更 UI 运行一次 Impeccable detector，并处理本范围内的有效问题。
- [x] 在 `http://127.0.0.1:4175/` 批量检查桌面与 `390 × 844`：Tapper 标签、Library 列表、Graph、LCA、Test Management 和 console。
- [x] 将验证结果追加到 Review，把本计划状态更新为 `completed`。

## 完成证据

- TDD 红灯：新增交互测试首次运行 `6 failed / 32 passed`，失败点对应尚未实现的删除标签、单一标识、筛选与图谱交互。
- TDD 绿灯：交互测试 `38 passed`；相关 5 个测试文件 `105 passed`；Web 全量 `14 files / 230 tests passed`。
- 静态与构建：ESLint、Prettier、依赖边界检查和 production build 均通过。
- 浏览器：桌面与 `390 × 844` 已复核 Tapper 上下文、Library All、Knowledge Graph、Low Code Automation 和 Test Management；窄屏 `scrollWidth = 390`，未出现横向溢出。
- Console：仅有 Vite/React 开发信息与 HMR debug，未观察到 error 或 warning。
- Impeccable detector：唯一 advisory 为图谱画布网格；该网格仅存在于实际关系图坐标空间，符合 detector 的保留条件。
- 边界：图数据、布局、关系与执行数据仍是确定性纯前端原型，不代表生产图数据库或真实 Pipeline Agent 执行。

## 2026-09-03 Codex 式模型选择与导航标识补充

本补充是已完成原型上的 bounded follow-up，不新增独立实施 Plan。

- [x] Tapper Composer 底部使用 `当前模型 + 下拉箭头` 触发器，不显示闪电；触发器和菜单的模型名称统一使用 `GPT-` 前缀，菜单不加入 Fast、Ultra 或 reasoning effort。
- [x] 模型选择写入当前 Conversation；新对话默认 `GPT-5.6 Sol`，切换历史 Conversation 后恢复原选择。
- [x] 显示名称调整不改变模型 ID；旧版浏览器快照缺少模型字段时迁移到默认模型，不丢弃既有 Conversation 与资产。
- [x] 一级产品 Rail 的 Tapper 聊天气泡改为 `A`；二级标题保留 `A`，Assistant Turn 不显示该标识。
- [x] 一级与二级导航中的 `A` 徽标统一使用白色背景；一级选中时，紫色只保留在外层导航按钮。
- [x] TDD 红灯为 `5 failed / 71 passed`；实现后的 3 个定向测试文件为 `77 passed`，Web 全量为 `14 files / 234 tests passed`。
- [x] 桌面与 `390 × 844` 浏览器已复核模型菜单、双层导航标识和无横向溢出；Console 无 error 或 warning。
- [x] 当前选择器仅模拟产品状态，不代表已经接入或切换真实 Codex 模型。
- [x] 白色徽标补充的样式契约测试先以 `1 failed` 证明二级徽标仍为紫色，修改后定向测试 `1 passed`；Web 全量保持 `14 files / 234 tests passed`，静态检查与 production build 通过。
- [x] 浏览器实测一级和二级 `A` 背景均为 `rgb(255, 255, 255)`；一级外层选中态保持紫色，取消选中后徽标仍为白色，Console 无 error 或 warning。
- [x] 模型命名与图标补充先以定向测试 `1 failed` 捕获旧名称和闪电，修改后定向测试 `1 passed`、交互测试 `41 passed`、Web 全量 `14 files / 234 tests passed`；静态检查与 production build 通过。
- [x] 桌面与 `390 × 844` 浏览器确认触发器和五个菜单项均使用 `GPT-` 前缀、闪电数量为 `0`，窄屏 `scrollWidth = 390`，Console 无 error 或 warning。
- [x] `New chat` 使用方框铅笔图标、透明背景和普通字重；不改变空白草稿、历史入列或窄屏关闭 Sidebar 的行为。
- [x] Tapper 二级菜单当前项使用整行浅灰圆角背景并移除紫色侧边线；Hover 使用更浅的灰色，键盘 Focus 保留描边。
- [x] 导航补充的定向测试先以 `1 failed` 捕获旧文案，修改后定向测试 `1 passed`、交互测试 `41 passed`、Web 全量 `14 files / 234 tests passed`；ESLint、Prettier、架构边界与 production build 通过。
- [x] 桌面与 `390 × 844` 浏览器确认 `New chat` 默认背景透明、方框铅笔图标数量为 `1`，当前项背景为 `rgb(231, 231, 229)`、圆角为 `12px` 且无侧边阴影；窄屏无横向溢出，Console 无 error 或 warning。
