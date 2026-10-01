# TAP AI 前端推荐问题实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apps/tap-ai-frontend` 的新对话页按 `/prototype` 的设计显示由 `GET /prompt-suggestions` 返回的推荐问题。

**Architecture:** 在 `KnowledgeClient` 增加 `listPromptSuggestions(locale)`，以 TanStack Query 钩子读取；新组件 `PromptSuggestions` 按原型实现卡片、换一批与骨架，由 `TapperChat` 在新对话页渲染；点击由 `TapperWorkspace` 写入草稿并追加来源。接口失败时静默隐藏。

**Tech Stack:** React 19、TypeScript、antd、TanStack Query、openapi-fetch、Vitest + Testing Library、Playwright。

**Spec:** [Tapper 推荐问题设计](../specs/2026-09-30-tapper-prompt-suggestions-design.md)（“界面”与“实施顺序”第 4 步）。前置：[原型推荐问题交互](2026-09-30-prototype-prompt-suggestions.md) 与 [推荐问题后端](2026-09-30-prompt-suggestions-backend.md) 已合入（`schema.ts` 已含 `prompt_suggestion_list`）。

## Global Constraints

- 路径相对 `apps/tap-ai-frontend/`；只改本应用与 `docs/`，不改 `apps/web`、后端和契约。
- 交互、文案、状态与原型一致：一次 4 张卡片、总数 > 4 才显示“换一批”、来源句式、点击填入不发送、光标在末尾、来源并集追加。实现前先打开原型 `/prototype` 与状态截图集 E 组对照；不从原型复制或共享组件代码，按原型重新实现。
- 组件与文案不得包含 prototype / demo / 示例字样（`copy.test.ts` 守卫）。
- 接口失败、项目未就绪（`projectId === null`）或返回空列表时推荐区不渲染，不报错、不写 `console.error`/`console.warn`（`src/shared/testing/setup.ts` 会使测试失败）。
- 推荐语言跟随界面 `locale`；切换语言即请求另一语言。
- 单测：`corepack pnpm --dir apps/tap-ai-frontend exec vitest run <path>`；全量：`corepack pnpm --filter @tap/ai-frontend exec vitest run` 与 `corepack pnpm --dir apps/tap-ai-frontend exec tsc -b`；收尾 `make check`。
- 提交用小写祈使句 Conventional Commit，结尾附 `Co-Authored-By` 行。

## Review Focus

1. 推荐的来源 ID 不在当前 `sources` 列表中（后端缓存与来源列表刷新不同步）→ 仍填入问题并追加来源 ID，chip 只显示能解析的来源，不崩溃 → Task 2 `keeps working when a suggestion source is not listed yet`。
2. 推荐加载完成前用户已开始输入 → 加载完成不覆盖草稿 → Task 2 `does not touch the draft when suggestions arrive`。
3. `ui-capture.spec.ts` 的假后端对未知路径抛错 → 新请求必须在每个路由处理块中有分支，否则截图流程失败 → Task 3。
4. 切换语言时旧语言请求仍在进行 → 只显示当前语言结果（查询键含 locale）→ Task 1 `keys suggestions by locale`。
5. 项目切换 → 不显示上一个项目的推荐（查询键含 projectId）→ Task 1 `keys suggestions by project`。

---

### Task 1: 客户端方法与查询钩子

**Files:**
- Modify: `src/features/knowledge/api/types.ts`（类型别名与 `KnowledgeClient` L56–167）、`src/features/knowledge/api/client.ts`（参照 `listPublishedSources` L423–432）、`src/features/knowledge/api/queries.tsx`（`knowledgeKeys` L91–107）、`src/features/knowledge/testing/fakeKnowledgeClient.ts`
- Test: `src/features/knowledge/api/queries.test.tsx`（若无则新建）、`src/features/knowledge/api/client.test.ts`（现有客户端测试文件）

**Interfaces:**
- Produces:
  ```ts
  export type PromptSuggestionPage = components["schemas"]["PromptSuggestionPage"];
  export type PromptSuggestionItem = components["schemas"]["PromptSuggestionItem"];
  // KnowledgeClient
  listPromptSuggestions(locale: Locale, signal?: AbortSignal): Promise<PromptSuggestionPage>;
  // knowledgeKeys
  promptSuggestions: (projectId: string | null, locale: Locale) => ["knowledge", projectId, "prompt-suggestions", locale] as const
  export function usePromptSuggestionsQuery(projectId: string | null, locale: Locale): UseQueryResult<PromptSuggestionPage>
  // FakeKnowledgeClient
  withPromptSuggestions(locale: Locale, page: PromptSuggestionPage): FakeKnowledgeClient;
  withPromptSuggestionsProblem(): FakeKnowledgeClient;   // listPromptSuggestions 以 KnowledgeClientError 拒绝
  readonly promptSuggestionCalls: readonly Locale[];
  ```
  `Locale` 复用 `src/widgets/tap/workspace/model.ts` 的定义（若 features 层不能依赖 widgets，则在 `types.ts` 定义 `export type SuggestionLocale = "en" | "zh"` 并让 workspace 的 `Locale` 与之兼容——以现有 architecture 检查为准）。钩子：`enabled` 同 `usePublishedSourcesQuery`；`retry: false`；`staleTime: 60_000`。未配置的 locale 在 fake 中返回 `{ items: [] }`。

- [ ] **Step 1: 写失败测试**

```ts
it("requests prompt suggestions with the locale", async () => { /* 用现有 client 测试的 fetch 替身：请求 GET /api/v1/projects/project-test/prompt-suggestions?locale=zh */ });
it("keys suggestions by locale", async () => { /* renderHook：locale en → zh，fake.promptSuggestionCalls == ["en","zh"]，data 为 zh 页 */ });
it("keys suggestions by project", () => { /* knowledgeKeys.promptSuggestions("a","en") 与 ("b","en") 不相等 */ });
it("does not query without a project", () => { /* projectId null → 无调用 */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过，`tsc -b` 通过**。
- [ ] **Step 5: Commit**：`feat: load prompt suggestions in tap ai`

---

### Task 2: 推荐区组件与新对话页接入

**Files:**
- Create: `src/widgets/tap/workspace/PromptSuggestions.tsx`、`PromptSuggestions.test.tsx`
- Modify: `src/widgets/tap/workspace/TapperChat.tsx`（`TapperChatProps` L50–75、`{composer}` 渲染处约 L1088）、`src/widgets/tap/TapperWorkspace.tsx`（`messageDraft` L1052、`updateActiveConversation` L1621、`<TapperChat>` 属性约 L2072–2139）、`src/widgets/tap/workspace/copy.ts`、`src/widgets/tap/TapperWorkspace.css`、`src/pages/TapAiPage.test.tsx:53`
- Test: `src/widgets/tap/TapperWorkspace.chatControls.test.tsx`

**Interfaces:**
- Consumes: Task 1 `usePromptSuggestionsQuery`、`PromptSuggestionItem`、`fakeKnowledgeClient().withPromptSuggestions(...)`。
- Produces:
  ```ts
  export interface PromptSuggestionsProps {
    copy: WorkspaceCopy;
    state: { kind: "loading" } | { kind: "ready"; items: readonly PromptSuggestionItem[] } | { kind: "hidden" };
    onPick(item: PromptSuggestionItem): void;
  }
  export function PromptSuggestions(props: PromptSuggestionsProps): JSX.Element | null
  ```
  - 文案 `WorkspaceCopy.chat.promptSuggestions`：与原型相同的 `label`、`refresh`、`basedOnOne(name)`、`basedOnMany(name, total)`（en “Suggested questions” / “Show others” / “Based on {name}” / “Based on {name} and {n} more”；zh “推荐问题” / “换一批” / “基于《{name}》” / “基于《{name}》等 {total} 份资料”），来源名取接口返回的 `sources[].name`。
  - 状态映射在 `TapperChat`：`projectId === null` 或 `isError` 或 `items` 为空 → `hidden`；`isPending` → `loading`（4 个 antd `Skeleton.Button active block`，容器 `aria-busy="true"`）；否则 `ready`。只在 `!hasTurns` 时调用渲染。
  - `TapperChatProps` 新增 `locale: Locale` 与 `onPickSuggestion(item: PromptSuggestionItem): void`。`TapperWorkspace` 的处理：`setMessageDraft(item.question)`、`updateActiveConversation(c => ({ ...c, selectedSourceIds: 并集(c.selectedSourceIds, item.sources.map(s => s.sourceId)) }))`；`TapperChat` 点击后聚焦 `composerRef` 并把光标移到末尾（与原型相同的 “message 更新后 effect 消费标记” 做法）。
  - `TapAiPage.test.tsx:53` 的回归断言改为：无旧写死提问文本；有推荐时 group 名为 “Suggested questions”。

- [ ] **Step 1: 写失败测试**

```tsx
// PromptSuggestions.test.tsx
it("shows four cards and rotates with wrap-around", () => {});      // 6 条 → 4 张；Show others → 第 5、6、1、2 条
it("hides the refresh action with four or fewer suggestions", () => {});
it("shows four skeletons while loading", () => {});
it("renders nothing when hidden", () => {});
it("attributes one or many sources", () => {});                      // “Based on A”、“Based on A and 1 more”
// TapperWorkspace.chatControls.test.tsx（fakeKnowledgeClient().withPublishedSources(PUBLISHED).withPromptSuggestions("en", …)）
it("fills the composer and appends sources when a suggestion is picked", async () => {}); // 已选 1 个来源 → 点击含该来源与另一来源的推荐 → 2 个来源 chip、无重复、composer 值为问题、光标在末尾、未发送
it("hides suggestions once the conversation starts", async () => {});
it("requests suggestions in the interface language", async () => {}); // 切换中文 → 显示 zh 页，group “推荐问题”
it("hides suggestions silently when loading fails", async () => {});   // withPromptSuggestionsProblem() → 无 group、无 role="alert"
it("does not touch the draft when suggestions arrive", async () => {});// 延迟返回（deferred）前输入 "draft" → 返回后值仍为 "draft"
it("keeps working when a suggestion source is not listed yet", async () => {}); // 推荐来源 ID 不在 sources 中 → 点击后问题填入，不抛错
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**；样式类名沿用 `tap-` 前缀：`.tap-prompt-suggestions`、`.tap-prompt-suggestion`、`.tap-prompt-suggestion-source`，视觉与原型截图 `e01-suggestions-default.png` 一致。
- [ ] **Step 4: 全量测试与 `tsc -b` 通过**。
- [ ] **Step 5: Commit**：`feat: show prompt suggestions on the tap ai new chat page`

---

### Task 3: 截图流程与端到端验收

**Files:**
- Modify: `tests/e2e/ui-capture.spec.ts`（L32、L128、L227 三处路由处理块）、`tests/e2e/tapper.spec.ts` 或 `knowledge-conversation.spec.ts`（真实后端旅程）

**Interfaces:**
- Consumes: 后端 `GET /api/v1/projects/{id}/prompt-suggestions?locale=` 与确定性网关的推荐（每个就绪来源一个 “What does {name} cover?”）。

- [ ] **Step 1: `ui-capture.spec.ts`**：三处路由处理块都为 `/prompt-suggestions` 返回固定页（4–6 条，来源名与该 spec 已有的已发布来源一致）；运行 `corepack pnpm --dir apps/tap-ai-frontend run ui:capture`，预期全部通过，并与原型 `e01` 截图目视对照。
- [ ] **Step 2: 真实后端旅程**：在现有上传 → 就绪的 e2e 旅程之后新开对话，轮询等待推荐区出现（worker 异步生成，超时 60 秒），点击第一张 → 发送 → 断言得到有引用的回答（非“资料不足”）。运行 `make demo-e2e`，预期通过；若因机器负载失败，如实记录失败输出并与 main 上同一用例对比，不得跳过或伪造。
- [ ] **Step 3: `make check`、`git diff --check` 通过。**
- [ ] **Step 4: Commit**：`test: cover prompt suggestions in tap ai journeys`
- [ ] **Step 5: 业务验收（不属于代码提交）**：在一个真实项目中逐个点击并发送显示的推荐问题，统计有依据回答比例（目标 ≥ 90%），结合能力 5 的可观测性记录核对，结果写入 V1 总纲“业务验证”对应条目。
