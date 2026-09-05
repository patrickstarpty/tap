# Task 2 report — Tapper conversations and context controls

## Status

Implemented and committed as `ea3d81a` (`feat: add Tapper conversations and context controls`).

## Delivered

- Added `PrototypeSidebar` as the single product navigation surface.
  - Orders Tapper, New Chat, Agent, Skills, Library, Test Management, and Low Code Automation in one navigation.
  - Supports expanded/collapsed states, English/Chinese switching, and inactive conversation history.
  - Selecting history restores the prior conversation; New Chat creates an independent empty conversation without mutating prior turns or context.
- Added controlled `TapperChat`.
  - Suggested prompts only fill and focus the composer.
  - Send/Enter appends a turn only to the active conversation and preserves the existing BDD/automation handoffs.
  - The `Add to message` menu exposes exactly `Add from Library`, `Use Agents`, and `Use Skills`.
  - Each picker is searchable and writes source, Agent, or Skill IDs to the active `Conversation`; selected items render as composer context chips and restore with that conversation.
- Added controlled `KnowledgeSourcesPanel`.
  - Lists ready sources from the existing read-only document query.
  - Search filters only visible rows; checkbox selection remains conversation-owned state.
  - The right panel has no management action; Library remains reachable only from left navigation or the composer's `Add from Library` flow.
- Added reusable built-in catalog fixtures, including `Life Underwriting Analyst` and `BDD Scenario Design`.
- Kept Agent, Skills, and Library management as simple placeholders. No Task 3 create/edit/graph UI was implemented.
- Continued using Task 1 `PROTOTYPE_COPY`, with English default and a working Chinese switch for the current shell/chat acceptance test.
- Preserved Test Management, Low Code Automation, BDD import, automation handoff, editable automation steps, and page-local-only state.
- Updated the Tapper page navigation assertion from the stale `Primary` label to the approved `Product` contract.

## TDD evidence

Before production implementation, added searchable Agent and Skill picker tests and ran:

```sh
corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx
```

RED was confirmed: all 11 tests failed against the pre-Task-2 UI, and both new picker tests failed specifically because `Add to message` did not exist.

After implementation, the Task 2 interaction slice passed:

```sh
corepack pnpm --dir apps/web exec vitest run src/widgets/tap/TapProductPrototype.interactions.test.tsx -t "defaults|fills|shows|starts|filters|adds a searchable"
```

Result: 8 passed, 3 skipped by the test-name filter.

## Regression and static verification

```sh
corepack pnpm --dir apps/web exec vitest run src/pages/TapperPage.test.tsx
```

Result: 11 passed.

```sh
corepack pnpm --dir apps/web exec prettier --check \
  src/widgets/tap/TapProductPrototype.tsx \
  src/widgets/tap/TapProductPrototype.css \
  src/widgets/tap/TapProductPrototype.interactions.test.tsx \
  src/widgets/tap/prototype/PrototypeSidebar.tsx \
  src/widgets/tap/prototype/TapperChat.tsx \
  src/widgets/tap/prototype/KnowledgeSourcesPanel.tsx \
  src/pages/TapperPage.test.tsx

corepack pnpm --dir apps/web exec eslint \
  src/widgets/tap/TapProductPrototype.tsx \
  src/widgets/tap/TapProductPrototype.interactions.test.tsx \
  src/widgets/tap/prototype/PrototypeSidebar.tsx \
  src/widgets/tap/prototype/TapperChat.tsx \
  src/widgets/tap/prototype/KnowledgeSourcesPanel.tsx \
  src/pages/TapperPage.test.tsx

corepack pnpm --dir apps/web exec tsc -p tsconfig.json --noEmit
git diff --check
```

Result: all passed.

The required one-time Impeccable detector run returned `[]` for the changed UI files.

## Expected remaining RED and concerns

The unfiltered interaction file now reports 8 passed and 3 failed. The only failures are the pre-existing Task 3 acceptance tests:

- Agent search/create/edit management
- Skill search/create/edit management
- Library thumbnails/Knowledge Graph workspace

Per the Task 2 scope ruling, these tests were neither skipped nor implemented; Task 3 owns making them GREEN.

The pre-existing untracked `.impeccable/` directory was not modified or staged. No backend write, persistence, authentication, or network mutation was added. Responsive/mobile presentation and complete cross-page translation remain Task 4 scope.

## Fix round 1 — review findings

### Changes

- Conversation history now includes inactive sessions with selected source, Agent, or Skill context even when they have no sent turns. Labels include the stable conversation ordinal and selected-context count, and the unreachable history `aria-current` branch was removed.
- The custom add menu now focuses its first item on open; supports Arrow Up/Down, Home, End, and Escape; and restores focus to the add trigger on dismissal.
- Context dialogs now contain keyboard focus at both Tab boundaries, close on Escape, and restore focus to the add trigger.
- Picker options derive `aria-selected` from the active conversation's source, Agent, and Skill ID arrays.
- Removed the right-panel `Manage knowledge` button and its callback. A regression assertion confirms that the action is absent.

### TDD RED

The new focused interaction tests were first run before their production changes. Result: 4 failed, covering the missing context-only history entry, missing menu focus/navigation, missing dialog keyboard operation/focus restoration, and hard-coded false `aria-selected` state.

The Tapper page regression was also run before removing the right-panel action. Result: 1 failed because `Manage knowledge` was still present.

### GREEN and regression evidence

Commands used the worktree's installed binaries directly after the local pnpm wrapper stopped before Vitest because its runtime wanted to purge `node_modules` in a non-interactive shell.

```sh
cd apps/web
./node_modules/.bin/vitest run \
  src/widgets/tap/TapProductPrototype.interactions.test.tsx \
  -t "defaults to English|fills and focuses|shows the integrated|starts a new|restores source|filters the Knowledge|adds a searchable|supports keyboard|contains dialog|reports selected"
```

Result: 12 passed, 3 skipped by the exact Task 2 name filter.

```sh
./node_modules/.bin/vitest run src/pages/TapperPage.test.tsx
```

Result: 11 passed.

```sh
./node_modules/.bin/eslint \
  src/widgets/tap/prototype/PrototypeSidebar.tsx \
  src/widgets/tap/prototype/TapperChat.tsx \
  src/widgets/tap/prototype/KnowledgeSourcesPanel.tsx \
  src/widgets/tap/TapProductPrototype.tsx \
  src/widgets/tap/TapProductPrototype.interactions.test.tsx \
  src/pages/TapperPage.test.tsx

./node_modules/.bin/prettier --check \
  src/widgets/tap/prototype/PrototypeSidebar.tsx \
  src/widgets/tap/prototype/TapperChat.tsx \
  src/widgets/tap/prototype/KnowledgeSourcesPanel.tsx \
  src/widgets/tap/TapProductPrototype.tsx \
  src/widgets/tap/TapProductPrototype.css \
  src/widgets/tap/TapProductPrototype.interactions.test.tsx \
  src/pages/TapperPage.test.tsx

./node_modules/.bin/tsc -b --pretty false
```

Result: all exited 0; Prettier reported all matched files formatted.

The unfiltered interaction file now reports 12 passed and exactly 3 failed. Those failures remain the intentionally out-of-scope Task 3 Agent management, Skill management, and Library/Knowledge Graph cases. No Task 3 UI was implemented or skipped.
