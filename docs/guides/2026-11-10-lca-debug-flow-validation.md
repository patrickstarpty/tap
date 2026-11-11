# LCA debug-flow validation checkpoint

The approved owned-site LCA debug-flow milestone passed in the isolated combined checkout. This is validation evidence, not a production deployment or branch integration claim.

## Implemented and verified

- Canonical six-operation validation and deterministic `flow.spec.ts` generation, with explicit canonical result attachments.
- Owner/project-scoped draft versions, frozen snapshots, idempotent run intent and recoverable browser execution linkage.
- Shared browser-control-v1 recording with owner-bound session context, terminal receipt projection, fixed stop/cancel watermark and excluded unknown/failed actions.
- Authorized HTTP adapter, server-to-server delegated actor authentication, same-origin TAP AI gateway and verified project context.
- `/lca` editor/history entry: parameters, explicit assertions, optimistic saves, frozen results, cancellation requests and authorized evidence download. Simulation remains separate.
- An isolated real Chromium test executed generated form automation successfully, then deliberately failed its assertion. Both produced real screenshot, trace and result evidence; the failed assertion retained its canonical step ID.

## Commands

```sh
uv run --project apps/backend pytest -q apps/backend/tests/unit/test_lca_*.py apps/backend/tests/contract/test_lca_api.py
uv run --project apps/tap-ai-backend pytest -q apps/tap-ai-backend/tests/contract/test_lca_gateway.py
corepack pnpm --dir apps/web exec vitest run src/LcaApplication.test.tsx src/features/lca --maxWorkers=2
make tap-backend-check
```

Build a separate version-matched image so AA development images are not overwritten:

```sh
docker build --pull=false -t tap-lca-browser:20261110-develop deploy/browser
TAP_RUN_LCA_BROWSER=1 uv run --project apps/backend pytest -q apps/backend/tests/integration/test_lca_real_browser.py
```

The browser test creates uniquely named owned fixture/runtime containers and cleans its own resources. It uses synthetic non-secret data and no model calls. Without opt-in it skips; a skip is not browser acceptance. `TAP_LCA_BROWSER_IMAGE` can select an already verified matching image.

## Gateway configuration boundary

No new credentials were provisioned. Both trusted server processes require the same dedicated `TAP_LCA_GATEWAY_TOKEN`; never put it in frontend configuration. TAP AI additionally uses `TAP_LCA_BACKEND_URL` to reach the TAP non-AI backend. That backend requires the existing complete browser authority configuration and the LCA migration before use. Missing gateway configuration returns unavailable; it does not create a demo principal. Both context and proxy require the trusted local validation identity mode; production knowledge permissions do not confer browser execution permission.

The Web dev proxy routes LCA requests to the loopback TAP AI API at `TAPPER_API_PORT` (default 8000), while other TAP APIs retain their existing target. The TAP AI origin allowlist must explicitly permit the Web application's origin; this change does not widen it automatically. Project/actor comes from the existing trusted scope provider. Do not trust a caller's actor header, URL or JSON body as identity.

## Accepted scope and remaining boundaries

The owned-site recording/editing/replay milestone passed. Production authentication, credential-bearing websites, formal published test assets, Test Plan integration and arbitrary-site compatibility remain outside this milestone. No production website, paid model, deployment, commit, merge or push is claimed. Original worktrees remain preserved.

## Review and recovery checks

An unresolved persisted run can be explicitly reconciled by its run ID after a page reload. Reconciliation reuses its original frozen request and execution key. Reads never dispatch, and the editor blocks fresh replay until unresolved history is reconciled. Transient history polling errors retain the last receipt and explicitly pause until Refresh.

## Combined-tree real recording acceptance

The isolated combined checkout includes the shared controller and migration 0010. No original worktree has been replaced, committed or pushed.

```sh
TAP_RUN_BROWSER_LOCAL_INTEGRATION=1 .venv/bin/python -m pytest -q apps/backend/tests/integration/test_lca_real_recording.py -x
```

Passed one real Chromium test in 10.80 seconds: navigation, fill, native select and one submit; duplicate command suppression; stop while submit is in flight; settled canonical import; parameter editing; no submit on save/generate; three explicit fresh-cookie-context replays (pass, deliberate canonical assertion failure, corrected pass). The earlier failed snapshot and evidence remained unchanged. Real PNG/trace/result hashes, owner restrictions and reconstructed application history were checked. This harness uses isolated SQLite and owned loopback pages, not a production website or paid model.

Cancellation closes admission and freezes the recording interval. The UI reports cancellation as terminal only after the runtime acknowledges it, preserves unknown outcomes separately and does not automatically import a canceled projection. Access renewal revokes the previous panel grant while retaining its owner-verified controller identity; it never retries an uncertain action.

## Final rendered UI and persistence gates

The full rendered `/lca` journey passed through the real same-origin TAP AI gateway, shared BrowserService/worker and Chromium. The final fresh run passed one test in 20.61 seconds using the normal Take control path, without a manual renewal workaround.

```sh
TAP_RUN_LCA_RECORDING_UI=1 .venv/bin/python -m pytest -q apps/tap-ai-backend/tests/integration/test_lca_recording_ui.py
```

The test records navigation, fill, native select and one submit; stops and drains the admitted action; imports into the editor; edits a parameter and assertion; saves; executes three fresh-context runs (pass, deliberate assertion failure, corrected pass); and reloads frozen history/evidence. It verifies four total submits across four isolated cookie contexts, with three snapshots/executions. The recording view and failed-run history fit a 390-pixel mobile viewport without horizontal overflow.

The real MySQL 8.4.6 gate in `scripts/verify-aa-lca-mysql.py` passed migration 0009 → 0010, all six LCA tables, empty downgrade/reupgrade, a 70,023-byte draft and owner/snapshot/run/recording persistence after reconnect. A populated downgrade refused before dropping data or changing the revision. The harness removed its own temporary container and volume. Its opt-in invocation requires an explicitly approved existing network; it never loads a user `.env` or targets a shared database.

Final combined gates reported: `make check` passed (438 backend tests, 47 explicit skips); `make test` passed its 460/83/69/9 test groups; the configured full Web suite passed 285 tests. Skips are not counted as live acceptance. Independent review covered the shared runtime, gateway and recorder UI; the live takeover race found during UI acceptance was fixed by preserving the original capability and waiting for confirmed control, then the normal path was rerun successfully.

## Repeated-recording lifecycle compatibility

New LCA recordings carry a server-only cleanup marker. Stop first fences new actions and drains the admitted interval; the recording remains `draining` until the runtime confirms browser teardown. Only then does it report `stopped`, and a fresh recording of the same saved flow may start. Recorded receipts, evidence and the frozen projection remain unchanged. Uncertain teardown remains unknown and is not retried automatically. Generic AA stop/control behavior is unchanged.

This is migration-free. Existing recordings without the marker require explicit cancellation and confirmed teardown before another recording starts. Reads neither backfill the marker nor silently close old sessions. Simultaneous first-recording creation across processes remains a separate serialization limitation; the runtime fails closed rather than reusing an active inspector.

The focused lifecycle regression passed 121 tests. The real owned-page regression passed in 9.84 seconds, including a second same-flow recording and navigation after stop/save/replay, without a manual cancellation workaround. The first recording's projection stayed unchanged. These checks do not establish FWD or AIA quotation acceptance.
