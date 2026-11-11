# LCA BDD editor validation

## Delivered behavior

The existing TAP Low Code Automation entry now uses three panes: BDD business steps, ordered actions for the selected step, and contextual action properties. Business steps and actions can be reordered; actions can move between steps. Origins, shared targets and parameters remain available under flow settings. Saving remains explicit. Recording appends into the selected BDD step without replacing unrelated actions. Imported ID collisions are remapped.

The optional `bdd_steps` metadata is persisted with the canonical definition. Its action IDs must form an exact ordered partition of the executor's flat actions. Legacy assets are readable without changing their bytes; explicit edits materialize their default BDD grouping. Empty BDD containers can be saved while authoring, but generation/replay rejects them rather than falsely implying that an unimplemented business step passed. Run summaries use the frozen snapshot, not the current draft.

This follows the approved composition pattern inspired by UiPath activities/properties, BrowserStack step editing, and Robot Framework's high-level keywords. It does not introduce a Robot Framework runner, mobile Tap/Swipe, or full feature parity with either reference product.

## Real product acceptance

All UI operations below used the normal TAP application started by `scripts/run-tapper-dev.sh`, with the existing product navigation and same-origin API. Test databases and ports were isolated. No personal information or insurance purchase was submitted.

- **FWD:** newly recorded navigation was appended into `Given Open the FWD quotation page`; the temporary initial action was removed. Two editor-created clicks were grouped under `When Choose age 30 and the Standard plan`, with two quotation assertions under `Then Verify the monthly quotation`. Group reorder, save, product navigation, browser reload and reopen preserved all five actions. Independent replay passed **5/5**. Flow `34d013d1bd1049e8816be7198f42cd89`, run `15ec265c8fb544a4af7845db70a89628`.
- **AIA:** the previously recorded 12-action asset was imported, not newly recorded. Through the product editor it was grouped as Given/When/Then, saved and reopened. All original actions and their order were unchanged. Independent replay passed **12/12**. Flow `162d6291bf7d4d83a07cd8e639a26d02`, run `0773b96126ae4cda8d7a373cb315cb5c`. Saving and grouping produced zero executions; explicit replay produced one.
- **Published version:** FWD v2 was published, then the draft's BDD description was changed and saved as v3. Published v2 replay passed **5/5**, retaining v2's original BDD descriptions and digest. Publishing, editing and review did not execute; confirmation produced one execution. Run `69db66cb2dcd4ebca333210ebe214c86`.
- Desktop and narrow mobile layouts were visually inspected. The editor remains in the TAP shell, with stacked panes at narrow widths.
- Three continuous browser recordings were converted without cuts to H.264 Main/yuv420p MP4 with faststart. Full decoding and exact duration checks passed: FWD 55.84 seconds, AIA 30.48 seconds, published replay 25.28 seconds.

## Persistence and checks

Docker's existing virtual disk was full. No old containers, volumes or user data were deleted to free space. The successful product acceptance used a new host-directory-backed MySQL 8.4.6 database. A separate focused test restarted a host-directory-backed MySQL instance and verified byte-identical grouped draft and immutable snapshot data afterward.

An earlier whole-stack database-restart attempt caused the existing suggestion worker/supervisor to exit on database loss; it did not establish automatic application recovery. The focused restart test proves LCA SQL persistence, not full supervisor crash recovery. Temporary test containers were ownership-checked before removal; host-directory test data and acceptance evidence were retained.

Validation includes backend grouping, exact partition/order rejection, legacy canonical-byte compatibility, recorder projection compatibility, immutable publication, empty-group execution rejection, target/ID collisions, parameter conflicts, BDD selection/movement, explicit-save behavior and frozen result summaries. Independent review found a legacy settings-edit grouping omission; it was fixed with a failing-then-passing regression.

The scope does not close production SSO, team sharing, scheduled suites, or the complete AA programme. FWD's live custom-dropdown recorder limitation still uses editor-authored actions. Remote CI is not asserted as passed. The original `tap` work directory is preserved; this work is integrated in the dedicated checkout and development branch.

Final local gates: `make check` passed (TAP backend 493 passed, 47 intentionally skipped); TAP Web full suite passed 329 tests; `make test` core regressions passed (461 backend core, 83 frontend core, 70 Insights backend and 9 Insights frontend). The focused LCA backend suite passed 155 tests. `git diff --check` passed. These are local results, not a claim about remote CI.
