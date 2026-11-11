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

## Reviewed requirements and edited flows

The import preserves the complete reviewed input contract from the hash-verified original bundle, separately from editable steps. The preview and workspace show the supplied value, expected observed value, attribute, business labels and optional scope. Old tasks without a structured reviewed contract are explicitly **legacy / unreviewed**; free-form goals never become a reviewed contract through model interpretation.

**Technical execution** and **Current requirements acceptance** are separate results. Requirements acceptance revalidates the original project/task/execution/bundle/input bindings and the final output’s exact source receipt for every required field (name, value, observation sequence, frame, document and unmodified source URL). It then checks the current frozen action, exact target and business-label guard, write/read ordering, and the current canonical observed output. Old AA proof alone, missing readbacks, unknown executions, edited values/targets, reordered or additional writes cannot pass. The original contract remains attached when an edited draft is saved, even if a client omits the metadata. Clients cannot replace it or promote an unreviewed source.

Current replay tracks real Playwright Frame objects and retained Document handles, not URL changes. Required readbacks are bound to the owning frame/document. A same-document click requires a fresh read of the frozen field before it can remain proven; a later confirmed transition in that same Frame can archive this fresh proof. Changed values, failed/raced rereads, a detached frame, an intervening write, or an unrelated later navigation invalidate it. Source snapshots after executable steps are retained as explicit read-only **Observe document state** actions, because an observation can establish a delayed document transition. Merely finishing the run cannot promote pending evidence.

The generated current result includes independent document-proof evidence. The service checks its current value, exact read step, live or archived document binding and error state in addition to the canonical step outputs and immutable original source. An old AA receipt or an old current-run output alone is insufficient. Sources without trustworthy frame/document identity cannot gain requirements acceptance, even if their technical replay succeeds.

Ordinary edits, saving and technical replay remain available. Publishing a reviewed flow whose requirements are not proven is blocked. This version does not reapprove a changed contract inside LCA: create a new AA task, review its required inputs in the actual browser-task editor, complete it, then import its result as a new LCA flow. Saving edits does not update the original approval.

## Full-product acceptance harness

Run `scripts/run-browser-core-e2e.py --mode synthetic --execution-mode local --opencode-binary <absolute-pinned-1.19.5-binary> --case form --lca-bridge`. This opt-in mode launches the real TAP API and TAP Web with independent LCA/control/worker test credentials, an owned synthetic site and a zero-provider-cost model fixture. It exercises the completed task's product link, preview, unsaved draft, explicit save and deterministic debug replay. It cannot be enabled for paid/real mode or the Docker-specific test worker.

The workflow event action ID is a logical call ID, not the browser action database ID. The bridge joins finished events through the unique server observation sequence in the verified session. Finalization's repeated read remains an extra executable assertion and may inherit only the earlier identical read contract's BDD group. A different selector, output name or assertion cannot inherit that mapping.

### Original technical acceptance (2026-11-11)

**The owned synthetic form path passed full product acceptance after Docker capacity recovery.** The user-visible journey ran through controlled AA success, **Review as LCA draft**, **Preview AA task**, **Use as unsaved draft**, **Save draft**, and **Debug replay**. The frozen version contains three BDD groups and six actual actions, including the final repeated read. All six canonical actions and all three BDD groups passed. Both named `result` values were `AC-42 / 120 / MATCH`; the original AA task's model-call counter did not increase during import/replay.

The real TAP screenshot was reviewed: saved version 1, editable BDD/action tree, passed business and canonical results, named output values and execution evidence downloads are visible. The accepted run ID is `08369d5df4d94fbba96ff8e008d21ebe`, with evidence captured by supervisor `tap-browser-core-f0b91a17fcc9`. Acceptance is limited to this owned synthetic scenario. It does not certify FWD/AIA or unrestricted arbitrary AA import, nor does it publish the imported asset automatically.

Two product-level issues surfaced beyond unit tests and were corrected:

1. Workflow logical call IDs differed from browser action-row IDs. The bridge now uses unique server observation sequences and preserves final identical read rechecks.
2. The existing canonical-result validator rejected the additional named read output generated by `assert_read`. It now requires and validates a bounded string output only for a passed, named read in the exact frozen definition, and the UI displays that validated value. Mismatched names, unsupported output fields, outputs on other operations or failed steps, and oversized/nonstring values remain invalid.

Earlier reruns were blocked by Docker VM disk exhaustion, not bypassed readiness checks. Only task-owned stopped containers were cleaned up; host database/evidence directories, volumes, shared images and active projects were preserved. After capacity recovery, a fresh session produced the accepted result above.

### Reviewed-contract acceptance

The updated synthetic form harness uses the real required-input editor and review checkbox before sending the AA task, then checks both technical replay and current requirements acceptance. Its product UI video is retained continuously in bridge mode, without desktop-recording permissions. The earlier six-action technical acceptance above is not evidence that the new reviewed-contract path has passed; the new run must finish all checks separately.

The current-document implementation has a separate local Chromium matrix for both main-frame and iframe flows: immediate/delayed navigation and same-document AJAX success, plus changed-input, detached-frame, intervening-write, passive-navigation and repeated-navigation failures. These verify generated replay and its backend evidence validator, not FWD/AIA site acceptance. The final full-product reviewed-contract recording remains a separate acceptance gate.

Source verification also rejects a missing or ambiguous final output, a mismatched provenance identity, and fallback to an earlier successful read after a later failed/unproven read of that name. An offline check of the earlier completed reviewed-form ledger found matching values, URLs and final read sequences after ordering the exported ledger by action sequence, but no document IDs; it correctly remains unproven under the new contract rather than being grandfathered into a pass. No stored URL or source digest was rewritten for this check.

## Start from the formal LCA page

Open Low Code Automation in TAP Web. Its Tapper panel is the real browser-task
composer. Enter the natural-language goal, start URL and any reviewed required
inputs, then **Send**. Tapper automatically plans BDD steps and executes AA. There
is no separate pre-execution BDD approval screen. The panel exposes the existing
workflow progress, BDD/actions, evidence and explicit cancel request.

For a successful live run, select **Review as LCA draft** in the panel. The host
stays on the same top-level page. Select **Preview AA task**, review source
provenance and projection issues, then **Use as unsaved draft**. Save, debug replay
and publication are still separate actions. Dirty drafts, recording and pending
publication prevent replacement. Hiding/opening Tapper preserves its conversation
and the current LCA draft; hiding does not cancel. AA frozen replay is not offered
inside this panel; the normal LCA replay is available after explicit import/save.

Configure `VITE_TAPPER_URL` on TAP Web and `VITE_TAP_WEB_URL` on Tapper to the exact
other loopback app origin, and point both API proxies at the same `TAPPER_API_PORT`.
The default origins are 127.0.0.1:5173 and 127.0.0.1:5174. An invalid URL,
unauthorized context, different gateway instance or disconnected API prevents
execution rather than falling back to another project. Reconnect refreshes the
parent context while retaining the LCA editor. A changed project/actor must be
resolved before continuing with its draft.

If a POST receipt was lost, **Recover original browser request** retries only the
session's exact frozen body and original key. That temporary session copy includes
the goal, notes and reviewed values, not transport credentials. Do not enter
passwords in task text or required inputs; recovery is not secret detection. Do not recreate it with a new goal
or key. A first explicit pre-admission rejection releases the failed intent; an
unknown outcome remains blocked, including when a later retry is denied. Accepted
receipts can be verified read-only after an API restart. Unknown receipts cannot
be automatically rebound to a new gateway incarnation: investigate task history
before starting a replacement. Blocked/corrupt session storage also prevents new
browser execution. This recovery is not unsaved-LCA autosave.

The updated `--lca-bridge` harness starts at this formal LCA page, submits within
the iframe and checks top-level URL stability, hide/show persistence, strict frame
policy, one AA admission and zero additional model calls for import/replay. Its
formal inline full-stack journey passed on `bc956669` with the synthetic provider:
one admission, 11/11 canonical actions, both current required fields passed, and
model count 11 → 11 through import/edited replay. See the [inline acceptance](2026-11-11-lca-inline-tapper-validation.md)
for exact source/run IDs, recording and failure fixes. The historical standalone
link acceptance above remains separate evidence; neither proves autonomous
FWD/AIA Qwen completion.
