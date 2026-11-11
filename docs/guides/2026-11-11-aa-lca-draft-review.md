# Review an AA result as an LCA draft

A passed live Agentic Automation task can be opened using **Review as LCA draft** in the task result, or by entering its task ID in the TAP LCA workspace's **Import an Agentic Automation result** section. Preview is read-only. **Use as unsaved draft** loads the projected actions into the existing BDD editor; saving, running and publishing remain separate explicit actions.

The gateway obtains the project-scoped task and its original execution, bundle, input digest and BDD action mapping. The browser service checks those bindings against its own stored execution/session/bundle, checks completion result and evidence, and requires a contiguous source action history. It projects actual successful action receipts, never generated script text or a caller-provided action list. Original source IDs and BDD expectations remain in step provenance; original scripts and evidence stay in the AA task.

## Projection rules

- Preserve source execution order, including repeated nonconsecutive BDD groups.
- Navigation, clicks, fill and native select can become editable steps. A server-confirmed controlled-action receipt supplies the semantic target and safe acknowledged input; input vault references are never decrypted by this feature.
- Uncontrolled native-select labels require observed options and the resulting canonical option value. Custom widgets without that evidence require review rather than being silently converted to native selects.
- Read assertions retain their attribute, output name and all original constraints through `assert_read`. Unsupported attributes or patterns cannot be weakened into existence checks.
- CSS and server-observed replacements for ephemeral references require target review. Ambiguous references, protected inputs, failed/unknown actions, human interventions, unsupported actions and missing business-step mappings produce explicit issues.
- Any issue prevents creation of an executable definition. The user can still inspect the issue report and source task; no partial executable flow silently omits an operation.

## Acceptance boundary

Unit and UI-component tests establish source authorization, loss-aware projection and explicit import behavior. A passed original AA task does not certify a newly edited LCA flow. Product acceptance requires loading a real completed task, reviewing the imported BDD/action tree, saving it, running its generated script, checking assertions and named outputs, and reviewing the run evidence. A missing real successful source task must not be replaced by a claim of end-to-end acceptance.

## Full-product acceptance harness

Run `scripts/run-browser-core-e2e.py --mode synthetic --execution-mode local --opencode-binary <absolute-pinned-1.19.5-binary> --case form --lca-bridge`. This opt-in mode launches the real TAP API and TAP Web with independent LCA/control/worker test credentials, an owned synthetic site and a zero-provider-cost model fixture. It exercises the completed task's product link, preview, unsaved draft, explicit save and deterministic debug replay. It cannot be enabled for paid/real mode or the Docker-specific test worker.

The workflow event action ID is a logical call ID, not the browser action database ID. The bridge joins finished events through the unique server observation sequence in the verified session. Finalization's repeated read remains an extra executable assertion and may inherit only the earlier identical read contract's BDD group. A different selector, output name or assertion cannot inherit that mapping.

### Acceptance status (2026-11-11)

The real product controlled-AA synthetic form task completed successfully and its **Review as LCA draft** link opened the TAP editor. The initial preview correctly refused import because logical call IDs did not match browser-row IDs. After fixing that join, offline projection of that exact stored successful task and receipt ledger produced all six actions, including both read checks, with no issues. The regression suite covers the corrected mapping and refuses a changed read contract.

A fresh full-product save/replay rerun was blocked before application startup by Docker VM disk exhaustion (`ENOSPC`, VM filesystem 95 GB used of 95 GB). **The imported-flow save and replay path is not yet accepted end to end.** The harness and deterministic unit/browser tests are not substitutes for that remaining product acceptance. Only stopped containers owned by this task were removed; host database/evidence directories, volumes, shared images and active projects were preserved.
