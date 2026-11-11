# LCA page and file action validation

## Delivered behavior

The existing TAP BDD editor and deterministic Playwright runtime now support explicit page contexts, open/switch/close page, click-triggered popup capture, exact one-shot dialog handling, project file upload and captured download evidence. Together with the previous common actions and bounded read assertion, the action model contains 28 operations. New actions are editor-authored; recording still covers navigation, click, fill, native selection and explicit page/container scroll.

Page lifecycle is validated in execution order. A popup receives a fresh page ID; actions can address it explicitly or switch active page. Dialog handling requires an exact expected type/message and explicit accept/dismiss decision. Missing events, unexpected messages, closed pages and disallowed destinations fail with step attribution, not silent fallback.

Upload assets are immutable, scoped to project and actor, and referenced by opaque ID plus SHA-256. File bytes are frozen into execution inputs rather than fetched from a machine path on replay. Common filename suffixes provide a fixed MIME mapping; unknown suffixes use application/octet-stream. This is compatibility metadata, not content validation. Uploads are limited to 256,000 bytes and total frozen inputs to 1,000,000 bytes. Download evidence is limited to 5,000,000 bytes per captured file and served only through run-authorized attachment access, with a safe filename. Downloaded content is not opened or executed.

The download limit bounds captured evidence and application memory. Playwright can first spool native downloads into its managed temporary directory; this is not a native transfer byte quota. Existing execution deadlines, isolation and network policy remain in effect.

## Evidence

- Existing canonical definitions and generated scripts retain identical bytes when the new optional context fields are omitted.
- Unit and actual Chromium tests cover ordered lifecycle, independent pages, delayed about:blank popup navigation, exact confirm/prompt/alert handling, unexpected-close failure and listener cleanup.
- Actual TAP UI edited prompt text and its assertion, saved/reopened the flow and passed all 15 page/dialog steps. Flow `d1b4548c1410462aa0ee49593df00338`, run `43cc6d95cfe14b0a8af3b0ef37d1dc6d`.
- Service → original browser worker → Chromium tests verify file type/bytes, bounded download evidence, immutable published replay after a draft file replacement, oversized capture rejection and no-download timeout.
- Actual TAP UI selected and uploaded a synthetic text file, bound its digest, saved/reopened and replayed **4/4**. Downloaded `fixture-result.txt` retained its filename and exact bytes. Flow `97ab5c47dcb64cb593eef52463aef364`, run `9e03ef8e503244e3a26356b69d8d473c`. Publishing v2, editing v3 and replaying the published v2 passed **4/4**, run `7387ba577fcd44b688319c55d4107dd7`.
- Independent review found and resolved download-export allowlist omission, MIME loss, input-serialization boundary mismatch, late upload response binding across flows/reloads, and loss of safe download filename. Targeted regressions validate each fix.

Controlled fixtures contain only synthetic data. They are not FWD/AIA quotations. Public-site evidence from the previous batch is described separately in [Web action validation](2026-11-11-lca-web-actions-validation.md).

## Local regression gates

API contracts were regenerated. The exact API-surface regression deliberately includes only the two new asset endpoints. The final affected backend gate passed 624 tests (80 intentionally skipped); core `make test` passed 462 AI-backend, 83 AI-frontend, 70 TAP-backend and 9 TAP-frontend tests. The complete TAP Web source suite passed 370 tests. The final AA inspector regression passed 16 tests. All `make check` targets passed across the initial run and the corrected backend/remainder reruns. Tests use explicit source paths so a shared editable environment cannot silently import another worktree. Controlled quality-fixture gates are not live-model performance claims.

## Persistence environment limit

Two isolated product starts successfully applied the real MySQL migrations through `0012_lca_assets`; asset creation, save/reopen and published replay used that durable SQL backend. Additional standalone migration and database-restart retention verification was blocked by Docker storage exhaustion (`No space left on device`), first on a new owned container and then on the owned host-bind container hosts file. The latter container exited; its host-backed database files and evidence were preserved. A proposed separate-schema retry stopped before database access because the container was no longer running. This batch does not claim that restart or populated-downgrade verification passed. SQLite migration/retention tests passed; repeat the MySQL retention gate after the environment can write again. No shared data was deleted or protection disabled to clear the blocker.

## Remaining boundaries

This is not universal browser support or complete UiPath/BrowserStack parity. Recording all new operations, nested iframe chains, cross-frame drag, mobile touch gestures, unrestricted large-file workflows, production SSO/team sharing and scheduling are not claimed. Existing full-stack document-parser startup issues are separate from the isolated product-component acceptance. Remote CI is not represented as passed by local validation.
