# 原型 Tapper 交互补齐实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 TAP AI 实现中已有、`/prototype` 缺失的 Tapper 交互（A–D 组）补进 `apps/web` 原型，使其重新成为唯一完整的设计基准。

**Architecture:** 只改 `apps/web/src/widgets/tap/`。新界面块做成 `prototype/` 下的独立组件，现有交互在原组件内调整。异常状态经新增的 `prototypeFaults.ts` 注入：Vitest 直接调用 `setPrototypeFaults`，Playwright 通过 `window.__TAP_PROTOTYPE_FAULTS__` 在页面加载前注入；页面上没有任何入口。

**Tech Stack:** React 19、TypeScript、antd 6、Vitest + Testing Library、Playwright 1.62。

**Spec:** [原型 Tapper 交互补齐设计](../specs/2026-09-29-prototype-tapper-interaction-sync-design.md)

## Global Constraints

- 只修改 `apps/web`（及 `docs/`）；`apps/tap-ai-frontend` 不改。
- 页面中不得出现演示开关、场景切换、"模拟"标签或实现说明；不显示 hash、偏移量等实现字段。
- 所有新增文案中英双语：接收 `copy: PrototypeCopy` 的组件把文案加入 `prototype/copy.ts`（`PrototypeCopy` 接口与 `en`、`zh` 两处同时加）；只接收 `locale` 的组件用 `t(en, zh)`。文案以本计划给出的中英文为准。
- 支持的上传格式统一为 `.pdf,.docx,.md,.markdown,.txt,.xlsx,.png,.jpg,.jpeg`。
- 截图条件：1280×720 视口、`deviceScaleFactor: 2`（2560×1440 PNG）。
- 每个任务先写失败测试再实现；提交信息用小写祈使句 Conventional Commit，结尾附 `Co-Authored-By` 行。
- 命令从仓库根目录运行；单测用 `corepack pnpm --dir apps/web exec vitest run <path>`。

## Review Focus

1. 注入的故障只生效一次（动作类）或在重试后清除（加载类）：重试必须真正恢复，不能永远失败。→ Task 1 `clears load faults on retry` / `consumes action faults once`。
2. 发送失败时草稿不能被 `TapperChat` 的清空逻辑覆盖。→ Task 5 `keeps the draft when sending fails`。
3. 删除来源后，已选中该来源的会话和附件不应残留失效引用。→ Task 7 `removes a deleted source from the active selection`。
4. 从浏览器快照恢复的旧数据（无新字段）不能让页面崩溃。→ Task 6 `loads a snapshot without seeded history fields`、Task 10 `loads saved chunks without index state`。
5. 引用侧栏打开时切换会话或折叠来源面板，不应卡在引用视图。→ Task 3 `returns to sources when the conversation changes`。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `prototype/prototypeFaults.ts`（新） | 故障注入：类型、读取、消费、清除、测试设置。 |
| `prototype/answer/KnowledgeAnswer.tsx`（新，从 `DocumentReview.tsx` 移出） | 知识问答回答的全部状态渲染。 |
| `prototype/answer/AnswerEvidence.tsx`（新） | 执行记录与"本次使用的资料与配置"两个折叠区。 |
| `prototype/answer/CitationPanel.tsx`（新） | 右侧引用视图，含失效与核验失败。 |
| `prototype/answer/answerOutcome.ts`（新） | 根据提示词、所选来源决定回答结果的纯函数。 |
| `prototype/sampleConversations.ts`（新） | 12 条示例历史会话。 |
| `prototype/SourceDetailDialog.tsx`（新） | 来源详情：文档状态、重试、删除来源。 |
| `prototype/UploadChunkSettings.tsx`（新） | 添加来源第二步：切片设置与预览。 |
| `prototype/GraphViewSwitch.tsx`（新） | 领域总览 / 已发布来源图谱切换。 |
| `prototype/skillMarkdown.ts`（新） | 名称校验与 `SKILL.md` 生成。 |
| `tests/e2e/prototype-states.spec.ts`、`playwright.prototype.config.ts`（新） | 前后截图与状态截图集采集。 |

其余为对 `TapProductPrototype.tsx`、`TapperChat.tsx`、`PrototypeSidebar.tsx`、`KnowledgeSourcesPanel.tsx`、`LibraryWorkspace.tsx`、`DocumentReview.tsx`、`ChunkManager.tsx`、`CatalogWorkspace.tsx`、`model.ts`、`copy.ts`、`sampleFiles.ts` 的修改。下文路径均相对 `apps/web/src/widgets/tap/`，除非以 `apps/`、`docs/` 开头。

---

### Task 0: 截图采集脚本与变更前基线

**Files:**
- Create: `apps/web/playwright.prototype.config.ts`
- Create: `apps/web/tests/e2e/prototype-states.spec.ts`
- Create: `docs/assets/prototype-states/before/*.png`

**Interfaces:**
- Produces: 环境变量 `TAP_PROTOTYPE_CAPTURE_DIR`（输出目录）与 `TAP_PROTOTYPE_CAPTURE_SET`（`before` | `after` | `states`）；辅助函数 `capture(page, name: string)` 与 `openWithFaults(page, path: string, faults: readonly string[])`，后续 Task 12 追加状态用例。

- [ ] **Step 1: 写配置**：`testDir: "./tests/e2e"`、`testMatch: "prototype-states.spec.ts"`、`workers: 1`、`use: { baseURL: process.env.TAP_PROTOTYPE_BASE_URL ?? "http://127.0.0.1:15176", viewport: { width: 1280, height: 720 }, deviceScaleFactor: 2 }`、`webServer: { command: "corepack pnpm dev --port 15176", url: "http://127.0.0.1:15176/prototype", reuseExistingServer: true }`。
- [ ] **Step 2: 写 `before`/`after` 用例**：每个用例前 `page.addInitScript(() => localStorage.clear())`。采集页面：`tapper-new-chat`（`/prototype`）、`tapper-answer`（选中 `Life underwriting guide · v1.2.md`，发送 `Summarize the health disclosure rules`，等回答完成）、`library-list`（`/prototype?module=library` 后点 `All`）、`library-graph`、`agents`、`skills`、`test-management`、`low-code`、`test-insights`。文件名 `${set}/${name}.png`，`animations: "disabled"`。
- [ ] **Step 3: 运行采集变更前截图**

Run: `TAP_PROTOTYPE_CAPTURE_SET=before TAP_PROTOTYPE_CAPTURE_DIR=../../docs/assets/prototype-states corepack pnpm --dir apps/web exec playwright test -c playwright.prototype.config.ts`
Expected: 9 passed，`docs/assets/prototype-states/before/` 下有 9 张 2560×1440 PNG。

- [ ] **Step 4: Commit**：`test: add prototype screenshot capture and baseline`

---

### Task 1: 故障注入模块

**Files:**
- Create: `prototype/prototypeFaults.ts`
- Test: `prototype/prototypeFaults.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export type PrototypeFault =
    | "send-failed" | "stop-failed" | "stream-interrupted" | "no-models"
    | "history-load-failed" | "conversation-delete-failed"
    | "sources-load-failed" | "library-load-failed" | "upload-failed"
    | "graph-load-failed" | "citation-verification-failed" | "source-version-changed";
  export function isPrototypeFaultActive(fault: PrototypeFault): boolean;   // 加载类：渲染时读取
  export function clearPrototypeFault(fault: PrototypeFault): void;         // 加载类：重试时清除
  export function takePrototypeFault(fault: PrototypeFault): boolean;       // 动作类：读取并消费，只在事件处理或定时器中调用
  export function setPrototypeFaults(faults: readonly PrototypeFault[]): void; // 仅测试使用
  ```
  故障集合在首次访问时从 `window.__TAP_PROTOTYPE_FAULTS__`（`string[]`，忽略未知值）初始化。

- [ ] **Step 1: 写失败测试**

```ts
afterEach(() => setPrototypeFaults([]));
it("reads faults injected before the page loads", () => { /* 设置 window.__TAP_PROTOTYPE_FAULTS__ = ["upload-failed", "bogus"]，重置模块（vi.resetModules 后动态 import），expect isPrototypeFaultActive("upload-failed") true，未知值被忽略 */ });
it("consumes action faults once", () => {
  setPrototypeFaults(["send-failed"]);
  expect(takePrototypeFault("send-failed")).toBe(true);
  expect(takePrototypeFault("send-failed")).toBe(false);
});
it("clears load faults on retry", () => {
  setPrototypeFaults(["library-load-failed"]);
  expect(isPrototypeFaultActive("library-load-failed")).toBe(true);
  clearPrototypeFault("library-load-failed");
  expect(isPrototypeFaultActive("library-load-failed")).toBe(false);
});
```

- [ ] **Step 2: 运行确认失败**：`vitest run src/widgets/tap/prototype/prototypeFaults.test.ts` → FAIL（模块不存在）。
- [ ] **Step 3: 实现**，用模块级 `Set<PrototypeFault>`；`declare global { interface Window { __TAP_PROTOTYPE_FAULTS__?: readonly string[] } }`。
- [ ] **Step 4: 运行确认通过** → 3 passed。
- [ ] **Step 5: Commit**：`feat: add prototype fault injection for state review`

---

### Task 2: 执行记录与本次使用的资料（A1、A2）

**Files:**
- Create: `prototype/answer/KnowledgeAnswer.tsx`（把 `DocumentReview.tsx` 中的 `KnowledgeAnswer` 原样移入，`TapProductPrototype.tsx` 改为从此导入）
- Create: `prototype/answer/AnswerEvidence.tsx`
- Modify: `prototype/model.ts`（`AssistantTurn`）
- Test: `prototype/answer/AnswerEvidence.test.tsx`

**Interfaces:**
- Produces:
  ```ts
  // model.ts
  export interface AnswerTrace { searchedSources: number; matchedPassages: number; citations: number }
  // AssistantTurn 新增
  trace?: AnswerTrace;
  // AnswerEvidence.tsx
  export function AnswerEvidence(props: { turn: AssistantTurn }): JSX.Element | null;
  ```
  `KnowledgeAnswer` 在 `completed` 状态的回答正文上方渲染 `<AnswerEvidence turn={turn} />`。`sendMessage` 生成回答时写入 `trace`：`searchedSources` = 所选就绪来源数，`matchedPassages` = 证据来源数 × 2 + 1，`citations` = 证据来源数。

文案（`t(en, zh)`）：

| 用途 | en | zh |
| --- | --- | --- |
| 执行记录摘要 | `Searched {n} sources · {m} passages matched · {k} citations` | `已检索 {n} 份来源 · 命中 {m} 段 · 引用 {k} 处` |
| 步骤 | `Search` / `Filter` / `Generate` | `检索` / `筛选` / `生成` |
| 资料折叠标题 | `Sources and configuration used` | `本次使用的资料与配置` |
| 分组 | `Knowledge sources` / `Agent` / `Skills` | `知识来源` / `Agent` / `Skills` |

- [ ] **Step 1: 写失败测试**

```tsx
it("summarizes the answer trace collapsed by default", () => {
  render(<AnswerEvidence turn={completedTurn({ trace: { searchedSources: 3, matchedPassages: 5, citations: 2 } })} />);
  const trace = screen.getByRole("button", { name: /Searched 3 sources · 5 passages matched · 2 citations/ });
  expect(trace).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(trace);
  expect(screen.getByText("Search")).toBeVisible();
});
it("groups sources, agents and skills used by the turn", () => {
  render(<AnswerEvidence turn={completedTurn({ catalogReferences: [{ id: "a1", kind: "agent", name: "Underwriting analyst" }, { id: "s1", kind: "skill", name: "bdd-writer" }] })} />);
  fireEvent.click(screen.getByRole("button", { name: "Sources and configuration used" }));
  expect(screen.getByRole("group", { name: "Knowledge sources" })).toHaveTextContent("Life underwriting guide · v1.2.md");
  expect(screen.getByRole("group", { name: "Agent" })).toHaveTextContent("Underwriting analyst");
  expect(screen.getByRole("group", { name: "Skills" })).toHaveTextContent("bdd-writer");
});
it("renders nothing without a trace", () => { /* 无 trace 的 turn → container 为空 */ });
```

`completedTurn` 是测试内的工厂函数，默认 `answerState: "completed"`、`locale: "en"`、一条来源 `Life underwriting guide · v1.2.md`。

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 移动 `KnowledgeAnswer` 并实现 `AnswerEvidence`**：两个折叠区用 `<button aria-expanded>` + 受控面板；分组用 `role="group"` + `aria-label`；空分组不渲染。在 `TapProductPrototype.tsx` 的 `sendMessage` 中写入 `trace`。
- [ ] **Step 4: 运行本测试及 `src/widgets/tap` 全部测试** → 全部通过（移动 `KnowledgeAnswer` 不应破坏 `DocumentReview.test.tsx`、`TapProductPrototype.test.tsx`）。
- [ ] **Step 5: Commit**：`feat: show answer trace and context in the prototype`

---

### Task 3: 引用侧栏、失效与核验失败（A3、A4、A5）

**Files:**
- Create: `prototype/answer/CitationPanel.tsx`
- Modify: `prototype/answer/KnowledgeAnswer.tsx`（移除 `AccessibleDialog` 引用弹窗，引用按钮改调 `onOpenCitation`）
- Modify: `prototype/model.ts`（`LibrarySource.hasNewerRevision?: boolean`）
- Modify: `prototype/sampleFiles.ts`（`sample-underwriting` 加 `hasNewerRevision: true`）
- Modify: `TapProductPrototype.tsx`（`openCitation` 状态；来源面板位置在 `openCitation !== null` 时渲染 `CitationPanel`）
- Test: `prototype/answer/CitationPanel.test.tsx`，`TapProductPrototype.test.tsx` 追加

**Interfaces:**
- Consumes: `takePrototypeFault("citation-verification-failed")`（Task 1）。
- Produces:
  ```ts
  export interface OpenCitation { turnId: string; index: number; source: AssistantSourceReference & { hasNewerRevision?: boolean } }
  export function CitationPanel(props: {
    citation: OpenCitation; locale: Locale;
    onClose: () => void; onOpenOriginal: (sourceId: string) => void;
  }): JSX.Element;
  // KnowledgeAnswer 新增 prop
  onOpenCitation: (citation: OpenCitation) => void;
  ```
  `KnowledgeAnswer` 为每个证据来源渲染 `[n] <来源名>` 引用按钮（n 从 1 开始）。`CitationPanel` 挂载时调用一次 `takePrototypeFault`，为真则显示核验失败。`onOpenOriginal` 切到 Library 并调用 `review.inspect(sourceId)`（知识库文档）或定位到 Library 列表中该来源（示例文件）。

文案：

| 用途 | en | zh |
| --- | --- | --- |
| 面板标题 | `Citation [{n}]` | `引用 [{n}]` |
| 位置 | `Version {v} · Section 4` | `版本 {v} · 第 4 节` |
| 打开原件 | `Open original` | `打开原件` |
| 关闭 | `Back to sources` | `返回来源` |
| 失效 | `This source has been updated. The cited passage may have changed.` | `来源已更新，引用内容可能已变化。` |
| 查看最新 | `View latest version` | `查看最新版本` |
| 核验失败 | `The citation could not be verified.` | `引用核验失败。` |
| 重试 | `Retry verification` | `重试核验` |

- [ ] **Step 1: 写失败测试**

```tsx
it("shows the cited passage with its location", () => { /* 渲染 CitationPanel，expect heading "Citation [1]"、文本 "Version v1.2 · Section 4"、高亮 <mark>、按钮 "Open original" */ });
it("warns when the cited source has a newer revision", () => { /* source.hasNewerRevision true → role="alert" 含 "This source has been updated"，按钮 "View latest version" */ });
it("retries a failed verification", () => {
  setPrototypeFaults(["citation-verification-failed"]);
  render(<CitationPanel ... />);
  expect(screen.getByRole("alert")).toHaveTextContent("The citation could not be verified.");
  fireEvent.click(screen.getByRole("button", { name: "Retry verification" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByText(/HEALTH_DISCLOSURE_REQUIRED/)).toBeVisible();
});
// TapProductPrototype.test.tsx
it("opens citations in the sources panel instead of a dialog", async () => { /* 选中 Life underwriting guide 与 Underwriting test rules.pdf，发送健康告知问题，等待回答，点 "[2] Underwriting test rules.pdf" → 无 role="dialog"；complementary 区域出现 "Citation [2]" 与失效提示；点 "Back to sources" → 出现 "Knowledge sources" 标题 */ });
it("returns to sources when the conversation changes", async () => { /* 打开引用后点 New chat → 不再显示 "Citation [1]" */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**：`openCitation` 在 `activeConversationId` 变化时重置为 `null`；来源面板折叠时点击引用先展开面板。原文段落沿用现有引用弹窗中的健康告知文本。
- [ ] **Step 4: 运行确认通过**（本任务测试 + `src/widgets/tap` 全部）。
- [ ] **Step 5: Commit**：`feat: open citations in the prototype sources panel`

---

### Task 4: 回答结果与排队态（A6–A9、B1、B4）

**Files:**
- Create: `prototype/answer/answerOutcome.ts`
- Modify: `prototype/model.ts`（`answerState` 增加 `"queued" | "conflict" | "source-changed" | "interrupted"`；`AssistantTurn.retrievalLimited?: boolean`；`LibrarySource.partiallyIndexed?: boolean`）
- Modify: `prototype/DocumentReview.tsx`（`underwriting-evidence-pdf` 文档映射出的来源带 `partiallyIndexed: true`，描述改为 `Some pages are still indexing` / `部分页面仍在索引中`）
- Modify: `prototype/answer/KnowledgeAnswer.tsx`、`TapProductPrototype.tsx`（`sendMessage`）
- Test: `prototype/answer/answerOutcome.test.ts`、`prototype/answer/KnowledgeAnswer.test.tsx`

**Interfaces:**
- Consumes: `takePrototypeFault("source-version-changed" | "stream-interrupted")`（在 1200 ms 完成定时器内调用）。
- Produces:
  ```ts
  export type AnswerOutcome = "completed" | "insufficient" | "conflict";
  export function decideAnswerOutcome(prompt: string, sources: readonly LibrarySource[]): {
    outcome: AnswerOutcome; evidence: readonly LibrarySource[]; retrievalLimited: boolean;
  };
  ```
  规则：`conflict` 当所选就绪来源中名称匹配 `/beneficiary/i` 的 ≥ 2 份且提示词匹配 `/beneficiar|受益人/i`，证据为这些受益人来源；否则沿用现有规则（证据 = 名称匹配 `/underwriting|健康|核保/i`；有证据且提示词匹配 `/health|disclosure|underwriting|健康|告知|核保/i` 为 `completed`，否则 `insufficient`）。`retrievalLimited` = 任一所选来源 `partiallyIndexed`。
  `sendMessage` 时序：追加 turn 为 `queued` → 500 ms 后 `running` → 再 1200 ms 后按故障（优先 `stream-interrupted`，其次 `source-version-changed`）或 `decideAnswerOutcome` 决定最终状态。停止在 `queued`、`running` 都有效。

文案：

| 状态 | en | zh |
| --- | --- | --- |
| queued | `Waiting to start…` | `等待开始…` |
| running | `Using {n} sources · Generating answer…` | `使用 {n} 份来源 · 正在生成回答…` |
| conflict | `The two sources reach different conclusions.` | `两份资料结论不一致。` |
| source-changed | `Sources were updated while answering. Please resubmit.` + 按钮 `Resubmit` | `回答期间资料已更新，请重新提交。` + `重新提交` |
| interrupted | `Conversation updates stopped. Your message is saved.` + 按钮 `Retry` | `对话更新已中断，你的消息已保存。` + `重试` |
| retrievalLimited | `Some sources could not be searched. This answer uses the remaining sources.` | `部分来源暂时无法检索，回答仅基于其余来源。` |

- [ ] **Step 1: 写失败测试**

```ts
it("detects conflicting beneficiary sources", () => {
  expect(decideAnswerOutcome("What changes a beneficiary?", [beneficiaryDocx, beneficiaryCases]).outcome).toBe("conflict");
});
it("keeps the underwriting completion rule", () => { /* Life underwriting guide + "Summarize the health disclosure rules" → completed；无匹配 → insufficient */ });
it("flags partially indexed sources", () => { /* 含 partiallyIndexed 来源 → retrievalLimited true */ });
```
```tsx
it("lists both conflicting citations", () => { /* conflict turn 含两份来源 → 文本 "The two sources reach different conclusions." 与两个引用按钮 */ });
it("offers resubmit when sources changed", () => { /* source-changed → 点 "Resubmit" 调用 onRetry */ });
it("offers retry after updates stop", () => { /* interrupted → 点 "Retry" 调用 onRetry */ });
it("shows the queued stage before generating", () => { /* queued → "Waiting to start…" 与 "Stop" 按钮 */ });
it("warns about limited retrieval above the answer", () => { /* completed + retrievalLimited → role="status" 文案 */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**；`TapperChat` 与 `TapProductPrototype` 中所有判断 `answerState === "running"` 的地方改为 `queued` 或 `running` 都视为生成中（抽成 `isGenerating(turn)` 放在 `model.ts`）。
- [ ] **Step 4: 运行确认通过**（本任务测试 + `src/widgets/tap` 全部；如现有测试用假定时器推进 1200 ms，改为推进 1700 ms）。
- [ ] **Step 5: Commit**：`feat: add answer outcomes and queued state to the prototype`

---

### Task 5: 输入框：发送失败、停止失败、模型可用性、来源说明（B2、B3、B5、B6）

**Files:**
- Modify: `prototype/TapperChat.tsx`、`TapProductPrototype.tsx`、`prototype/model.ts`、`prototype/copy.ts`
- Test: `TapperChatControls.test.tsx` 追加

**Interfaces:**
- Consumes: `takePrototypeFault("send-failed" | "stop-failed")`、`isPrototypeFaultActive("no-models")`。
- Produces:
  - `TapperChat` 的 `onSend: (prompt: string) => boolean`：返回 `false` 时不清空输入框；新增 props `sendError: string | null`、`stopError: string | null`。
  - `CODEX_MODELS` 每项增加 `available: boolean`；`gpt-5.4` 为 `false`，其余 `true`。`no-models` 故障激活时视全部为不可用。
  - `copy.composer` 新增 `sendFailed`、`stopFailed`、`modelUnavailable`、`noModels`、`contextNote`。

文案：

| key | en | zh |
| --- | --- | --- |
| `sendFailed` | `Message was not sent. Your draft is still here. Please try again.` | `消息未发送，草稿已保留，请重试。` |
| `stopFailed` | `The response may still be running. Please try again shortly.` | `回答可能仍在生成，请稍后再试。` |
| `modelUnavailable` | `Unavailable` | `不可用` |
| `noModels` | `No models available` | `暂无可用模型` |
| `contextNote` | `Each turn records the knowledge context you select.` | `每轮对话会记录你选择的知识来源。` |

- [ ] **Step 1: 写失败测试**

```tsx
it("keeps the draft when sending fails", async () => {
  setPrototypeFaults(["send-failed"]);
  render(<TapProductPrototype />);
  type composer "Summarize the health disclosure rules"; click Send;
  expect(await screen.findByRole("alert")).toHaveTextContent("Message was not sent.");
  expect(composer).toHaveValue("Summarize the health disclosure rules");
  click Send again → alert gone，composer 为空。
});
it("reports a failed stop", () => { /* stop-failed：发送后点 Stop → alert "The response may still be running."，回答仍在生成 */ });
it("disables unavailable models", () => { /* 打开模型菜单 → "GPT-5.4" 项 aria-disabled="true" 且含 "Unavailable" */ });
it("blocks sending when no model is available", () => { /* no-models → 模型按钮文本 "No models available"，Send 禁用 */ });
it("explains that each turn records context", () => { /* 文本 contextNote 可见 */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**：`sendMessage` 返回 `boolean`；失败时设置 `sendError`，下次成功发送或修改输入时清除。`stopTurn` 在故障时不改状态、设置 `stopError`，3 秒后清除。
- [ ] **Step 4: 运行确认通过**（`src/widgets/tap` 全部）。
- [ ] **Step 5: Commit**：`feat: add composer failure and model states to the prototype`

---

### Task 6: 会话历史：加载失败、加载更多、删除失败（B7–B9）

**Files:**
- Create: `prototype/sampleConversations.ts`
- Modify: `prototype/PrototypeSidebar.tsx`、`TapProductPrototype.tsx`（无快照时初始会话 = `createConversation("chat-1")` + `SAMPLE_CONVERSATIONS`）、`prototype/copy.ts`（`navigation`）
- Test: `prototype/PrototypeSidebar.history.test.tsx`（新）

**Interfaces:**
- Consumes: `isPrototypeFaultActive/clearPrototypeFault("history-load-failed")`、`takePrototypeFault("conversation-delete-failed")`。
- Produces: `export const SAMPLE_CONVERSATIONS: readonly Conversation[]`（12 条，id `sample-chat-1`…`sample-chat-12`，每条 1 个 `completed` 或 `insufficient` turn，标题为保险/测试主题）；`export const HISTORY_PAGE_SIZE = 10`（`PrototypeSidebar.tsx`）。`PrototypeSidebar` 的 `onDeleteConversation` 改为返回 `boolean`。

文案（`copy.navigation`）：

| key | en | zh |
| --- | --- | --- |
| `historyLoadFailed` | `Chat history could not be loaded.` | `历史加载失败。` |
| `retry` | `Retry` | `重试` |
| `loadMore` | `Load more` | `加载更多` |
| `deleteFailed` | `The chat could not be deleted. Please try again.` | `删除失败，请重试。` |

- [ ] **Step 1: 写失败测试**

```tsx
it("pages chat history", () => { /* 13 条有内容的会话 → 显示 10 行 + "Load more"；点击后 13 行，按钮消失 */ });
it("retries a failed history load", () => { /* history-load-failed → alert + Retry；点击后显示会话行 */ });
it("keeps the chat when deletion fails", () => { /* conversation-delete-failed → 确认删除后行仍在，alert "The chat could not be deleted." */ });
it("loads a snapshot without seeded history fields", () => { /* localStorage 写入只含 chat-1 的旧快照 → 正常渲染，不注入示例会话 */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**；搜索时对全部会话过滤，不分页。
- [ ] **Step 4: 运行 `src/widgets/tap` 全部测试**。现有测试若断言历史为空或按位置取第一行，改为按会话标题定位；不得删除原有断言意图。
- [ ] **Step 5: Commit**：`feat: add chat history paging and failure states to the prototype`

---

### Task 7: 来源详情：文档状态、重试、删除来源（C3）

**Files:**
- Create: `prototype/SourceDetailDialog.tsx`
- Modify: `prototype/LibraryWorkspace.tsx`（每行加 `View {name}` 按钮）、`TapProductPrototype.tsx`（`removedSourceIds` 状态，过滤 `sources`，并从所有会话 `selectedSourceIds` 与附件中移除）、`prototype/DocumentReview.tsx`（`useDocumentReview` 新增 `retry(id: string): void`：`failed` → `processing`，900 ms 后 → `review`；不再按 `/scan/` 规则回到失败）、`prototype/copy.ts`（`library`）
- Test: `prototype/SourceDetailDialog.test.tsx`、`TapProductPrototype.test.tsx` 追加

**Interfaces:**
- Produces:
  ```ts
  export function SourceDetailDialog(props: {
    copy: PrototypeCopy; source: LibrarySource; opener: HTMLElement | null;
    onClose: () => void; onRetry: (sourceId: string) => void; onDelete: (sourceId: string) => void;
  }): JSX.Element;
  // LibraryWorkspace 新增 props
  onRetrySource: (sourceId: string) => void; onDeleteSource: (sourceId: string) => void;
  ```
  详情列出该来源的文档行：名称、类型、状态（就绪/处理中/失败），失败行显示 `Retry`。示例失败文档沿用 `Underwriting rules — scanned.pdf`。

文案（`copy.library`）：`viewSource` `View` / `查看`；`sourceDocuments` `Documents` / `文档`；`retryDocument` `Retry` / `重试`；`deleteSource` `Delete source` / `删除来源`；`deleteSourceConfirm` `Delete this source? It will be removed from Library and knowledge sources.` / `删除该来源？它将从知识库和知识来源中移除。`；`confirmDelete` `Delete` / `删除`。

- [ ] **Step 1: 写失败测试**

```tsx
it("retries a failed document", () => { /* 打开 scanned.pdf 详情 → 状态 Failed + Retry；点击 → Processing；推进 900 ms → Ready */ });
it("deletes a source after confirmation", () => { /* Delete source → 确认文案 → Delete → onDelete 被调用；取消不调用 */ });
it("removes a deleted source from the active selection", () => { /* 在 Tapper 选中 Underwriting test rules.pdf → Library 删除该来源 → 回 Tapper，来源面板无此项，已选计数为 0 */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**；`removedSourceIds` 写入原型快照（`prototype/artifacts/persistence.ts` 的 `library` 段新增可选 `removedSourceIds: string[]`，旧快照缺省为 `[]`）。
- [ ] **Step 4: 运行确认通过**（`src/widgets/tap` 全部，含 `artifacts/persistence.test.ts`）。
- [ ] **Step 5: Commit**：`feat: add source detail with retry and delete to the prototype`

---

### Task 8: 来源面板与 Library 的失败和空态（B10、B11）

**Files:**
- Modify: `prototype/KnowledgeSourcesPanel.tsx`、`prototype/LibraryWorkspace.tsx`、`prototype/copy.ts`
- Test: `prototype/KnowledgeSourcesPanel.test.tsx`（新）、`prototype/LibraryWorkspace.states.test.tsx`（新）

**Interfaces:**
- Consumes: `isPrototypeFaultActive/clearPrototypeFault("sources-load-failed" | "library-load-failed")`。

文案：`sources.loadFailed` `Knowledge sources could not be loaded.` / `知识来源加载失败。`；`sources.allProcessing` `Sources are processing. They can be selected when ready.` / `来源正在处理，完成后可选用。`；`library.loadFailed` `Library could not be loaded.` / `知识库加载失败。`；`library.emptyHeading` `No knowledge sources yet` / `还没有知识来源`；`retry` 复用 `Retry` / `重试`。

- [ ] **Step 1: 写失败测试**

```tsx
it("retries loading knowledge sources", () => { /* sources-load-failed → alert + Retry → 列表出现 */ });
it("explains when every source is processing", () => { /* sources 全部 status processing → 文案 allProcessing；无 ready 且无 processing 时仍显示原 noReadySources */ });
it("retries loading Library", () => { /* library-load-failed → alert + Retry → 列表出现 */ });
it("distinguishes an empty Library from no search results", () => { /* sources=[] → "No knowledge sources yet" + "Add source" 按钮；有来源但搜索无结果 → 原 noResults */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**（`KnowledgeSourcesPanel` 需要接收全部来源以判断处理中，`TapProductPrototype` 已传入全部 `sources`）。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: Commit**：`feat: add source and library failure and empty states`

---

### Task 9: 添加来源两步流程、格式、上传中与失败（C1、C2、B12）

**Files:**
- Create: `prototype/UploadChunkSettings.tsx`
- Modify: `prototype/ChunkManager.tsx`（导出 `export type ChunkSettings`（即现 `Settings`）、`export const DEFAULT_CHUNK_SETTINGS`、`export function generateChunks(text: string, settings: ChunkSettings): Chunk[]`、`export type Chunk`、`export const SAMPLE_CHUNK_SOURCE_TEXT`（即现 `original`））
- Modify: `prototype/DocumentReview.tsx`（`upload(name, options: { inspect?: boolean; chunkSettings?: ChunkSettings })`：提供 `chunkSettings` 时 `chunkSetup: false`，并把 `{ settings, chunks, versions: [chunks] }` 写入 `tap.prototype.chunks.v1.${id}`，不自动打开详情；替换文件的 `accept` 改为统一格式）
- Modify: `prototype/LibraryWorkspace.tsx`（对话框两步；`onAddSource` 签名改为 `(source: Pick<LibrarySource, "name" | "type">, chunkSettings: ChunkSettings) => void`）、`TapProductPrototype.tsx`（`addLocalSource` 透传）、`prototype/copy.ts`
- Test: `prototype/UploadChunkSettings.test.tsx`、`prototype/LibraryWorkspace.upload.test.tsx`

**Interfaces:**
- Consumes: `takePrototypeFault("upload-failed")`（提交时）。
- Produces:
  ```ts
  export function UploadChunkSettings(props: {
    copy: PrototypeCopy; value: ChunkSettings; onChange: (value: ChunkSettings) => void;
  }): JSX.Element;
  export const ACCEPTED_SOURCE_EXTENSIONS = ".pdf,.docx,.md,.markdown,.txt,.xlsx,.png,.jpg,.jpeg"; // 放在 fileTypes.ts
  ```
  第二步字段：分段方式（`general` / `parent-child`）、最大长度、重叠；`parent-child` 时加子块最大长度。`Preview chunks` 用 `generateChunks(SAMPLE_CHUNK_SOURCE_TEXT, value)` 显示前 3 个切片（父子模式下每个显示子块数）。提交：按钮 loading、`Cancel` 与返回上一步禁用，1000 ms 后完成并关闭；故障时不关闭并显示错误。

文案（`copy.library`）：`stepFile` `Choose file` / `选择文件`；`stepChunks` `Chunk settings` / `切片设置`；`next` `Next` / `下一步`；`back` `Back` / `上一步`；`recommended` `Recommended settings are applied. You can submit directly.` / `已应用推荐设置，可直接提交。`；`previewChunks` `Preview chunks` / `预览切片`；`childChunks` `{n} child chunks` / `{n} 个子块`；`uploading` `Uploading…` / `正在上传…`；`uploadFailed` `Upload failed. Please try again.` / `上传失败，请重试。`；`supportedFormats` `PDF, DOCX, Markdown, TXT, XLSX, PNG or JPG` / `支持 PDF、DOCX、Markdown、TXT、XLSX、PNG、JPG`。

- [ ] **Step 1: 写失败测试**

```tsx
it("previews the first chunks for the chosen settings", () => { /* 默认设置 → 点 Preview chunks → 3 个 listitem；改为 parent-child → 每项含 "child chunks" */ });
it("accepts the unified source formats", () => { /* file input accept === ACCEPTED_SOURCE_EXTENSIONS；格式说明文案可见 */ });
it("uploads with the chosen chunk settings", () => { /* 选文件 → Next → 最大长度改 300 → 提交 → 按钮 loading、Cancel 禁用 → 推进 1000 ms → 对话框关闭，onAddSource 收到 max 300 */ });
it("keeps the dialog open when upload fails", () => { /* upload-failed → 提交后 alert "Upload failed. Please try again."，对话框仍在；再次提交成功关闭 */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行 `src/widgets/tap` 全部**（`ChunkManager.test.tsx`、`DocumentReview.test.tsx` 须保持通过）。
- [ ] **Step 5: Commit**：`feat: configure chunks while adding a source in the prototype`

---

### Task 10: 单个切片索引失败与重试（B13）

**Files:**
- Modify: `prototype/ChunkManager.tsx`（`Chunk.indexState?: "indexed" | "indexing" | "failed"`，缺省视为 `indexed`；文档 id 为 `underwriting-evidence-pdf` 且无保存数据时，默认第 2 个切片为 `failed`；失败切片显示 `Index failed` / `索引失败` 与 `Retry indexing` / `重试索引`，点击后 `indexing`（`Indexing…` / `索引中…`），800 ms 后 `indexed`）
- Test: `prototype/ChunkManager.test.tsx` 追加

- [ ] **Step 1: 写失败测试**

```tsx
it("retries indexing a failed chunk", () => { /* id="underwriting-evidence-pdf" → 1 处 "Index failed" + "Retry indexing"；点击 → "Indexing…"；推进 800 ms → 无失败标记 */ });
it("loads saved chunks without index state", () => { /* localStorage 写入旧格式 chunks（无 indexState）→ 渲染无失败标记 */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: Commit**：`feat: show chunk index failure and retry in the prototype`

---

### Task 11: 图谱视图切换与加载失败（C4）

**Files:**
- Create: `prototype/GraphViewSwitch.tsx`
- Modify: `prototype/LibraryWorkspace.tsx`（图谱面板顶部渲染切换；"已发布来源图谱"下 `KnowledgeGraph` 的 `sources` 仅为所选来源）、`prototype/model.ts`（`LibrarySource.hasPublishedGraph?: boolean`）、`prototype/sampleFiles.ts`（`sample-underwriting`、`sample-beneficiary` 设为 `true`）、`prototype/DocumentReview.tsx`（`state === "published"` 的文档映射为 `true`）、`prototype/copy.ts`
- Test: `prototype/GraphViewSwitch.test.tsx`

**Interfaces:**
- Consumes: `isPrototypeFaultActive/clearPrototypeFault("graph-load-failed")`。
- Produces:
  ```ts
  export type GraphView = { kind: "domain" } | { kind: "source"; sourceId: string | null };
  export function GraphViewSwitch(props: {
    copy: PrototypeCopy; sources: readonly LibrarySource[]; value: GraphView; onChange: (value: GraphView) => void;
  }): JSX.Element;
  ```
  下拉列出全部就绪来源；所选来源无 `hasPublishedGraph` 时图谱区域显示空态。

文案：`graphDomain` `Domain overview` / `领域总览`；`graphPublished` `Published source graph` / `已发布来源图谱`；`graphSource` `Source` / `来源`；`graphEmpty` `This source has no published graph yet.` / `该来源尚无已发布图谱。`；`graphLoadFailed` `The knowledge graph could not be loaded.` / `知识图谱加载失败。`。

- [ ] **Step 1: 写失败测试**

```tsx
it("switches to a published source graph", () => { /* 选 Published source graph → 下拉选 Underwriting test rules.pdf → onChange({kind:"source", sourceId:"sample-underwriting"}) */ });
it("shows an empty state for a source without a published graph", () => { /* LibraryWorkspace 中选 Exploratory testing checklist.md → graphEmpty 文案 */ });
it("retries a failed graph load", () => { /* graph-load-failed → alert + Retry → 图谱出现 */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**（切换用 antd `Segmented`）。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: Commit**：`feat: switch between domain and published source graphs`

---

### Task 12: Agent/Skill 内置只读、名称规则与 SKILL.md 预览（D）

**Files:**
- Create: `prototype/skillMarkdown.ts`
- Modify: `prototype/CatalogWorkspace.tsx`、`prototype/copy.ts`（`catalog`）
- Test: `prototype/skillMarkdown.test.ts`、`prototype/CatalogWorkspace.test.tsx`（新）

**Interfaces:**
- Produces:
  ```ts
  export const CATALOG_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  export function isValidCatalogName(name: string): boolean;
  export function toSkillMarkdown(draft: CatalogDraft): string;
  ```
  `toSkillMarkdown` 输出：

```text
---
name: <name>
description: <description>
---

<instructions>
```

  内置条目（`origin === "built-in"`）不渲染编辑按钮；保存按钮在名称无效时禁用；名称输入下方实时显示校验提示；表单右侧显示 `SKILL.md` 预览（`<pre aria-label="SKILL.md preview">`）。编辑现有自定义条目时名称同样校验。

文案（`copy.catalog`）：`nameRule` `Use lowercase letters, numbers and hyphens, e.g. health-disclosure-check.` / `只能使用小写字母、数字和连字符，例如 health-disclosure-check。`；`previewHeading` `SKILL.md preview` / `SKILL.md 预览`。

- [ ] **Step 1: 写失败测试**

```ts
it("accepts kebab-case names only", () => {
  expect(isValidCatalogName("health-disclosure-check")).toBe(true);
  for (const bad of ["Health", "a--b", "-a", "a_b", "a b", ""]) expect(isValidCatalogName(bad)).toBe(false);
});
it("renders frontmatter and body", () => {
  expect(toSkillMarkdown({ name: "bdd-writer", description: "Writes BDD", instructions: "Use Given/When/Then." }))
    .toBe("---\nname: bdd-writer\ndescription: Writes BDD\n---\n\nUse Given/When/Then.\n");
});
```
```tsx
it("keeps built-in items read-only", () => { /* 内置条目无 Edit 按钮，有 Use in chat */ });
it("validates the name and previews SKILL.md while creating", () => { /* 输入 "Bad Name" → 提示可见、保存禁用；改为 "bdd-writer" → 保存可用，预览含 "name: bdd-writer" */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**；内置条目的现有名称不受校验影响。
- [ ] **Step 4: 运行 `src/widgets/tap` 全部**（现有测试中创建条目使用的名称若非 kebab-case，改为合法名称）。
- [ ] **Step 5: Commit**：`feat: add skill naming rules and markdown preview to the prototype`

---

### Task 13: 状态截图集、回归与文档

**Files:**
- Modify: `apps/web/tests/e2e/prototype-states.spec.ts`（`states` 用例集）
- Create: `docs/assets/prototype-states/after/*.png`、`docs/assets/prototype-states/states/*.png`
- Create: `docs/guides/2026-09-29-prototype-state-gallery.md`
- Modify: `docs/guides/2026-09-22-product-prototype-baseline.md`（新增"2026-09-29 Tapper 交互补齐"一节）

- [ ] **Step 1: 追加 `states` 用例**：每个状态一个用例，注入型用 `openWithFaults` 设置 `window.__TAP_PROTOTYPE_FAULTS__`。清单（文件名 → 触发）：
  - 自然：`a1-answer-trace`、`a2-context-used`、`a3-citation-panel`、`a4-citation-stale`、`a7-conflict`、`a9-retrieval-limited`、`b1-queued`、`b5-model-unavailable`、`b8-history-load-more`、`b10-sources-processing`、`b11-library-empty`、`b12-uploading`、`b13-chunk-index-failed`、`c1-upload-chunk-preview`、`c3-source-detail`、`c4-graph-empty`、`d-builtin-readonly`、`d-skill-preview`
  - 注入：`a5-citation-verification-failed`、`a8-source-changed`、`b2-send-failed`、`b3-stop-failed`、`b4-stream-interrupted`、`b5-no-models`、`b7-history-load-failed`、`b9-delete-failed`、`b10-sources-load-failed`、`b11-library-load-failed`、`b12-upload-failed`、`c4-graph-load-failed`
- [ ] **Step 2: 采集 `after` 与 `states`**

Run: `for s in after states; do TAP_PROTOTYPE_CAPTURE_SET=$s TAP_PROTOTYPE_CAPTURE_DIR=../../docs/assets/prototype-states corepack pnpm --dir apps/web exec playwright test -c playwright.prototype.config.ts; done`
Expected: `after` 9 张、`states` 30 张，全部通过。

- [ ] **Step 3: 人工对照 `before` 与 `after`**：逐张比较，确认仅 A–D 相关区域变化；新对话页因示例历史会话增加而变化属预期。结论写入基准规范新节。
- [ ] **Step 4: 写状态截图集索引**：表格列出编号、交互、触发方式（自然 / 注入 + 故障名）、图片链接；开头说明截图用确定性示例数据，不代表后端能力。
- [ ] **Step 5: 更新基准规范**：新节写明本轮补齐范围与 spec 链接、前后截图链接与对照结论，以及规则："新交互先在 `/prototype` 设计并确认，TAP AI 实现按原型开发，不直接在实现中设计新交互；异常状态经 `prototypeFaults.ts` 在页面外注入。"
- [ ] **Step 6: 全量验证**

Run: `make check && make test && git diff --check`
Expected: 全部通过。另手动打开 `/prototype`，确认 Tapper、Agents、Skills、Library、Knowledge Graph、Test Management、Test Insights、Low Code Automation 均可到达，Tapper → Test Plan → Automation 跳转与悬浮助手 → Tapper 交接正常。

- [ ] **Step 7: Commit**：`docs: add prototype state gallery and baseline update`
