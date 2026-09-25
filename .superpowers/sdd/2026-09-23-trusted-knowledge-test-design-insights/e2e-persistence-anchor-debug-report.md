# Owned E2E persistence anchor verification

## Result and boundary

The final independent persistence verifier now accepts the approved document `inventoryItemId` and hashes it in the same canonical position as the browser. The guarded `make demo-e2e` run passed browser, application-restart, Compose-restart, and persistence phases. This is isolated fake-model validation; it does not establish real-model or real-reviewer acceptance.

## Root cause and scope

- The browser `canonicalAnchorHash` accepted `inventoryItemId` and serialized it after `headingPath`. The citation preview preserved the resolver-approved value, but Python `_canonical_anchor_hash` required the former six-field mapping and raised `citation-anchor-shape` before comparing hashes.
- The verifier now requires exactly the seven approved fields and includes `inventoryItemId` in that same JSON order. Unknown fields remain rejected; no product contract or retrieval authorization changed.

## RED, GREEN, and validation

1. Added a test with a fixed, independently calculated SHA-256 for an anchor containing `inventoryItemId: "item-approved-1"`. It also asserts that an additional unapproved field raises `citation-anchor-shape`. Before the fix, the targeted test failed with `VerificationFailure: citation-anchor-shape` (`1 failed`).
2. Added the approved field to the exact Python mapping and canonical JSON. The same targeted test passed (`1 passed`). The complete persistence test file passed locally (`28 passed, 1 skipped`); the skip is the selected live E2E verifier outside its owned phase. Ruff check, Ruff format check, and `git diff --check` passed.
3. The owned preflight passed with local Docker and fixed loopback ports. `TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e` then passed: browser journey **5 passed**, application restart **1 passed**, Compose restart **1 passed**, and final persistence verification **29 passed**. The live verifier's comparison against the browser-produced hash confirms cross-language parity. Final pytest emitted two existing Alembic `path_separator` deprecation warnings and no failures.
4. All four phase evidence files under `/private/tmp/tap-persistence-anchor-e2e-evidence/` report `cleanup: complete`. The runner used only the fixed `tap-tapper-e2e` Compose project, private ports, and owned volumes. No shared/default services or volumes were targeted. There was no new independent failure.

Task 0 real-input and Task 1 real-identity/two-person Gates remain pending external inputs as recorded in `progress.md`.
