---
status: completed
date: 2026-09-02
completed: 2026-09-04
---

# Athena 交互原型实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不接入新后端的前提下，把 Athena 原型扩展为统一聊天入口、可恢复的页面内会话历史、可引用的 Agent/Skills/Library，以及可搜索的知识来源和知识图谱视图。

**Architecture:** Conversation 以 React 状态作为运行时事实源，并通过版本化浏览器本地存储模拟刷新恢复；损坏或不兼容快照安全回退到初始空白会话。`TapProductPrototype` 保留产品模块和 Test Management/Low Code Automation 的交付跳转；Athena 子组件负责会话、来源和可配置目录，公共类型与双语文案集中在 prototype model/copy 文件中。

**Tech Stack:** React 19、TypeScript、Ant Design、Vitest、Testing Library、CSS。

**Spec:** [RFC-008：TAP 产品壳层与 Low Code Automation 交互原型](../proposals/2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md)。本 Plan 只实施已经获批的原型范围；RFC 中仍为 `proposed` 或 `unresolved` 的条目不得被当作实施授权。

**Scope update (2026-09-03):** 本 Plan 继续拥有 Athena 壳层、会话、Catalog、Library 来源和双语交互；旧的单草稿 Low Code 编辑器验收已由 [Low Code Automation 交互原型实施计划](2026-09-03-low-code-automation-interaction-prototype.md) 取代，不得恢复为当前产品要求。用户随后确认采用行业通用 Conversation 历史：首轮后入列、当前项可见高亮、同会话多轮合并，并在跨模块与刷新后恢复。Athena 左右面板进一步统一为 Codex 式面板按钮换位交互，聊天视口增加由用户问题生成的导航轨。

## 完成与验证记录（2026-09-04）

四个 Task 均已在当前前端原型实现；Low Code Automation 的后续扩展继续由 successor Plan 管理，Mobile/Azure DevOps 历史模拟不重新进入本 Plan。完成状态经本次鲜活验证确认：

```bash
corepack pnpm --dir apps/web exec vitest run \
  src/widgets/tap/prototype/model.test.ts \
  src/widgets/tap/TapProductPrototype.interactions.test.tsx \
  src/pages/AthenaPage.test.tsx
# 3 files, 113 tests passed

corepack pnpm --dir apps/web run check
# ESLint、Prettier、architecture、TypeScript、Vite build passed

corepack pnpm --dir apps/web test -- --run
# 14 files, 246 tests passed
```

1440×900 与 390×844 浏览器检查未发现裁切、重叠或页面横向溢出。Impeccable detector 只报告 Knowledge Graph canvas 的网格背景 advisory；该用法属于图谱画布语义，不是阻塞项。

## Global Constraints

- 本轮只交付纯前端交互原型；Conversation 和其原型资产引用使用版本化 `localStorage` 模拟刷新恢复，不能宣称已经具备服务端持久化。自定义 Catalog 与真实权限仍不在本轮持久化范围。
- 默认语言为英文，并可切换中文；寿险新单投保与核保是唯一演示领域。
- `New Chat` 创建空白草稿；发送首条消息后才新增一条历史记录并用首条消息生成标题。旧会话和当前会话都保留在历史列表，当前项高亮且可重新打开；同一会话后续消息不得拆成新历史项。
- Athena 输入框为空且不处于 IME 组字时，按上方向键填入当前 Conversation 最近一次已发送内容，但不自动发送；已有草稿、无历史或其他 Conversation 的消息不得被覆盖或跨会话召回。
- 建议提示词只填入并聚焦输入框，只有 Send 或 Enter 才发送。
- 最左 `68px` 一级产品 Rail 始终保留；仅收起或展开其右侧的 Athena 上下文 Sidebar。展开时按钮位于 Sidebar 标题区，收起后同一 Codex 式面板图标移动到工作区左上角用于恢复。图标按当前状态改变对应侧面积：收起态仅保留窄线，展开态显示更宽的面板区域，左右两侧严格镜像。移动端使用遮罩抽屉，并保留 Escape、背景 inert、滚动锁定和焦点恢复。
- Athena 上下文 Sidebar 采用 Codex 式连续菜单节奏；`New chat`、Agent、Skills、Library 共用同一行高与相邻间距，不为 `New chat` 增加额外分组间距。
- 产品一级 Athena 入口是页面中唯一的 `A` 品牌徽标；Athena 二级栏标题只显示文字，不再重复 `A`。
- Test Management 与 Low Code Automation 保持现有顶层模块，Test Management 保持 Test Plan/Test Data 子页。
- 右侧 Knowledge sources 表示当前会话的来源范围，支持搜索、勾选和独立收展；展开按钮与左栏采用同一视觉语法并在右侧镜像，收起后聊天区释放其列宽，窄屏改为右侧遮罩抽屉。左右面板共用 `200ms` 指数缓出过渡，固定内容尺寸并由外层裁切，不叠加透明度动画；减弱动态效果时取消过渡。Library 表示完整知识库管理与关系浏览。
- 有 Turn 时，聊天视口左缘按每条用户问题生成一个 Codex 式横线 minimap。刻度组在输入框上方的 Transcript 行内垂直居中，每项使用 `14px` 节拍；默认短线为 `12 × 4px`、`#dbdbdb`，全部固定在距聊天区左缘 `14px` 的同一左锚点。滚动对应的当前项保持 `12 × 4px`，只加深为 `#8a8a8a`，不得让邻近刻度永久扩散。仅在 Hover/Focus 时，以目标项为中心按 `12 / 14 / 18 / 24 / 34 / 24 / 18 / 14 / 12px` 形成向右伸出的紧凑鱼眼放大，目标项为 `#222529` 并显示问题预览。点击平滑跳转到对应 Turn，当前项随点击与滚动位置更新。问题数超过可用容量时进入固定高度滑动窗口：按 Transcript 行从顶部到 Composer 上沿的真实高度动态计算奇数槽位，与 Composer 至少保留 `32px` 间距；当前项居中跟随，上下渐隐续接显示隐藏数量并支持翻页，minimap 滚轮每次移动三个问题。只挂载窗口内的问题按钮，不压缩刻度、不向屏外或输入框区域增长，也不产生第二条滚动条。
- Agent、Skills 和 Library 都可从 composer 的 `+` 菜单搜索并引用。
- 不新增网络请求、持久化、真实 Agent 执行或真实知识图谱计算。

---

### Task 1: Prototype model and interaction contract

**Files:**

- Create: `apps/web/src/widgets/tap/prototype/model.ts`
- Create: `apps/web/src/widgets/tap/prototype/copy.ts`
- Create: `apps/web/src/widgets/tap/prototype/model.test.ts`
- Test: `apps/web/src/widgets/tap/TapProductPrototype.interactions.test.tsx`

**Interfaces:**

- Produces: `Locale`, `ProductModule`, `AthenaSurface`, `AssistantIntent`, `AssistantTurn`, `Conversation`, `CatalogItem`, `LibrarySource`, `detectIntent`, `createConversation`, `appendTurn`, `PROTOTYPE_COPY`.
- `Conversation` owns its turns, selected source IDs, selected Agent IDs and selected Skill IDs so history restoration is lossless.

- [x] **Step 1: Write failing model and component tests**

```ts
expect(detectIntent("Create BDD tests for underwriting")).toBe("test-plan");
expect(createConversation("chat-2").turns).toEqual([]);
await user.click(screen.getByRole("button", { name: /Create BDD test cases/ }));
expect(screen.getByRole("textbox", { name: "Message Athena" })).toHaveValue(
  "Create BDD test cases for life insurance underwriting",
);
expect(
  screen.queryByRole("log", { name: "Conversation" }),
).not.toBeInTheDocument();
```

- [x] **Step 2: Run the tests and confirm RED**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/prototype/model.test.ts src/widgets/tap/TapProductPrototype.interactions.test.tsx`

Expected: FAIL because the model exports and the new interaction behavior do not exist.

- [x] **Step 3: Implement the typed model and bilingual copy**

```ts
export type Locale = "en" | "zh";
export type AthenaSurface = "chat" | "agents" | "skills" | "library";

export interface Conversation {
  id: string;
  title: string;
  turns: readonly AssistantTurn[];
  selectedSourceIds: readonly string[];
  selectedAgentIds: readonly string[];
  selectedSkillIds: readonly string[];
}
```

- [x] **Step 4: Run model tests until GREEN**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/prototype/model.test.ts`

- [x] **Step 5: Commit**

```bash
git add apps/web/src/widgets/tap/prototype apps/web/src/widgets/tap/TapProductPrototype.interactions.test.tsx apps/web/src/pages/AthenaPage.test.tsx
git commit -m "test: define Athena prototype interactions"
```

### Task 2: Product rail, Athena contextual sidebar, conversations, composer context, and sources

**Files:**

- Create: `apps/web/src/widgets/tap/prototype/PrototypeSidebar.tsx`
- Create: `apps/web/src/widgets/tap/prototype/AthenaChat.tsx`
- Create: `apps/web/src/widgets/tap/prototype/KnowledgeSourcesPanel.tsx`
- Create: `apps/web/src/widgets/tap/prototype/PanelToggleIcon.tsx`
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.tsx`
- Test: `apps/web/src/widgets/tap/TapProductPrototype.interactions.test.tsx`

**Interfaces:**

- Consumes: all Task 1 model and copy exports.
- Produces: accessible product rail, contextual `PrototypeSidebar`, `AthenaChat`, shared `PanelToggleIcon`, and independently collapsible `KnowledgeSourcesPanel` components controlled by the root prototype state.

- [x] **Step 1: Extend failing tests for sidebar and session restoration**

```ts
await sendMessage(user, "What documents are required for a life policy?");
await user.click(screen.getByRole("button", { name: "New Chat" }));
expect(
  screen.queryByText("What documents are required for a life policy?"),
).not.toBeInTheDocument();
await user.click(
  screen.getByRole("button", { name: "What documents are required" }),
);
expect(
  screen.getByText("What documents are required for a life policy?"),
).toBeVisible();
```

- [x] **Step 2: Confirm RED**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx`

Expected: FAIL on missing collapse, New Chat/history, source search, and context picker controls.

- [x] **Step 3: Implement the integrated sidebar and session state**

```tsx
<PrototypeSidebar
  collapsed={sidebarCollapsed}
  conversations={conversations}
  activeConversationId={activeConversationId}
  onNewChat={createNewChat}
  onSelectConversation={setActiveConversationId}
/>
```

The root appends turns only to the active conversation. New Chat creates a new empty conversation and leaves every prior conversation unchanged.

- [x] **Step 4: Implement composer `+` menu and searchable source panel**

The menu exposes exactly `Add from Library`, `Use Agents`, and `Use Skills`. Each picker filters by name and updates the active conversation context. The right source search only changes visible rows and never changes selection.

- [x] **Step 5: Add Codex-style panel toggles and the question navigator**

Use one authored outline icon with left/right and expanded/collapsed variants. The collapsed variant compresses the corresponding side to a narrow divider; the expanded variant shows a visibly wider pane area. Keep the product Rail mounted; move the left toggle between the Athena header and workspace edge, and move the mirrored right toggle between Knowledge sources and the chat edge. Preserve source selection across collapse. Drive both panels with the same `200ms` exponential ease-out, transform, clipping and reduced-motion rules without opacity cross-fades. Generate one left-edge horizontal marker per Turn in a vertically centered `14px`-pitch stack. Keep every default marker `12 × 4px` on the same `14px` left anchor, darken only the scroll-active marker, and derive the `14 / 18 / 24 / 34px` rightward fisheye solely from the Hover/Focus target. Expose the full prompt on Hover/Focus, hide native and minimap scrollbars, and use `scrollIntoView` with a reduced-motion fallback. Measure the Transcript row above the Composer with `ResizeObserver` plus a window-resize fallback, derive an odd slot capacity after a `64px` vertical safety inset, center the rail within that boundary, and reserve two continuation slots once overflow begins. Keep the rail at least `32px` above the Composer and recompute it when either region changes size. Keep only the visible Turn buttons mounted; recenter the window from transcript scroll, shift three nodes per minimap wheel event, and page by the visible count from the faded continuation controls.

- [x] **Step 6: Run focused tests until GREEN**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx src/pages/AthenaPage.test.tsx`

- [x] **Step 7: Commit**

```bash
git add apps/web/src/widgets/tap
git commit -m "feat: add Athena conversations and context controls"
```

### Task 3: Agent, Skills, and Library workspaces

**Files:**

- Create: `apps/web/src/widgets/tap/prototype/CatalogWorkspace.tsx`
- Create: `apps/web/src/widgets/tap/prototype/LibraryWorkspace.tsx`
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.tsx`
- Test: `apps/web/src/widgets/tap/TapProductPrototype.interactions.test.tsx`

**Interfaces:**

- Consumes: Task 1 catalog/source types and controlled arrays from `TapProductPrototype`.
- Produces: searchable create/edit catalog workspaces and a Library list/graph workspace with an in-memory add-source action.

- [x] **Step 1: Add failing catalog and Library tests**

```ts
await user.click(screen.getByRole("button", { name: "Agent" }));
await user.click(screen.getByRole("button", { name: "Create Agent" }));
await user.type(
  screen.getByRole("textbox", { name: "Agent name" }),
  "Policy Reviewer",
);
await user.click(screen.getByRole("button", { name: "Save Agent" }));
expect(screen.getByText("Policy Reviewer")).toBeVisible();

await user.click(screen.getByRole("button", { name: "Library" }));
await user.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
expect(
  screen.getByRole("img", { name: "Life insurance knowledge graph" }),
).toBeVisible();
```

- [x] **Step 2: Confirm RED**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx`

- [x] **Step 3: Implement reusable catalog management**

`CatalogWorkspace` accepts `kind`, localized copy, items, `onCreate`, `onUpdate`, and `onUse`. Search is case-insensitive; create/edit mutations only update page state.

- [x] **Step 4: Implement Library list and graph modes**

List mode renders source thumbnails/status and search. Graph mode renders an accessible SVG with document and life-insurance concept nodes plus labeled relationships. Add source stores filename/type only in local state.

- [x] **Step 5: Run focused tests until GREEN**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx src/pages/AthenaPage.test.tsx`

- [x] **Step 6: Commit**

```bash
git add apps/web/src/widgets/tap
git commit -m "feat: add Athena catalogs and knowledge graph"
```

### Task 4: Responsive presentation and final verification

**Files:**

- Modify: `apps/web/src/widgets/tap/TapProductPrototype.css`
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.tsx`
- Modify: `apps/web/src/pages/AthenaPage.test.tsx`

**Interfaces:**

- Consumes: all prior components and state.
- Produces: Manus-inspired idle/active chat layout, fixed desktop product rail, expanded/collapsed Athena contextual sidebar, usable mobile overlay drawer, and localized Test Management/Low Code labels.

- [x] **Step 1: Add any missing regression assertions**

Assert Test Management retains `Test Plan`/`Test Data`, generated BDD can still import, the Low Code product entry remains reachable, and Chinese mode changes visible shell labels without changing saved conversation data. Low Code asset/editor behavior is verified only by the successor Plan linked above.

- [x] **Step 2: Run focused tests and confirm any new assertion fails before styling/integration**

Run: `corepack pnpm --dir apps/web exec vitest run src/pages/AthenaPage.test.tsx src/widgets/tap/TapProductPrototype.interactions.test.tsx`

- [x] **Step 3: Implement CSS and final integration**

Use a light product rail plus Athena contextual sidebar, retain a 44px minimum interaction target for ordinary controls, preserve focus-visible states, keep active-chat composer at the bottom, and provide responsive behavior at desktop and 390px widths. The desktop minimap navigator is the deliberate dense-control exception: it keeps the separately specified 14px pitch and exposes its prompt through Hover/Focus rather than expanding every target to 44px.

- [x] **Step 4: Run automated verification**

```bash
corepack pnpm --dir apps/web exec vitest run src/pages/AthenaPage.test.tsx src/widgets/tap/TapProductPrototype.interactions.test.tsx
corepack pnpm --dir apps/web run check
corepack pnpm --dir apps/web test -- --run
git diff --check
```

- [x] **Step 5: Run one desktop/mobile browser inspection and the Impeccable detector**

Verify 1440×900 and 390×844, then run:

```bash
node .agents/skills/impeccable/scripts/detect.mjs --json apps/web/src/widgets/tap/TapProductPrototype.tsx apps/web/src/widgets/tap/TapProductPrototype.css apps/web/src/widgets/tap/prototype
```

- [x] **Step 6: Commit**

```bash
git add apps/web/src/widgets/tap apps/web/src/pages/AthenaPage.test.tsx
git commit -m "feat: complete Athena interaction prototype"
```
