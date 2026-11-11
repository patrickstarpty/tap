# LCA BDD editor implementation plan

> For agentic workers: use the repository subagent-driven-development or executing-plans workflow and TDD.

**Goal:** Deliver an integrated business-step/action editor in TAP.
**Architecture:** Optional validated BDD grouping references existing canonical actions. Three-pane UI edits the same persistent definition and deterministic executor.
**Tech Stack:** React/TypeScript, FastAPI/Pydantic, SQL storage, Playwright.
**Spec:** ../specs/2026-11-11-lca-bdd-editor-design.md

## Global constraints

Retain stable action IDs, legacy bytes and immutable published snapshots. No new execution engine or unsupported operations. Recording append cannot discard unrelated actions. No save or editor operation starts execution.

- [x] Backend: test valid grouping and rejected duplicate/missing/out-of-order action IDs; implement bounded optional model, verify snapshot roundtrip and legacy digest.
- [x] UI model: test group reorder and cross-group move, legacy view-only normalization, collision-safe recording append, capacity rejection; implement pure helpers.
- [x] Editor: test selection, group editing and contextual action properties; replace flat form with three panes while preserving TAP styling and parameter editing.
- [x] Integrate: recording destination selection and append, grouped frozen run results, explicit save/replay controls.
- [x] Verify: focused red/green tests, regression/build checks, real browser workflow and screenshots/video, review and delivery documentation.

See [acceptance](../../guides/2026-11-11-lca-bdd-editor-validation.md). Final check results and development-branch publication are recorded with delivery.
