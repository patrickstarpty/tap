---
target: Tapper
total_score: 26
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 2
target_identity: "file:/Users/patrick/Desktop/Workspace/ai-native/tap/apps/web/src/pages/TapperPage.tsx"
target_fingerprint: "sha256:e397df8aa2a2b335f62a99d6a71b94f83618cb5d9623af6244ccef536ab9e9b5"
target_path: /Users/patrick/Desktop/Workspace/ai-native/tap/apps/web/src/pages/TapperPage.tsx
timestamp: 2026-09-01T17-11-19Z
slug: apps-web-src-pages-tapperpage-tsx
---

# Tapper UI Critique

评审方法：将 Tapper 作为 Operate 型知识工作台，综合独立人工设计评审、真实页面桌面与 390px 窄屏检查、最新 Web Interface Guidelines 源码审计，以及 Impeccable CLI detector；没有修改产品代码。

## Executive summary

Tapper 的主要问题不是单纯配色，而是产品主次关系仍停留在 Demo 阶段：三块同权卡片同时铺开选资料、得到回答、核验原文，空态时留下两大片无效空间；移动端又把桌面三栏直接纵向堆叠。它有可信知识产品的好底子，但当前更像能工作的组件集合，而非围绕核心任务精心编排的成熟产品。

- Visual specificity: 3 / 4
- Nielsen usability: 26 / 40 — Acceptable
- Cognitive load: 1 / 8 failed — low to moderate
- P0: 0
- P1: 2
- Detector warnings: 2, both triaged as semantic false positives

## Nielsen heuristic scores

| Heuristic                       | Score | Evidence                                                                                            |
| ------------------------------- | ----: | --------------------------------------------------------------------------------------------------- |
| Visibility of system status     |   3/4 | Loading, failure and answer-pending states are explicit.                                            |
| Match with the real world       |   3/4 | Source-answer-original-text is natural; Chunks and SHA-256 surface too early.                       |
| User control and freedom        |   3/4 | Source clearing, deletion cancellation and citation focus restoration exist.                        |
| Consistency and standards       |   3/4 | Panel language is consistent, but AntD, Tailwind and handwritten CSS are not yet one closed system. |
| Error prevention                |   3/4 | Empty questions and no-source submissions are blocked; deletion is confirmed.                       |
| Recognition rather than recall  |   2/4 | Zero-source and failure states require users to infer that Knowledge Library is the recovery path.  |
| Flexibility and efficiency      |   2/4 | Search, select-all and Enter submit exist; expert acceleration paths do not.                        |
| Aesthetic and minimalist design |   3/4 | Restrained palette, but empty answer and original-text panels dominate the first screen.            |
| Error recognition and recovery  |   3/4 | Retry exists, but does not explain which trust-chain step failed.                                   |
| Help and documentation          |   1/4 | No onboarding, sample question or discoverable task help.                                           |

## Cognitive load

One of eight checks failed: recognition rather than recall. There is no single decision group with more than four peer choices. The source list can reach 20 items, but search and bulk selection make it a filterable dataset rather than a raw menu.

## Emotional journey

1. Entry feels quiet, credible and research-oriented.
2. The left-to-right evidence chain is immediately understandable.
3. The current runtime failure state creates a sharp low point: the question action is disabled and Retry is the only recovery.
4. The strongest intended moment is opening a citation and verifying original text.
5. Focus restoration completes the interaction, but the first-use path still lacks a clear next action.

## Strengths

1. The product promise is clear: answers are grounded only in selected sources.
2. Source, answer and evidence are real functional responsibilities, not decorative panels.
3. Accessibility intent is strong: landmarks, labels, aria-live, visible focus and 44px targets are present.

## Priority issues

### P1 — Zero-source and loading-failure states do not lead to an actionable next step

The first screen can show an error, disabled source actions and a disabled Ask button at once, with Retry as the only action. Separate no-content from service-unavailable states and give each a clear recovery CTA.

### P1 — Static three-column layout gives nonexistent content first-class space

The desktop grid is fixed at 280px / fluid / 360px in apps/web/src/app/styles.css:93. Before a question exists, both the answer surface and original-text panel are large empty regions. Evidence should appear on demand after a citation is selected.

### P2 — Mobile stacks desktop information architecture instead of redesigning the task

At 390px the source card consumes roughly 615px and the question region starts around y=748. The breakpoint at apps/web/src/app/styles.css:596 only turns three columns into one. Use progressive disclosure: source and original text as sheets, with the composer near the viewport.

### P2 — Visual character exists, but the design system does not close the loop

apps/web/src/app/theme.ts:3 contains a small AntD token set, while colors, borders, shadows, spacing and typography continue across more than 600 lines of apps/web/src/app/styles.css, alongside Tailwind utilities. Georgia headings, AntD controls and paper cards read as assembled rather than authored together.

### P2 — Trust information is not layered by audience

Everyday users need availability, update time and answer provenance first. Chunks, Revision ID and SHA-256 belong in advanced verification disclosure.

### P3 — Document structure and navigation state are not fully productized

apps/web/src/pages/TapperPage.tsx:18 has no H1 or skip link. The Ask/Knowledge Library tab state is not mapped to the URL, so refresh, sharing and browser history do not restore context.

## Persona red flags

- Alex: empty evidence chrome, cross-tab recovery and lack of recent/common source shortcuts slow expert work.
- Sam: semantic foundations are good, but success-flow announcements, citation replacement and source readiness were not verified end to end.
- Jordan: understands the three-step model but receives no first-source path or sample question.
- Riley: when the API is unavailable, 0 ready / 0 processing / 0 failed can make unknown status look like a valid zero state.

## Minor observations

- The 390px layout has no horizontal overflow.
- The mobile header remains visually stable but takes meaningful first-screen height.
- The global outline reset is paired with focus-visible replacements; AntD edge cases still merit browser testing.
- The trustworthy, specific Chinese product copy should be extended to empty and recovery states.

## Detector and guideline findings

The mandatory detector found two side-tab warnings at apps/web/src/app/styles.css:257 and :371. They correspond to a pending-status marker and a blockquote marker, so both are semantic false positives and should not drive redesign.

Current Web Interface Guidelines findings:

- apps/web/src/pages/TapperPage.tsx:18 — no skip link and no H1.
- apps/web/src/pages/TapperPage.tsx:21 — tab state is not represented in the URL.
- apps/web/src/features/knowledge/components/SourcesPanel.tsx:79 — search input lacks name/autocomplete metadata.
- apps/web/src/features/knowledge/components/QuestionComposer.tsx:34 — question textarea lacks name/autocomplete metadata.
- apps/web/src/features/knowledge/components/DocumentTable.tsx:66 — numeric Chunks column does not use tabular numerals.

## Three redesign directions

### A. Evidence Desk — recommended

Make the answer canvas primary, collapse sources into a rail, open original text only when a citation is selected, and use sheets for sources/evidence on mobile. Preserve the warm editorial tone but unify it with semantic tokens and a clearer hierarchy. This best protects Tapper's evidence-first differentiation.

### B. Conversational Scholar

Use a single conversation stream, a source-scope bar above the composer, inline evidence cards and a side sheet for full text. This is easiest for general users, but weakens parallel evidence comparison and risks resembling generic AI chat.

### C. Evidence Operations

Build a denser, adjustable multi-pane console with source sets, processing status, saved queries and an evidence queue. This best serves research, legal and audit power users, but has the highest scope and onboarding cost.

## Run notes

- Browser coverage: desktop Ask, desktop Knowledge Library, 390x844 Ask, keyboard focus.
- Current local data source was unavailable; upload, successful answer, original citation and deletion paths were not mutated or exercised.
- Browser exposed no supported DOM mutation capability, so the optional critique overlay was skipped.
- No additional live server was started for the overlay.
- Product code changes: none.

## Decision questions

1. Should Tapper become A Evidence Desk, B Conversational Scholar, or C Evidence Operations?
2. Is the primary user a general knowledge worker or a research/legal/audit expert?
3. Should the first redesign optimize desktop first, or desktop and mobile together?
