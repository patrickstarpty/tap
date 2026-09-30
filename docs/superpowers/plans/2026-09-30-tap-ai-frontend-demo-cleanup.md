# TAP AI 前端清理原型内容实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `apps/tap-ai-frontend` 只呈现由真实 API 驱动的产品界面：去掉 fixture 演示模式、写死的示例内容和虚构的“领域总览”图谱，并把实现组件从“原型”改名。

**Architecture:** 先去掉 api 模式下仍会显示的演示内容（快捷提问、侧栏身份、领域总览），再把依赖 fixture 模式的测试迁移到“真实组件 + 假后端”（`renderKnowledgeApp` + `fakeKnowledgeClient`），然后删除 fixture 分支和只为它服务的模块，最后统一改名。

**Tech Stack:** React 19、TypeScript、antd、TanStack Query、Vitest + Testing Library、Playwright。

**Spec:** [Tapper 推荐问题设计](../specs/2026-09-30-tapper-prompt-suggestions-design.md)（“实施顺序”第 1 步）；背景约定见 [产品原型基准规范](../../guides/2026-09-22-product-prototype-baseline.md)：原型只在 `apps/web` 的 `/prototype`，TAP AI 前端按原型实现，不承载示例数据。

## Global Constraints

- 只改 `apps/tap-ai-frontend`、`docs/`（不含 `docs/archive/`）和 `scripts/run-task14-acceptance.sh`；不改 `apps/web`、后端和契约。
- api 模式下的现有行为保持不变，除本计划明确列出的删除项：快捷提问、侧栏“Prototype team / PT”、Library 图谱的“领域总览”。
- 用户可见文案中英双语；删除后不得残留 “prototype” / “原型” / “demo” / “示例” 字样（Task 1 的守卫测试）。
- CSS 类名（`tap-` 前缀）不改。
- 测试迁移必须保留原测试的行为意图；只因 fixture 存在才有意义的用例可删除，但须在任务报告中逐条列出原因。
- 每个任务先写失败测试再实现；提交用小写祈使句 Conventional Commit，结尾附 `Co-Authored-By` 行。
- 单测命令：`corepack pnpm --dir apps/tap-ai-frontend exec vitest run <path>`；全量：`corepack pnpm --dir apps/tap-ai-frontend exec vitest run` 与 `corepack pnpm --dir apps/tap-ai-frontend exec tsc -b`。

## Review Focus

1. 只由 fixture 用例覆盖的 api 行为在迁移中丢失 → Task 3、4 报告须附“原用例 → 新用例 / 删除原因”对照表。
2. 去掉快捷提问后，新对话页布局出现空洞或错位 → Task 1 以截图确认（Task 6 重拍）。
3. 删除 `durable` 分支时误删 api 路径逻辑（如 `durableTestPlanPath()` 仍需保留） → Task 5 全量测试与 `tsc -b`。
4. 没有项目（`projectId === null`）时 Library 图谱标签页崩溃或回退到虚构图谱 → Task 2 `shows no graph without a project`。
5. 演示文案被重新引入 → Task 1 守卫测试 `keeps demo wording out of product copy`。

---

## 文件结构

| 文件 | 变化 |
| --- | --- |
| `src/widgets/tap/prototype/TapperChat.tsx` | 删除快捷提问区（Task 1） |
| `src/widgets/tap/prototype/PrototypeSidebar.tsx` | 侧栏身份改为“本地工作区”（Task 1） |
| `src/widgets/tap/prototype/copy.ts` | 删除演示 key，新增守卫测试（Task 1、5） |
| `src/widgets/tap/prototype/LibraryWorkspace.tsx`、`KnowledgeGraph.tsx` | 去掉领域总览与虚构图谱回退（Task 2） |
| `src/pages/TapperPage.tsx`、`TapperPage.test.tsx` | 删除，测试并入 `TapAiPage.test.tsx`（Task 3） |
| `src/widgets/tap/TapProductPrototype.interactions.test.tsx` | 迁移到 api 模式（Task 4） |
| `src/widgets/tap/TapProductPrototype.tsx` | 删除 fixture 模式（Task 5） |
| `sampleFiles.ts`、`sampleKnowledge.ts`、`knowledgeGraphData.ts`、`SampleKnowledgeGraph.*`、`artifacts/persistence.*` | 删除（Task 2、5） |
| 全部含 Prototype 的名称 | 改名（Task 6） |

以下路径均相对 `apps/tap-ai-frontend/`。

---

### Task 1: 去掉 api 模式下的演示文案

**Files:**
- Modify: `src/widgets/tap/prototype/TapperChat.tsx:1100-1113`、`src/widgets/tap/prototype/PrototypeSidebar.tsx:203-213`、`src/widgets/tap/prototype/LibraryWorkspace.tsx:180-185`、`src/widgets/tap/prototype/copy.ts`
- Create: `src/widgets/tap/prototype/copy.test.ts`
- Test: 相关组件现有测试文件

**Interfaces:**
- Produces: `PrototypeCopy.chat` 删除 `quickPrompts`、`suggestedPrompts`；`PrototypeCopy.navigation` 删除 `prototypeTeam`，保留 `localWorkspace`（en “Local workspace” / zh “本地工作区”）。

- [ ] **Step 1: 写失败测试**

```tsx
it("does not show hard-coded quick prompts on a new chat", () => {
  /* renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() }) → 无 "Summarize the life insurance underwriting rules"，无 role="group"/aria-label "Suggested prompts" */
});
it("shows a neutral workspace identity in the sidebar", () => {
  /* 侧栏含 "Local workspace"；无 "Prototype team"、无文本 "PT" */
});
it("describes the published graph without prototype wording", () => { /* LibraryWorkspace.durableGraph.test.tsx 追加：说明文案为 "Published source graph · nodes and relationships come from the service." */ });
```
```ts
// copy.test.ts
it("keeps demo wording out of product copy", () => {
  const values = collectStrings(PROTOTYPE_COPY); // 递归收集 en、zh 全部字符串
  for (const value of values) expect(value).not.toMatch(/prototype|demo|原型|演示|示例/i);
});
```
若正则误伤正当文案（例如“示例”用于真实功能），在报告中列出并收窄正则，不得放过演示文案。此守卫测试在 Task 5 删除死 key 之后才能通过：本任务先以 `it.fails` 标注并在注释写明“Task 5 移除 `chat.answer`、`library.example` 等后改为 `it`”。

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**：删除快捷提问区及两个 key；侧栏只显示 `localWorkspace`，头像位置改为 antd `DesktopOutlined` 图标（`aria-hidden`）；已发布图谱说明改为 en “Published source graph · nodes and relationships come from the service.” / zh “已发布的来源图谱 · 节点与关系来自服务。”。
- [ ] **Step 4: 全量测试与 `tsc -b` 通过**（受影响的旧断言按新文案更新）。
- [ ] **Step 5: Commit**：`fix: remove demo prompts and identity from tap ai`

---

### Task 2: 去掉“领域总览”与虚构图谱回退

**Files:**
- Modify: `src/widgets/tap/prototype/LibraryWorkspace.tsx:59-190, 541-560`、`src/widgets/tap/prototype/KnowledgeGraph.tsx`、`src/widgets/tap/prototype/copy.ts`
- Delete: `src/widgets/tap/prototype/SampleKnowledgeGraph.tsx`、`.css`、`.test.tsx`（当前无生产引用）
- Test: `src/widgets/tap/prototype/LibraryWorkspace.durableGraph.test.tsx`、`LibraryWorkspace.test.tsx`

**Interfaces:**
- Produces: `KnowledgeGraph` 的 `publishedData` 改为必填 prop，删除 `buildKnowledgeGraph` 回退与 `copy.library.illustrative`；`ProjectKnowledgeGraph` 不再有 `graphView` 状态，只显示“已发布来源图谱”（来源选择下拉保留）。`LibraryWorkspace` 在 `graphProjectId === undefined` 时，图谱面板显示 `<p role="status">`：en “No project is selected.” / zh “未选择项目。”。
- `knowledgeGraphData.ts` 中被 `publishedGraphData` 或 `KnowledgeGraph` 继续使用的类型/布局常量移到 `src/widgets/tap/prototype/graphLayout.ts`，其余（`buildKnowledgeGraph`、对 `sampleKnowledge.ts` 中 `SAMPLE_REPRESENTATIVE_EDGES` 的引用）与只为它服务的图谱 copy key（application、underwriting、beneficiary 等节点、关系与社区名）在本任务删除。fixture 模式此后也显示“未选择项目”，受影响的 fixture 用例在本任务按新行为更新。

- [ ] **Step 1: 写失败测试**

```tsx
it("offers only the published source graph", () => { /* durableGraph 测试：无 "Domain overview" / "领域总览" 按钮；直接显示来源下拉与已发布图谱 */ });
it("shows no graph without a project", () => { /* LibraryWorkspace 不传 graphProjectId，切到 Knowledge Graph → status "No project is selected."；无虚构节点如 "Underwriting" */ });
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 全量测试与 `tsc -b` 通过**。
- [ ] **Step 5: Commit**：`fix: show only published graphs in tap ai library`

---

### Task 3: 页面测试迁移到 api 模式并删除 `TapperPage`

**Files:**
- Modify: `src/pages/TapAiPage.test.tsx`、`src/pages/TapAiPage.tsx`、`src/app/providers.test.tsx`
- Delete: `src/pages/TapperPage.tsx`、`src/pages/TapperPage.test.tsx`

**Interfaces:**
- Consumes: `renderKnowledgeApp(ui, { api })`（`src/features/knowledge/testing/renderKnowledgeApp.tsx`）、`fakeKnowledgeClient()`（`src/features/knowledge/testing/fakeKnowledgeClient.ts`）。
- Produces: `TapAiPage` 不再接受 `conversationSource` prop，始终渲染 `<TapProductPrototype conversationSource="api" />`（prop 本身在 Task 5 删除）。

- [ ] **Step 1: 迁移测试**：`TapAiPage.test.tsx` 的 3 个用例去掉 `conversationSource="fixture"`；`TapperPage.test.tsx` 的 12 个用例全部改为渲染 `<TapAiPage />` + `fakeKnowledgeClient()` 后并入 `TapAiPage.test.tsx`。对依赖 fixture 本地数据的断言，改用假后端提供等价数据（会话、来源、Agent/Skill）。只描述 fixture 自身行为的用例（如 “keeps the explicit demo fixture Conversation across modules”）以等价的 api 行为重写（会话跨模块保持），无等价行为的删除并在报告中说明。`providers.test.tsx` 改用 `TapAiPage`。
- [ ] **Step 2: 删除 `TapperPage.tsx` 与其测试，`TapAiPage` 去掉 prop**。
- [ ] **Step 3: 全量测试与 `tsc -b` 通过**。
- [ ] **Step 4: Commit**：`test: cover tap ai pages through the api only`

---

### Task 4: 交互测试迁移到 api 模式

**Files:**
- Modify: `src/widgets/tap/TapProductPrototype.interactions.test.tsx`

**Interfaces:**
- Consumes: 同 Task 3。

- [ ] **Step 1: 把 `renderPrototype` 默认值改为 `"api"`**，逐个修复 46 个原 fixture 用例：纯界面行为（语言切换、侧栏收放、移动端抽屉、问题导航、模型选择、历史搜索等）只需提供假后端数据；依赖示例来源、内置 Agent/Skill 或本地快照（`loadPrototypeSnapshot`）的用例改为由假后端返回对应数据。参考文件内已有的 api 用例（如 L87、L124、L181）。
- [ ] **Step 2: 删除 `renderPrototype` 的参数**（只保留 api）。
- [ ] **Step 3: 全量测试与 `tsc -b` 通过**；报告附“原用例 → 新用例 / 删除原因”对照表。
- [ ] **Step 4: Commit**：`test: run tap ai interaction tests against the api`

---

### Task 5: 删除 fixture 模式

**Files:**
- Modify: `src/widgets/tap/TapProductPrototype.tsx`、`src/widgets/tap/prototype/copy.ts`、`src/widgets/tap/prototype/model.ts`、`src/widgets/tap/prototype/LibraryWorkspace.tsx`、`src/widgets/tap/prototype/CatalogWorkspace.tsx`
- Delete: `src/widgets/tap/prototype/sampleFiles.ts`、`sampleKnowledge.ts`（及测试）、`src/widgets/tap/prototype/artifacts/persistence.ts`（及测试）

**Interfaces:**
- Produces: `TapProductPrototype` 无 props；删除 `durable` 常量与所有 fixture 分支（清单见下），保留 `durableTestPlanPath()`。`CatalogWorkspace` 的 `durableDrafts` prop 删除，按原 `true` 行为。`LibrarySource.isExample` 删除。

删除项（行号为当前文件）：`conversationSource` prop（L932-939）；`useDocumentListQuery`/`documentSources`（L945、1604-1632）；`initialSnapshot` 与快照读写（L946-952、1472-1491）；`chat-1` 初值（L973-977、1009-1011）；`BUILT_IN_AGENTS`/`BUILT_IN_SKILLS`（L641-679、1126-1131）；`localSources`/`addLocalSource`/`nextLocalSourceId`（L1178-1206、2261-2270）；fixture 的 `sources`/`answerSources`（L1633-1656）；本地历史搜索、改名、删除（L1856-1907）；`createNewChat` 的 fixture 路径（L1915-1926）；`sendMessage` 的本地生成回答（L2208-2219）；Library 模块 fixture 路径（L2596-2600）；`AssistantResponse` 的静态回答兜底（L630-635）；其余 `durable ? a : b` 化简为 `a`，`if (!durable) return` 删除。copy 删除：`chat.answer`、`library.loadExamples`、`library.examplesLoaded`、`library.example`。

- [ ] **Step 1: 删除上述代码与模块**。
- [ ] **Step 2: 把 Task 1 的守卫测试由 `it.fails` 改为 `it`**，并确认通过。
- [ ] **Step 3: 全量测试、`tsc -b`、`corepack pnpm --dir apps/tap-ai-frontend run check`（若存在）通过**；`rg -n "fixture|durable" src/widgets src/pages` 只剩 `durableTestPlanPath` 相关结果。
- [ ] **Step 4: Commit**：`refactor: remove the fixture demo mode from tap ai`

---

### Task 6: 改名、截图与文档

**Files:**
- Rename：
  - `src/widgets/tap/TapProductPrototype.tsx` → `src/widgets/tap/TapperWorkspace.tsx`（组件 `TapProductPrototype` → `TapperWorkspace`），`.css` 与 4 个测试文件同步（`TapperWorkspace.*.test.tsx`）
  - 目录 `src/widgets/tap/prototype/` → `src/widgets/tap/workspace/`
  - `PrototypeSidebar` → `WorkspaceSidebar`（文件同名）；`PrototypeCopy` → `WorkspaceCopy`；`PROTOTYPE_COPY` → `WORKSPACE_COPY`
  - `package.json` 脚本 `prototype:capture` → `ui:capture`；`playwright.prototype.config.ts` → `playwright.capture.config.ts`；`tests/e2e/tap-ai-demo-capture.spec.ts` → `tests/e2e/ui-capture.spec.ts`
- Modify: `README.md`、`docs/guides/2026-09-27-frontend-developer-onboarding.md`、`docs/guides/2026-09-13-tapper-developer-guide.md`、`docs/guides/2026-09-06-file-type-icons.md`、`docs/guides/2026-09-22-product-prototype-baseline.md`、`docs/guides/2026-09-04-customer-prototype-demo-guide.md`、`scripts/run-task14-acceptance.sh` 中指向上述旧路径或脚本名的引用（只改 TAP AI 前端路径，`apps/web` 的原型路径不动）
- Modify: `docs/architecture.md`：`apps/tap-ai-frontend` 一行补充“只呈现 API 驱动的产品界面，交互以 `apps/web` `/prototype` 为设计来源”

- [ ] **Step 1: 改名**（用 `git mv`），更新全部 import 与测试描述中的 “prototype” 措辞。
- [ ] **Step 2: 验证**：`rg -in "prototype" apps/tap-ai-frontend/src apps/tap-ai-frontend/tests apps/tap-ai-frontend/*.ts apps/tap-ai-frontend/package.json` 无结果；全量测试与 `tsc -b` 通过。
- [ ] **Step 3: 重拍截图**：`corepack pnpm --dir apps/tap-ai-frontend run ui:capture`，确认 Library 图谱截图只含已发布来源图谱、新对话页无快捷提问且布局正常（用 Read 查看 PNG）。
- [ ] **Step 4: 更新文档**，`git diff --check` 通过；`make check` 通过。
- [ ] **Step 5: Commit**：`refactor: rename the tap ai workspace away from prototype`
