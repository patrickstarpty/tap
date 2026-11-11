# Tapper Direct Browser Execution Implementation Plan

> **For agentic workers:** Use executing-plans in this session. The user requested the primary agent to implement personally; do not delegate.

**Goal:** Complete public browser tasks through an observe/action/verify loop, with persisted BDD progress and independent replay.

**Architecture:** Replace the browser task's default OpenCode handoff with native structured browser tools. Reuse the existing project-scoped BrowserApiClient, workflow event journal, ModelGateway and immutable replay bundles; business completion is separate from replay.

**Tech Stack:** Existing Python/FastAPI, React/TypeScript, Playwright 1.62.1. No additional dependency.

**Spec:** ../specs/2026-11-08-tapper-direct-browser-execution-design.md

## Global Constraints

- Loopback demo; preserve public-web/private-network policy and existing shared services.
- No FWD-specific selectors or prices in product code.
- No online shell or coding tools; no automatic re-execution during script generation.
- Preserve original observations and failed actions; only real receipts may mark steps complete.

## Task 1: Observable controls and business result evidence

Files: `deploy/browser/inspector.mjs`, browser service contracts/runtime and their tests.

- [x] Add failing real-browser regression for readonly combobox: observation includes readonly and input value; expansion/option click changes value.
- [x] Add bounded read/assert actions returning DOM facts and assertion failures, preserving existing action receipt shape.
- [x] Record session trace and result/screenshot evidence under the session's project/task authority. Add tampering/cross-task tests.
- [x] Verify focused JS and backend browser tests.

## Task 2: Native browser loop

Files: new workflow browser loop/runtime and unit tests; built-in model tool registry.

- [x] Test the real loop with a deterministic gateway: plan → navigation → current control click → read/assert → finish. Assert browser actions, durable progress, extracted results, and absence of coding tools.
- [x] Implement structured plan/action/read/finish tools with current-observation references, bounded calls, task deadline, cancellation and existing journaled model usage.
- [x] Test stale references, readonly fill, failed assertions, invented output, repeated failing actions and cancellation before subsequent dispatch.
- [x] Generate a frozen script from successful recorded actions and assertions without visiting the site again. Test that compilation performs no browser calls and excludes failed attempts.

## Task 3: Product integration

Files: `entrypoints/tapper_runtime.py`, workflow dispatch, artifact projection, affected frontend contracts and tests.

- [x] Wire native runtime as default browser route. Preserve explicit frozen replay through the existing executor.
- [x] Project live business result and session evidence separately from script/replay; retain BDD actions and expandable diagnostics.
- [x] Test that script failure preserves business completion and that replay begins only on explicit request.
- [x] Update ADR-038, architecture and affected roadmap rows with actual implemented scope.

## Task 4: Acceptance

- [x] Run narrow tests then required cross-module checks and core regression.
- [x] Run deterministic full Tapper journey with dynamic form, multi-page navigation, wrong expected value, missing input, cancellation and separate replay. Receipt tests assert live completion never queues a second execution.
- [ ] Run original FWD public quote through natural language entry using bounded paid model calls. Verify every requested value and actual nonzero monthly price.
- [ ] Export result, screenshot, trace and original script; explicitly replay with zero additional model calls.
- [ ] Settle usage, confirm owned resources stopped and original 14 services restored, update handoff, integrate accepted changes into develop.
