# 原型推荐问题交互实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `apps/web` 的 `/prototype` 新对话页设计推荐问题区，替换写死的快捷提问，覆盖设计中的全部 6 个状态并加入状态截图集。

**Architecture:** 新增一个纯展示组件 `PromptSuggestions`（卡片、换一批、骨架）；示例推荐与其依据来源 ID 放在独立的示例数据文件，由根组件按当前可用来源过滤后传入 `TapperChat`。点击卡片由根组件同时写入草稿和追加来源；两个注入状态沿用 `prototypeFaults.ts`。

**Tech Stack:** React、TypeScript、antd、Vitest + Testing Library、Playwright（截图）。

**Spec:** [Tapper 推荐问题设计](../specs/2026-09-30-tapper-prompt-suggestions-design.md)（“界面”一节与“实施顺序”第 2 步）；约束见 [产品原型基准规范](../../guides/2026-09-22-product-prototype-baseline.md)。

## Global Constraints

- 只改 `apps/web/src/widgets/tap/`、`apps/web/tests/e2e/prototype-states.spec.ts`、`docs/guides/2026-09-29-prototype-state-gallery.md` 与 `docs/assets/prototype-states/`；不改 `apps/tap-ai-frontend`、后端和契约。
- 原型只展示交付后的产品界面：不加演示开关或实现说明；加载中与接口失败只能经 `window.__TAP_PROTOTYPE_FAULTS__` / `setPrototypeFaults` 注入。
- 推荐区只在新对话页（`!hasTurns`）显示，位于输入框下方；一次 4 张卡片；推荐总数 > 4 时才显示“换一批”。
- 卡片来源行：单来源 en “Based on {name}” / zh “基于《{name}》”；多来源 en “Based on {name} and {n} more”（n = 总数 − 1）/ zh “基于《{name}》等 {total} 份资料”。
- 点击卡片：问题填入输入框、光标在末尾、不发送；来源按“并集追加、不覆盖、不重复”写入当前对话 `selectedSourceIds`。
- 推荐文本随界面语言切换；中英文案都必须提供（`satisfies Record<Locale, PrototypeCopy>` 强制）。
- 保留原型所有模块与现有交互；删除 `chat.quickPrompts`、`chat.suggestedPrompts` 与 `.tap-quick-prompts` 样式。
- 单测：`corepack pnpm --dir apps/web exec vitest run <path>`；全量：`corepack pnpm --filter @tap/web run test` 与 `make check`。
- 提交用小写祈使句 Conventional Commit，结尾附 `Co-Authored-By` 行。

## Review Focus

1. 推荐依据的来源部分被删除（多来源推荐只剩一个来源）→ 该推荐整条隐藏，而不是显示残缺来源 → Task 1 `hides suggestions whose sources are not all ready`。
2. 已选中的来源再次被推荐追加 → 不产生重复 chip → Task 2 `appends suggestion sources without duplicates`。
3. 换一批在总数不是 4 的倍数时（示例共 6 条）→ 第二批为第 5、6、1、2 条，始终显示 4 张 → Task 1 `rotates to the next batch with wrap-around`。
4. 用户已输入草稿后点击推荐 → 草稿被推荐文本替换（与旧快捷提问一致），不拼接 → Task 2 `replaces the draft and keeps focus at the end`。
5. 切换语言时位于第二批 → 回到第一批，不越界 → Task 1 `resets to the first batch when suggestions change`。

---

## 文件结构

以下路径相对 `apps/web/src/widgets/tap/`。

| 文件 | 变化 |
| --- | --- |
| `prototype/samplePromptSuggestions.ts` | 新建：示例推荐与过滤函数 |
| `prototype/PromptSuggestions.tsx`、`.test.tsx` | 新建：推荐区组件 |
| `prototype/prototypeFaults.ts` | 新增两个注入状态 |
| `prototype/copy.ts` | 新增 `chat.promptSuggestions` 文案；删除快捷提问 key |
| `prototype/TapperChat.tsx` | 用推荐区替换快捷提问 |
| `TapProductPrototype.tsx` | 计算可用推荐，实现点击填入与追加来源 |
| `TapProductPrototype.css` | 推荐卡片与骨架样式，删除 `.tap-quick-prompts` |
| `TapperChatControls.test.tsx` | 根组件级交互测试 |

---

### Task 1: 推荐区组件与示例数据

**Files:**
- Create: `prototype/samplePromptSuggestions.ts`、`prototype/PromptSuggestions.tsx`、`prototype/PromptSuggestions.test.tsx`
- Modify: `prototype/prototypeFaults.ts`、`prototype/prototypeFaults.test.ts`、`prototype/copy.ts`、`TapProductPrototype.css`

**Interfaces:**
- Produces（`samplePromptSuggestions.ts`）：
  ```ts
  export interface PromptSuggestion { readonly id: string; readonly question: string; readonly sources: readonly { id: string; name: string }[] }
  export function availablePromptSuggestions(locale: Locale, readySources: readonly LibrarySource[]): readonly PromptSuggestion[]
  ```
  内部常量 `SAMPLE_PROMPT_SUGGESTIONS` 共 6 条，每条 `{ id, question: Record<Locale, string>, sourceIds }`，依据来源必须是现有示例 ID：`underwriting-v12`、`health-disclosure-approved`、`premium-rates-xlsx`、`sample-underwriting`、`sample-beneficiary`、`sample-test-cases`；至少 2 条为多来源。问题写成这些文档能回答的具体问题（如 “What evidence is required for applicants over 60 in the life underwriting guide?”）。`availablePromptSuggestions` 只保留全部 `sourceIds` 都在 `readySources`（`status === "ready"`）中的条目，来源名取自 `readySources`。
- Produces（`PromptSuggestions.tsx`）：
  ```ts
  export interface PromptSuggestionsProps { copy: PrototypeCopy; suggestions: readonly PromptSuggestion[]; onPick(suggestion: PromptSuggestion): void }
  export function PromptSuggestions(props): JSX.Element | null
  ```
  组件自己读取注入状态：`suggestions-loading` → 渲染 4 个骨架（antd `Skeleton.Button active block`，容器 `aria-busy="true"`，`aria-label` 为 `copy.chat.promptSuggestions.label`）；`suggestions-load-failed` 或 `suggestions` 为空 → 返回 `null`，不渲染任何提示。正常时容器为 `role="group"`、`aria-label={copy.chat.promptSuggestions.label}`，内含 4 个 `<button>`，按钮可访问名以问题文本开头。`batchStart` 状态在 `suggestions` 引用变化时重置为 0。
- Produces（`prototypeFaults.ts`）：`PrototypeFault` 与 `KNOWN_FAULTS` 增加 `"suggestions-loading"`、`"suggestions-load-failed"`，均为 load 类（`isPrototypeFaultActive` 读取）。
- Produces（`copy.ts`）：`PrototypeCopy.chat.promptSuggestions: { label: string; refresh: string; basedOnOne(name: string): string; basedOnMany(name: string, total: number): string }`。en：label “Suggested questions”、refresh “Show others”；zh：label “推荐问题”、refresh “换一批”；来源句式见 Global Constraints。本任务只新增 key，快捷提问 key 在 Task 2 随引用一起删除。

- [ ] **Step 1: 写失败测试** `prototype/PromptSuggestions.test.tsx` 与 `prototypeFaults.test.ts` 追加：

```tsx
it("shows four cards with source attribution", () => { /* 6 条建议 → group "Suggested questions" 内 4 个按钮；单来源卡片含 "Based on Life underwriting guide · v1.2.md"；多来源卡片含 "Based on … and 1 more" */ });
it("rotates to the next batch with wrap-around", () => { /* 点击 "Show others" → 显示第 5、6、1、2 条 */ });
it("hides the refresh action with four or fewer suggestions", () => { /* 4 条 → 无 "Show others" */ });
it("resets to the first batch when suggestions change", () => { /* 换一批后 rerender 新数组 → 显示新数组前 4 条 */ });
it("calls onPick with the chosen suggestion", () => {});
it("renders nothing when there are no suggestions", () => { /* container 为空 */ });
it("shows four skeletons while suggestions load", () => { /* setPrototypeFaults(["suggestions-loading"]) → aria-busy="true"，4 个骨架，无按钮 */ });
it("renders nothing when suggestions fail to load", () => { /* "suggestions-load-failed" → 无 group、无 role="alert" */ });
it("hides suggestions whose sources are not all ready", () => { /* availablePromptSuggestions("en", 去掉 premium-rates-xlsx 的来源) 不含依赖它的条目；全部来源为空 → [] */ });
it("follows the interface language", () => { /* availablePromptSuggestions("zh", …) 返回中文问题 */ });
```

- [ ] **Step 2: 运行确认失败**：`corepack pnpm --dir apps/web exec vitest run src/widgets/tap/prototype/PromptSuggestions.test.tsx src/widgets/tap/prototype/prototypeFaults.test.ts`，预期 FAIL（模块不存在）。
- [ ] **Step 3: 实现** 上述文件；卡片样式 `.tap-prompt-suggestions`（两列网格，窄屏一列）、`.tap-prompt-suggestion`（问题文本 + 下方 `.tap-prompt-suggestion-source` 小字、单行省略），沿用 `.tap-quick-prompts button` 的配色与 focus-visible 规则。
- [ ] **Step 4: 运行确认通过**，并运行 `tsc`（`corepack pnpm --dir apps/web exec tsc -b`）。
- [ ] **Step 5: Commit**：`feat: add prompt suggestion cards to the prototype`

---

### Task 2: 新对话页接入推荐区

**Files:**
- Modify: `prototype/TapperChat.tsx:469-472, 1241-1256`（`fillPrompt` 与快捷提问区）、`TapProductPrototype.tsx`（`sources` 计算附近与 `<TapperChat>` 属性 1562–1672）、`prototype/copy.ts`、`TapProductPrototype.css:684-715`
- Test: `TapperChatControls.test.tsx`

**Interfaces:**
- Consumes: Task 1 的 `availablePromptSuggestions`、`PromptSuggestions`、`PromptSuggestion`。
- Produces: `TapperChatProps` 新增 `promptSuggestions: readonly PromptSuggestion[]` 与 `onPickSuggestion(suggestion: PromptSuggestion): void`；删除 `fillPrompt`。根组件的 `pickSuggestion` 调用 `setMessageDraft(suggestion.question)` 并以 `updateActiveConversation` 把来源 ID 并集追加到 `selectedSourceIds`；`TapperChat` 在点击后聚焦 `composerRef` 并把光标设到末尾（在 `message` 更新后的 effect 中消费一个 `caretToEndRef` 标记，调用 `setSelectionRange(len, len)`）。

- [ ] **Step 1: 写失败测试**（`TapperChatControls.test.tsx`，沿用文件内 `composer()`、`selectUnderwritingSource()` 等 helper）：

```tsx
it("replaces the draft and keeps focus at the end", () => {
  /* type("draft") → 点击第一张推荐 → composer() 值为该问题、document.activeElement === composer()、selectionStart === selectionEnd === 值长度；未发送（无新 turn） */
});
it("selects the suggestion sources when none are selected", () => { /* 点击多来源推荐 → 右侧面板 "2 selected"，输入区出现两个来源 chip */ });
it("appends suggestion sources without duplicates", () => { /* selectUnderwritingSource() → 点击依据含 "Underwriting test rules.pdf" 与另一来源的推荐 → "2 selected"，"Underwriting test rules.pdf" chip 只出现一次 */ });
it("hides suggestions once the conversation starts", () => { /* send(问题) 后无 group "Suggested questions" */ });
it("shows suggestions in the interface language", () => { /* 切换中文 → group "推荐问题"，卡片为中文问题 */ });
it("hides suggestions after every source is deleted", () => { /* 以 localStorage 预置快照 removedSourceIds = 全部来源 ID（参照 prototype-states.spec.ts 的 baseSnapshot），render → 无 group */ });
```
同时删除旧快捷提问相关断言（如有）。

- [ ] **Step 2: 运行确认失败**：`corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapperChatControls.test.tsx`。
- [ ] **Step 3: 实现**：根组件以 `useMemo(() => availablePromptSuggestions(locale, readySources), [locale, readySources])` 计算（`readySources` 即当前传给 `TapperChat` 的 ready 来源）；`TapperChat` 在 `!hasTurns` 时于 `{composer}` 之后渲染 `<PromptSuggestions>`；删除 `quickPrompts`/`suggestedPrompts` copy 与 `.tap-quick-prompts*` 样式，`.tap-chat--idle > .tap-prompt-suggestions` 保持与输入框同宽。
- [ ] **Step 4: 全量测试与 `tsc -b` 通过**：`corepack pnpm --filter @tap/web run test`。
- [ ] **Step 5: Commit**：`feat: show prompt suggestions on the prototype new chat page`

---

### Task 3: 状态截图集

**Files:**
- Modify: `apps/web/tests/e2e/prototype-states.spec.ts`、`docs/guides/2026-09-29-prototype-state-gallery.md`
- Create: `docs/assets/prototype-states/states/e01…e07-*.png`；更新 `before/`、`after/` 下的 `tapper-new-chat.png`

**Interfaces:**
- Consumes: Task 1 的两个注入状态名；spec 中现有 helper `capture`、`openWithFaults`、`openFresh`、`openWithSnapshot`、`baseSnapshot`。

- [ ] **Step 1: 在 spec 的 `states` 套件新增分组 E**（测试名即截图名）：
  - `e01-suggestions-default`：`openFresh`，截新对话页。
  - `e02-suggestions-next-batch`：点击 “Show others” 后截图。
  - `e03-suggestions-zh`：切换中文后截图。
  - `e04-suggestions-empty`：`openWithSnapshot(baseSnapshot(全部来源 ID))`，断言无 “Suggested questions”，截图。
  - `e05-suggestions-loading`：`openWithFaults(page, …, ["suggestions-loading"])`。
  - `e06-suggestions-load-failed`：`openWithFaults(page, …, ["suggestions-load-failed"])`，断言无推荐区且无 alert。
  - 另加 `e07-suggestion-picked`：点击一张多来源推荐后截图（显示填入的问题与来源 chip）。
- [ ] **Step 2: 生成截图**：`TAP_PROTOTYPE_CAPTURE_SET=states TAP_PROTOTYPE_CAPTURE_DIR=../../docs/assets/prototype-states corepack pnpm --dir apps/web exec playwright test -c playwright.prototype.config.ts -g "e0"`，预期 7 passed、生成 7 张 PNG；再以 `TAP_PROTOTYPE_CAPTURE_SET=after` 重拍 `tapper-new-chat`，与 `before/tapper-new-chat.png` 对比确认只有推荐区变化。
- [ ] **Step 3: 更新截图集指南**：新增 `## E：推荐问题`，表格列沿用 `| 编号 | 交互 | 触发方式 | 截图 |`；e01–e03、e07 为“自然”，e04 为“自然（`localStorage` 预置终态，见上）”，e05、e06 为“注入 · `suggestions-loading` / `suggestions-load-failed`”。预览 Markdown 确认链接可用。
- [ ] **Step 4: `git diff --check`，`make check` 通过。**
- [ ] **Step 5: Commit**：`docs: capture prompt suggestion states in the prototype gallery`
