# LCA Web action expansion

## Delivered scope

The TAP editor and deterministic Playwright generator now support 20 operations. Existing navigate, click, fill, native select, text assertion and URL assertion remain compatible. New operations are page/container scroll, scroll into view, key press, hover, double click, right click, check, uncheck, drag and drop, element-state wait, visibility assertion, input-value assertion, checked-state assertion and exact rendered-text assertion.

Targets can reference one top-level iframe target. Nested frame chains and cross-frame drag are rejected rather than silently targeting the wrong document. Exact rendered-text assertions use `innerText()` without whitespace normalization; existing `assert_text` keeps its original Playwright semantics. Arguments are strictly typed, parameter values are revalidated at execution time, and scroll distances are bounded. No user-supplied JavaScript or host file paths are accepted.

The existing BDD editor supports operation-specific fields and reference-aware target management. Invalid numeric edits are retained as invalid input, so saving cannot silently persist the previous valid value. Recorded-import ID remapping includes frame and drag-destination references.

## Recording boundary

Recording supports navigation, click, fill, native select and now explicit page/container scroll. Scroll receipts identify their scope; old receipts without scope remain excluded rather than being misinterpreted as container scrolling. Other new operations are authored in the editor and replayed; this is not a claim that every new operation can be recorded through live control.

Live control continues to enforce actor, frame, deadline, sensitive-target and bound-node checks. FWD's changing page rejected two attempted live scroll recordings with `frame_revision_changed`. They were not automatically retried in the same uncertain session and the safety boundary was not weakened. The supported editor fallback is evaluated separately.

## Validation

- Unit tests cover strict arguments, legacy canonical-byte compatibility, parameter validation, iframe references, cross-frame drag rejection, exact text semantics and scroll receipt provenance.
- The generated scripts execute in real Chromium against an owned fixture, including positive execution and deliberate assertion failure with the failed action correctly identified.
- The real TAP product UI saved, reopened and executed a 23-action owned fixture successfully. This includes keyboard, hover, double/right click, checkbox state, drag/drop, page/container scroll, iframe input and assertions. Flow `e4ab9322befb49f5be6873b2bfa4fb48`, run `668f0d1343fa4b08b51e99415f8505ed`.
- Real recording of page and container scroll, import into a BDD group, SQL save and independent replay passed **4/4**. Flow `e11941e394044da88552d2f4ea6f07e4`, run `3e49079b86a84146877e200ea4f5345b`.
- FWD navigation was recorded; scroll, product-selection clicks and quotation assertions were authored in the TAP editor. Save, reopen and independent replay passed **6/6**, flow `d2c1672a4b45405e9917b9c622c0478d`, run `a244519f975d49d689155b027f5cfcac`. This does not erase the failed direct live-scroll attempts.
- AIA's existing recorded asset, not a new recording, was regrouped/edited, saved and replayed **12/12**, run `f8f22e6c94904a6baaeb4c34c16e3c35`.
- Publishing the six-action FWD v2, editing draft v3 and explicitly replaying v2 passed **6/6**, run `6dffd6608f06470597ee63a3e3696e9c`. No save, publish or review operation executed the flow.
- After restarting the owned fixture MySQL, all four flow rows and two immutable snapshots had identical canonical-byte hashes. This proves persistence, not automatic recovery of the entire application stack.

Initial local aggregate `make check` passed. After adding exact-text semantics, the affected backend gate and TAP Web full suite were rerun; the frontend passed 338 tests. Core `make test` passed (461 backend, 83 frontend, 70 Insights backend and 9 Insights frontend). Browser live-control safety tests and targeted new-action/legacy-scroll regressions passed. These are local checks; remote CI is not claimed as passed.

The product acceptance uses the standard TAP backend, existing gateway, original browser worker and TAP Web against isolated persistent MySQL. It does not use a replacement editor or custom route implementation. Full `run-tapper-dev.sh` startup was attempted but was blocked by existing test Redis disk-full write protection and then document-parser lifecycle state. Existing data and protection settings were preserved; isolated Redis was used. Consequently this batch does not claim full-stack startup recovery.

## Remaining work

This is a common-action expansion, not universal Web or reference-product parity. Popup/multi-page context, safe project-scoped upload/download assets, dialogs, mobile touch gestures, nested iframes and recording of all new actions are still open. The safe next design uses explicit page IDs and opaque project-owned attachment IDs with frozen bounded bytes, not arbitrary machine paths. AA autonomy, SSO, sharing and scheduling are separate acceptance items.
