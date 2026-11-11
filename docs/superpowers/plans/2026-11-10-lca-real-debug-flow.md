# LCA Real Debug Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Deliver real recording, editable canonical steps/assertions, saved debug flows and explicit isolated replay with evidence.

**Architecture:** TAP non-AI LCA owns canonical drafts/snapshots and references existing browser execution authority. AA owns shared controller fencing and browser-control-v1 human receipts. Generated Playwright is derived, not a second editable truth.

**Tech Stack:** Existing Python/FastAPI/SQLAlchemy/MySQL, React/TypeScript and pinned Playwright. No new framework or paid model dependency.

**Spec:** [Approved architecture](../specs/2026-11-10-lca-real-debug-flow-design.md).

## Global Constraints

- Preserve user changes, replay guard, simulated history and AA worktree. No commits or pushes.
- Use project authorization, server-side credentials and AA local gateway capability adapter. Never expose service token to UI.
- Owned non-secret fixture acceptance only. No silent real-site or credential support.
- Canonical IDs, deterministic safe generation, immutable snapshots and explicit replay. Unknown outcomes never automatically repeat.
- Run TDD per task, focused checks before broader gates. Missing live dependencies must be reported, never replaced by simulated acceptance.

## Task 1: Canonical definition and deterministic generation

Files: `apps/backend/src/tap_platform/lca/definition.py`, `generator.py`, `__init__.py`; tests `apps/backend/tests/unit/test_lca_definition.py`, `test_lca_generator.py`.

- [x] Write failing tests for six operations, strict typed arguments, target references, parameter declarations, stable IDs, origin limits and safe strings.
- [x] Run focused tests and observe missing implementation failures.
- [x] Implement pure canonical validation and deterministic Playwright generator, including canonical step mapping and explicit assertion operations.
- [x] Verify repeat generation byte identity, injection resistance, exact parameter resolution, malformed values and per-step IDs.
- [x] Independent spec/code review; address findings; run Ruff/mypy scoped checks.

## Task 2: Persisted debug intent and recoverable execution linkage

Files: `apps/backend/src/tap_platform/lca/store.py`, `service.py`; TAP migration with next free revision; tests `apps/backend/tests/unit/test_lca_store.py`, `test_lca_service.py`.

- [x] Define optimistic draft updates, immutable canonical snapshots and persisted DebugRun intent before browser enqueue.
- [x] Write red tests for revision conflicts, cross-project isolation, key/body conflicts, lost response and enqueue/link crash.
- [x] Implement under TAP metadata/migration conventions; reuse BrowserService frozen-bundle execution API through an injected narrow adapter.
- [x] Reconcile same frozen input under original idempotency keys, preserving unknown outcomes and original execution link.
- [x] Review and test persistence; isolated MySQL migration acceptance required before claiming full persistence completion.

## Task 3: Shared recording and authorized HTTP boundary

Files: `apps/backend/src/tap_platform/lca/recording.py`, `http.py`; backend app integration; contract tests.

- [x] Consume AA browser-control-v1 terminal receipts, never raw unacknowledged UI commands. Freeze pre-action target and post-action evidence lineage.
- [x] Write red tests for duplicate receipt, ordered sequence, stop watermark, late outcomes, unknown exclusions and secret-field rejection.
- [x] Implement recording projection and project/actor-authorized APIs through shared gateway adapter.
- [x] Add contract tests for rejected same-project actors, cross-project reads and evidence permissions.
- [x] Resolve integration with AA without creating a second controller; review exact control/receipt contracts.

## Task 4: Real editor and execution history

Files: `apps/web/src/features/lca/` API/types/editor/history components/tests; existing app navigation integration.

- [x] Write failing UI tests for loading backend flows, editing typed steps/assertions, optimistic conflict and explicit run.
- [x] Implement save/reload and immutable real history; retain separately labelled prototype simulation.
- [x] Connect shared recording viewer/control; unsupported actions explicitly stop/reject, not disappear.
- [x] Test failed assertions, missing evidence, unknown execution and authorization errors without success claims.
- [x] Run frontend boundary, TypeScript/build and affected tests; independent review.

## Task 5: Owned-site real acceptance and final review

Files: focused owned browser acceptance harness/tests and delivery guide.

- [x] Run real record → edit → assert → save/reload → explicit debug replay → evidence journey on owned site.
- [x] Assert recording submits once; save/generation submit zero times; explicit replay submits once; wrong assertion genuinely fails.
- [x] Exercise duplicate request, stale epoch, cancellation and lost acknowledgement without duplicate dispatch.
- [x] Run make test, relevant backend/frontend gates and broad independent review.
- [x] Report exact evidence and unresolved dependencies, leaving incomplete tasks unchecked.

## Execution ledger

- 2026-11-10: Written design approved. Ruling: implement independent canonical core first while AA owns control transport; this avoids concurrent browser edits and does not count as end-to-end recorder completion.
- Ruling: Preserve no-commit instruction despite workflow skill examples; all changes remain reviewable in the existing requested develop checkout.

- Core checkpoint: canonical generation, SQLite durability, service recovery, receipt projection and injectable HTTP adapter implemented; review findings on port-zero origin and incomplete receipt validation corrected with red/green tests. At that checkpoint real integration and UI were incomplete; final gates are recorded below.
- Environment: authorized removal of exactly two verified-unused historical browser-core networks freed Docker address pools. Shared image had incompatible newer video evidence, so a separately tagged develop image was built and the isolated real replay test passed (including intentional assertion failure and screenshot/trace/result evidence). No existing image, container or data volume removed.

- Gateway/editor checkpoint: trusted validation-only scope and same-origin app wiring implemented. Independent review findings fixed: reload recovery uses durable run ID and original execution key, flow switches fence stale history, and transient poll errors explicitly pause with Refresh. At that checkpoint shared recorder transport, migration and UI acceptance were pending; all passed in the combined checkout below.

- Combined integration checkpoint: backend and browser HTTP share the same BrowserService/input vault. Owner-bound recording context persists draft version and declared origins. Shared navigation/select, capability renewal, stop/cancel fencing and explicit editor import are implemented. Real local Chromium backend journey passed with exact submit counts, fresh-context pass/fail/pass replays and immutable evidence; rendered UI and actual MySQL subsequently passed as recorded below.

## Final owned-site milestone evidence

- Full rendered UI through the real gateway/shared worker/Chromium: `apps/tap-ai-backend/tests/integration/test_lca_recording_ui.py`, one final fresh pass in 20.61 seconds. Normal Take control preserved its capability through admission and confirmation. Record → drain → import → parameter/assertion edit → save → pass/fail/corrected-pass replay → reload/history/evidence passed. Four actual submits used four isolated cookie contexts; three immutable snapshots/executions remained distinct. Recording and failed history had no horizontal overflow at 390 pixels.
- Backend real recording acceptance: `apps/backend/tests/integration/test_lca_real_recording.py`, one pass in 10.80 seconds; duplicate suppression, in-flight drain, save/generate without execution, fresh contexts, evidence hashes, owner restrictions and immutable earlier failure verified.
- Real MySQL 8.4.6: `scripts/verify-aa-lca-mysql.py` passed 0009 → 0010, six-table persistence after reconnect, 70,023-byte payload, empty downgrade/reupgrade and populated downgrade retention refusal. Only owned temporary resources were removed.
- Final combined `make check` passed (438 tests, 47 skips); `make test` passed 460/83/69/9 groups; configured full Web suite passed 285 tests. Independent runtime/gateway/UI reviews and affected lint/type/build checks passed.
- Acceptance is limited to owned synthetic non-secret sites and local validation identity. No production deployment, paid model call, commit, merge or push occurred. Original worktrees remain preserved; the reviewed combined source is prepared for durable sibling preservation, not silently installed into develop.
