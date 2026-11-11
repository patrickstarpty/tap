# LCA BDD editor

The user approved a three-pane TAP editor on 2026-11-11: business BDD steps, ordered implementation actions, and properties of the selected action. This extends the existing product, not a separate prototype.

## Model and compatibility

Keep the canonical action list and stable action IDs. Add optional `bdd_steps` containing `id`, `keyword` (Given/When/Then/And/But), `description`, and ordered `action_ids`. When present, groups partition the action list exactly and preserve execution order. Old definitions retain identical canonical bytes until an explicit edit. Display a virtual default group for these assets. Saving edits adds grouping metadata without changing action IDs. Published snapshots remain immutable. Empty BDD containers may be saved during authoring, but generation/execution rejects them until implemented or removed.

## Interaction

The left pane selects and reorders business steps. The middle pane shows only that step's actions, supports add/remove/reorder/move-to-step, and clearly labels supported operations. The right pane edits the selected action's target, argument, timeout, and locator. Origin/parameter configuration stays in a separate disclosure. No fake Tap/Swipe or Robot executor support.

Recording import explicitly appends actions to a chosen BDD step, renames colliding IDs, merges targets/origins without losing other steps, and does not save or execute. Import refuses stale editor state and capacity overflow. Result groups derive from the frozen run snapshot, never the current draft.

## Evidence and acceptance

Tests cover legacy bytes, invalid group references/order, persistence and release snapshots, editor selection and ordering, recording merge, frozen result grouping, and unchanged save/replay safety. Browser acceptance exercises the real TAP route and normal backend, saves and reloads grouped flows, replays real FWD/AIA assets, and captures MP4 evidence after checks.

## Reference patterns

- [UiPath activity properties](https://docs.uipath.com/studio-web/automation-cloud/latest/user-guide/configuring-activities): activities and contextual properties.
- [UiPath recorder](https://docs.uipath.com/activities/other/latest/ui-automation/app-web-recorder): recording into a selected container.
- [BrowserStack editing](https://www.browserstack.com/docs/low-code-automation/test-recording/test-editing): step selection, ordering and editing.
- [Robot Framework BDD](https://docs.robotframework.org/docs/testcase_styles/bdd): business-readable steps implemented by lower-level keywords.

BDD grouping is TAP's chosen model, not a claim that the reference products have the same model.
