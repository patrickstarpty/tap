# AA Live Browser Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Execute task-by-task with red/green tests and independent review.

**Goal:** Deliver watch → exclusive human takeover → handback → same Agent continues on an owned fixture site.

**Architecture:** Extend the existing Browser API/worker and OpenCode+Playwright runtime. A durable controller epoch fences admission and dispatch; a trusted local gateway mediates human capabilities and read-only frames. LCA consumes the same acknowledged receipts.

**Tech Stack:** Python/FastAPI/SQLAlchemy, OpenCode 1.19.5, Playwright/Node, React/TypeScript.

**Spec:** `docs/superpowers/specs/2026-11-10-aa-live-browser-control-design.md` (written design approved by user).

## Global Constraints

- Preserve all existing worktree edits; no commits, merges, shared service changes, paid model calls or production claims.
- Loopback owned fixture only; synthetic nonsecret data. Reject password input before persistence.
- No raw CDP exposed to UI. No automatic repeat of unknown actions. Original deadlines/cost remain bounded.
- LCA owns its editor/drafts/generator in its separate checkout; AA owns shared browser control here.
- Ruling: use parallel native workers for independent backend and OpenCode feasibility tasks; reserve shared integration changes for the coordinator. No user questions for minor implementation choices.

## Task 1: Durable controller and dispatch fencing

Files: browser_automation/{contracts,store,service,worker}.py; new control.py if needed; migrations/versions/0009_browser_control.py; unit/test_browser_control.py; unit/test_browser_runtime.py.

Contract: session control states and browser-control-v1 receipt follow spec §3–4. A dispatch reservation and control transition take the same session lock. Old epoch queued actions are rejected; unknown dispatch outcomes block takeover.

- [x] Add tests with real SQLite ledger and fake runtime. Assert takeover blocks new agent admission, same idempotency key returns same receipt, stale epoch cannot dispatch, and outstanding reservation prevents human_active. Test an already-reserved action settling exactly once.
```python
assert submit_count == 1
assert stale_epoch_request.status == "rejected"
assert controller_state != "human_active" or outstanding_count == 0
```
- [x] Run `uv run --no-sync --project apps/backend pytest -q apps/backend/tests/unit/test_browser_control.py` and capture expected failures.
- [x] Add explicit migration, typed state/receipt contract, atomic control transitions and dispatch reservation; adapt worker only through that contract. Preserve legacy sessions and existing bundle replay.
- [x] Run new tests and existing service/runtime/API suites; lint/typecheck affected backend. Independent review before downstream use.

## Task 2: OpenCode no-paid continuity gate

Files: workflows/adapters/opencode_runtime.py and local_opencode.py only if proven necessary; deploy/browser/opencode-client.mjs; tests/unit/test_opencode_runtime.py; deploy/browser/test/opencode-client.test.mjs; new tests/unit/test_browser_control_continuity.py.

Contract: active provider session and browser session survive human waiting; cancellation/absolute deadlines remain effective. Do not use terminal return/finally cleanup to pause.

- [x] Build a deterministic tool/model fixture that records provider_session_id, browser_session_id and model/action counters around takeover.
```python
assert before.provider_session_id == after.provider_session_id
assert before.browser_session_id == after.browser_session_id
assert human_interval.model_requests == 0
assert handback.first_write.observation_sequence > before.observation_sequence
```
- [x] Execute probe with pinned local OpenCode using only synthetic model transport; no environment API keys or real provider network.
- [x] If current continuation fails, implement minimal wait/continue protocol without replacing engine; add tests first for cancel/timeout while waiting and late old-generation actions.
- [x] Report exact successful probe or blocking protocol fact before UI task starts. Independently review.

## Task 3: Trusted gateway, frames and human commands

Files: browser_automation/http.py, control.py, local.py, docker.py and inspector bridge; workflows HTTP routes/adapters/browser_client.py; tests/contract/test_browser_control_api.py; integration/test_browser_control_runtime.py.

Contract: trusted local gateway grants short-lived opaque per-panel capability bound to task/session/controller/actions; actor is not accepted from arbitrary JSON. Frame has sequence/time/viewport and is ephemeral. Commands reference current epoch and observed frame. Password fields are rejected before values reach persistence.

- [x] Test cross-project/task denial, forged actor, expired capability, stale frame, old epoch, cancel/expiry revocation; verify no supplied secret in ledger/log/evidence.
```python
assert unauthorized.status_code in (403, 404)
assert stale_frame.status_code == 409
assert secret not in persisted_bytes
```
- [x] Implement same-origin/CSRF checks and authenticated delegation, bounded frame reads and controlled click/type/scroll. Do not reuse raw CDP as human transport.
- [x] Test real owned Chromium page, target identity captured before navigation, command_id/action sequence, and no automatic retry. Verify trace/video privacy gates; keep sensitive login disabled if isolation cannot be proven.
- [x] Run contract/integration suites and independent review; send concrete receipt schema to LCA worker.

## Task 4: RunDetail live control UX

Files: features/workflows/api/client.ts; components/RunDetail.tsx; new BrowserControl.tsx and tests; generated contracts only via existing generator.

- [x] Add tests for observing, requesting takeover, waiting for confirmed control, human mode, handback, stale/disconnected frame, cancel and expiry.
```tsx
expect(screen.queryByRole('button', {name: 'Hand back'})).toBeNull();
// Only confirmed human_active grants writable controls.
```
- [x] Implement frame view, timestamp/controller/deadline and server-derived actions. Closing panel disconnects view only. Never imply click acknowledgment equals completed handoff.
- [x] Typecheck/lint and run focused UI suites. No implementation before Task 2 gate.

## Task 5: End-to-end acceptance and integration

Files: scripts/browser_core_site.mjs; browser-core E2E fixtures and test; architecture.md and affected product-roadmap row only after code evidence.

- [ ] Add deterministic local fixture with explicit submit counter, agent step, human change, handback and final verification. Reset before independent zero-model replay.
- [ ] Assert one intended submission; zero additional on repeated requests, competing panels, stale epoch, reconnect and unknown outcome. Test manual unrecordable prerequisite separately from successful synthetic recorded replay.
- [ ] Exercise cancellation and deadline during handoff; check frames and input inaccessible afterward. Verify files/evidence contain no sensitive manual values.
- [ ] Run `make check`, `make test`, isolated relevant Chromium E2E, and final independent review. Record exact passed/failed/not-run stages. Do not claim real website acceptance.
- [ ] Deliver milestone report and remaining limits; leave changes uncommitted for parent integration decision.

## Verification checkpoint (2026-11-10)

- Implemented durable controller/model fencing, same-session OpenCode continuation, authenticated panel gateway, ephemeral frames/input transport, and desktop/mobile control UI. Work remains uncommitted.
- `make check` passed, including backend 300 passed / 42 skipped; `make test` passed (460 AI backend, 83 AI frontend, 69 backend, 9 web). These gates precede the final recording-stop route and sensitivity-race fixes and must not substitute for their targeted acceptance.
- Real integrated UI/local Chromium journey passed after vault and frame-throttle changes: fill synthetic value 35, scroll, exactly one submit, then agent-active epoch 2. The workflow authorization query was synthetic; browser, HTTP gateway, SQLite, worker and UI transport were real.
- A separate real Chromium regression changes a text field to password after admission: input is rejected and absent from SQLite and artifacts. Independent review identified a narrower final-dispatch DOM race still under remediation. Security acceptance is not yet complete.
- LCA shared ordered receipt listing and atomic recording-stop watermark/drain are being integrated. No main/feature consolidation, production credential provisioning, real-site acceptance or paid model verification has occurred.


## Final AA milestone verification (2026-11-10)

- Both reviewed races are repaired: exact bound-element synchronous sensitivity/expiry checks at actuation, and worker authority revalidation after awaited input retrieval. Failed controlled writes latch blocked and cannot hand back or finalize a trace. Independent reviewer reran 17 Chromium cases and 2 real inspector integrations; no blocking finding remains in this bounded scope.
- Final `make contracts`, `make check` and `make test` pass. Check includes backend 306 passed / 44 skipped; test includes AI 460, frontend 83, backend 69 and web 9. Build emits an existing large-chunk warning.
- Final explicit owned Chromium core gates: 5 passed, including separate API/worker input vault and DOM sensitivity regression. Final delayed actual UI journey: 1 passed; focused UI: 23 passed plus type/lint. Pinned OpenCode 1.19.5 continuity/runtime: 72 passed with synthetic model transport. No paid calls.
- Actual migration 0009 upgrade/downgrade verified in isolated SQLite against metadata with preserved legacy rows. Shared receipt listing, immutable recording start and stop/drain watermark are tested.
- Controlled press, custom-select and nested-frame input remain unsupported. Native click and input/change semantics are deliberately bounded. Sensitive login remains unsupported; trace is only the last safe agent segment, not full interaction history.
- AA local live-watch/takeover/handback milestone is verified. Combined LCA recording/edit/replay integration, dirty-tree consolidation, production authorization deployment and real-site/model acceptance are not completed by these gates. No commits or merges made.

## Combined integration checkpoint (2026-11-10)

The isolated combined checkout now includes the LCA recorder and shared human native-select/explicit-navigation extensions. Original main/feature source files remain unchanged against the staging manifest. Independent reviews cleared shared runtime, recorder authorization/persistence, and UI recovery fixes.

The full rendered `/lca` journey uses the actual same-origin gateway and real owned Chromium: normal takeover, navigation/fill/select/single submit, in-flight stop/drain, explicit import and parameter/assertion editing, save, three fresh pass/fail/corrected-pass replays, reload/history and evidence download. It observed exactly four submissions across four isolated cookie contexts and three distinct snapshots/executions. Desktop and 390-pixel mobile recording/history layouts passed. The failure snapshot remained unchanged after the corrected run.

Real owned MySQL 8.4.6 verified online migrations through 0009/0010, reconnect persistence, a large draft, empty downgrade/reupgrade, and refusal of destructive populated downgrade. The owned test container/volume was removed. `scripts/verify-aa-lca-mysql.py` retains an explicitly opted-in reproduction harness.

Combined `make contracts`, `make check` (438 backend passes, 47 opt-in skips), `make test`, and configured web tests (285 passes) succeeded before the final display-only saved-notice wording correction. Final affected verification is recorded in the LCA validation guide. No production credentials, real external business website, paid model call, commit, merge or push is claimed. The combined checkout remains an uncommitted integration candidate for parent review.
