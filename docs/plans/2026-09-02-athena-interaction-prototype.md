---
status: active
date: 2026-09-02
---

# Athena 交互原型实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不接入新后端的前提下，把 Athena 原型扩展为统一聊天入口、可恢复的页面内会话历史、可引用的 Agent/Skills/Library，以及可搜索的知识来源和知识图谱视图。

**Architecture:** 所有新增数据只保存在 React 页面状态中，刷新后恢复初始 fixture。`TapProductPrototype` 保留产品模块和 Test Management/Low Code Automation 的交付跳转；Athena 子组件负责会话、来源和可配置目录，公共类型与双语文案集中在 prototype model/copy 文件中。

**Tech Stack:** React 19、TypeScript、Ant Design、Vitest、Testing Library、CSS。

**Spec:** 2026-09-02 当前会话中用户已批准的 Athena 原型需求；正式产品 RFC 和总体设计明确推迟到原型确认后更新。

## Global Constraints

- 本轮只交付纯前端交互原型；刷新页面后所有会话、自定义 Agent、Skills 和新增来源重置。
- 默认语言为英文，并可切换中文；寿险新单投保与核保是唯一演示领域。
- `New Chat` 创建新会话，旧会话保留在历史列表并可重新打开；不得删除旧会话内容。
- 建议提示词只填入并聚焦输入框，只有 Send 或 Enter 才发送。
- 左侧是一个可折叠的整合侧栏，不创建第二套并列导航。
- Test Management 与 Low Code Automation 保持现有顶层模块，Test Management 保持 Test Plan/Test Data 子页。
- 右侧 Knowledge sources 表示当前会话的来源范围，支持搜索和勾选；Library 表示完整知识库管理与关系浏览。
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

- [ ] **Step 1: Write failing model and component tests**

```ts
expect(detectIntent("Create BDD tests for underwriting")).toBe("test-plan");
expect(createConversation("chat-2").turns).toEqual([]);
await user.click(screen.getByRole("button", { name: /Create BDD test cases/ }));
expect(screen.getByRole("textbox", { name: "Message Athena" })).toHaveValue(
  "Create BDD test cases for life insurance underwriting",
);
expect(screen.queryByRole("log", { name: "Conversation" })).not.toBeInTheDocument();
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/prototype/model.test.ts src/widgets/tap/TapProductPrototype.interactions.test.tsx`

Expected: FAIL because the model exports and the new interaction behavior do not exist.

- [ ] **Step 3: Implement the typed model and bilingual copy**

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

- [ ] **Step 4: Run model tests until GREEN**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/prototype/model.test.ts`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/widgets/tap/prototype apps/web/src/widgets/tap/TapProductPrototype.interactions.test.tsx apps/web/src/pages/AthenaPage.test.tsx
git commit -m "test: define Athena prototype interactions"
```

### Task 2: Integrated sidebar, conversations, composer context, and sources

**Files:**

- Create: `apps/web/src/widgets/tap/prototype/PrototypeSidebar.tsx`
- Create: `apps/web/src/widgets/tap/prototype/AthenaChat.tsx`
- Create: `apps/web/src/widgets/tap/prototype/KnowledgeSourcesPanel.tsx`
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.tsx`
- Test: `apps/web/src/widgets/tap/TapProductPrototype.interactions.test.tsx`

**Interfaces:**

- Consumes: all Task 1 model and copy exports.
- Produces: accessible `PrototypeSidebar`, `AthenaChat`, and `KnowledgeSourcesPanel` components controlled by the root prototype state.

- [ ] **Step 1: Extend failing tests for sidebar and session restoration**

```ts
await sendMessage(user, "What documents are required for a life policy?");
await user.click(screen.getByRole("button", { name: "New Chat" }));
expect(screen.queryByText("What documents are required for a life policy?")).not.toBeInTheDocument();
await user.click(screen.getByRole("button", { name: "What documents are required" }));
expect(screen.getByText("What documents are required for a life policy?")).toBeVisible();
```

- [ ] **Step 2: Confirm RED**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx`

Expected: FAIL on missing collapse, New Chat/history, source search, and context picker controls.

- [ ] **Step 3: Implement the integrated sidebar and session state**

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

- [ ] **Step 4: Implement composer `+` menu and searchable source panel**

The menu exposes exactly `Add from Library`, `Use Agents`, and `Use Skills`. Each picker filters by name and updates the active conversation context. The right source search only changes visible rows and never changes selection.

- [ ] **Step 5: Run focused tests until GREEN**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx src/pages/AthenaPage.test.tsx`

- [ ] **Step 6: Commit**

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

- [ ] **Step 1: Add failing catalog and Library tests**

```ts
await user.click(screen.getByRole("button", { name: "Agent" }));
await user.click(screen.getByRole("button", { name: "Create Agent" }));
await user.type(screen.getByRole("textbox", { name: "Agent name" }), "Policy Reviewer");
await user.click(screen.getByRole("button", { name: "Save Agent" }));
expect(screen.getByText("Policy Reviewer")).toBeVisible();

await user.click(screen.getByRole("button", { name: "Library" }));
await user.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
expect(screen.getByRole("img", { name: "Life insurance knowledge graph" })).toBeVisible();
```

- [ ] **Step 2: Confirm RED**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx`

- [ ] **Step 3: Implement reusable catalog management**

`CatalogWorkspace` accepts `kind`, localized copy, items, `onCreate`, `onUpdate`, and `onUse`. Search is case-insensitive; create/edit mutations only update page state.

- [ ] **Step 4: Implement Library list and graph modes**

List mode renders source thumbnails/status and search. Graph mode renders an accessible SVG with document and life-insurance concept nodes plus labeled relationships. Add source stores filename/type only in local state.

- [ ] **Step 5: Run focused tests until GREEN**

Run: `corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx src/pages/AthenaPage.test.tsx`

- [ ] **Step 6: Commit**

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
- Produces: Manus-inspired idle/active chat layout, expanded/collapsed desktop sidebar, usable mobile overlay rail, and localized Test Management/Low Code labels.

- [ ] **Step 1: Add any missing regression assertions**

Assert Test Management retains `Test Plan`/`Test Data`, generated BDD can still import, Low Code steps remain editable, and Chinese mode changes visible shell labels without changing saved conversation data.

- [ ] **Step 2: Run focused tests and confirm any new assertion fails before styling/integration**

Run: `corepack pnpm --dir apps/web exec vitest run src/pages/AthenaPage.test.tsx src/widgets/tap/TapProductPrototype.interactions.test.tsx`

- [ ] **Step 3: Implement CSS and final integration**

Use one light integrated sidebar, retain a 44px minimum interaction target, preserve focus-visible states, keep active-chat composer at the bottom, and provide responsive behavior at desktop and 390px widths.

- [ ] **Step 4: Run automated verification**

```bash
corepack pnpm --dir apps/web exec vitest run src/pages/AthenaPage.test.tsx src/widgets/tap/TapProductPrototype.interactions.test.tsx
corepack pnpm --dir apps/web run check
corepack pnpm --dir apps/web test -- --run
git diff --check
```

- [ ] **Step 5: Run one desktop/mobile browser inspection and the Impeccable detector**

Verify 1440×900 and 390×844, then run:

```bash
node .agents/skills/impeccable/scripts/detect.mjs --json apps/web/src/widgets/tap/TapProductPrototype.tsx apps/web/src/widgets/tap/TapProductPrototype.css apps/web/src/widgets/tap/prototype
```

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/widgets/tap apps/web/src/pages/AthenaPage.test.tsx
git commit -m "feat: complete Athena interaction prototype"
```
