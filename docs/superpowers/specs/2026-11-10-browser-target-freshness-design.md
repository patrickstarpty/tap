# Bounded selector freshness for readonly comboboxes

## Scope and evidence

FWD's public quote page has a ticking promotion outside its age picker. A fresh explicit-selector click was rejected with `frame_revision_changed`. Removing whole-document validation globally is not acceptable.

The first implementation supports only a main-frame, nonsensitive, native readonly input with `role="combobox"`, no owning form, one explicit `aria-controls` reference to a unique listbox, and that listbox's complete `aria-labelledby` dependency. Its descendant `role="option"` elements share this proof. The input's own accessible name is preserved, including an empty name; the listbox label is not invented as an input label. No association is inferred from ID prefixes, CSS classes, neighboring text or site identity. The unassociated FWD Toggle suggestions button remains unsupported by this path.

Saved official HTML contains the unique controller, hidden listbox and Age label in the closed state. Saved component code sets `openOnClick:true`, toggles controlled open state on input click, and transitions `INPUT.CLICK` from idle/focused to open. This supports an owned matching fixture and a later normal input-click acceptance path, not a fabricated association.

Only human explicit-selector clicks use this path. Fill/select/press, coordinates, navigation, window scrolling, buttons, forms, missing/ambiguous relationships and incomplete/oversized contexts retain existing whole-document validation. A failed supported proof is rejected, never silently downgraded to another selector or reacquired target. No origin, ownership, receipt or retry policy changes.

## Private frame proof

Keep the public frame payload, broker and five-second lifetime unchanged. `live-control.mjs` owns private frame proof objects and exact retained node references. Nothing is placed on a page global or returned in frame JSON, persisted evidence or diagnostics.

Capture one shared group baseline: exact controller/listbox/label/option references and parent chains; tag, type, native/declared role; explicit name/description sources (including external and hidden referenced text); readonly/disabled/inert/sensitive classification; controller value; option order, text, declared value and selected/disabled state; relevant ARIA ownership references; computed visibility, transforms, clipping ancestors, client rectangles and ancestor scroll positions. Any protected dependency disqualifies the group before its value/text is collected. Missing referenced nodes, cycles or unsupported naming/context dependencies use strict validation.

Limits: 128 proof targets, 128 KiB per shared group and 512 KiB per frame; eight frames maximum. Limits fail closed, never truncate a proof into acceptance. Release private observers and handles on five-second expiry, eviction, action consumption, control change, navigation failure and teardown. An in-flight action retains only its own binding.

## Capture and admission

Install dependency-scoped MutationObservers before the baseline and retain them until disposal. Drain queued mutation records synchronously at admission and the final guard. Watch group/reference subtrees and relevant ancestor attributes/child-list changes. Mutate-and-revert of a dependency invalidates the proof even if endpoint samples match. Observe dependency layout changes and reject active dependency animations or otherwise unsupported style/layout sampling. Global stylesheet changes invalidate proofs conservatively. Capture masked screenshots with the original caret state to avoid injecting input caret styles. Compare dependency facts before and after capture; only a complete, clean proof may be used.

Admission still verifies frame age, document identity, exact URL, viewport, epoch, authority expiry and captured scroll context. Resolve the explicit selector once and require the same captured node. Compare all proof dependencies before any mutation. Only unrelated changes outside those dependencies may be ignored.

For a captured, clipped listbox option, the existing nearest/instant scroll acts only on that retained node. After the bounded scheduling boundary, permit geometry translation explained solely by recorded ancestor scroll deltas; reject reparenting, reflow, semantic changes, sensitivity changes, expiry and overlays. Final hit testing, guarded focus and click remain synchronous. No input is retried. Existing select-redraw acknowledgement and fill protections remain unchanged.

The proof stays clean through admission. After admitted native focus, only `data-focus` presentation attributes on captured dependencies may change. `aria-expanded`, listbox visibility, controller value, names/descriptions, option meaning/order, ownership, sensitivity and the exact target must remain unchanged until the click. Focus-driven popup opening remains on strict validation in this first implementation.

## Verification and review

Focused TDD: unrelated countdown accepted; target replacement, value/type/name/description/label changes, option reordering, ownership changes, protected dependencies, overlay, document swap and expiry rejected. Test dependency mutation/revert during capture and after capture, reflow versus authorized nested scrolling, malformed/missing ARIA strict fallback, coordinate/targetless strict fallback, cap overflow, expiry/teardown disposal and no duplicate dispatch after unknown.

Use owned pages only until independent security/code review. Run one full runtime aggregate after the implementation freezes, then real acceptance under the parent task's existing authorization. The native capture fence closes the demonstrated script-driven screenshot sampling race; unsupported capture contexts retain strict validation. No site-specific countdown exemption or broader automatic retry is introduced.

## Reviewed capture fence correction

Owned adversarial tests demonstrated that endpoint equality cannot prove correspondence when a silent `input.value` mutation changes the screenshot and is restored afterward. Mutation observers alone do not cover this property. The relaxed path is not cleared for live use until this gap is closed.

The correction keeps the entire private proof baseline and a whole-document revision in one synchronous evaluation. A temporary native CDP pause inside an inspector-owned empty script then prevents main-frame script execution while the paused whole-page value/DOM signature and bounded dependency geometry/computed appearance are compared to that baseline, a native screenshot is captured, and both are compared again. Unique dependency paths are fixed, JSON-quoted read-only data; original handles and queued mutation records are verified after resume before publication. Pausing only the inspector-owned script avoids suspending a site layout callback midway through rendering. Resume and debugger disable run in `finally`, with bounded waits; uncertain restoration blocks the runtime. No page globals, setter interception, page-content mutation or persistent browser setting is introduced.

This path requires one main frame, no protected elements, settled fonts and no active document animations or moving dependency media. All image readiness/source/natural-size states are included in the paused signature; lazy images outside the dependency region may remain pending, but any transition during capture rejects it. Otherwise it retains strict masked frame validation. Stylesheet events remain fenced, exact geometry and sensitivity are rechecked after resume, and the public frame/receipt contracts do not change. A failed admitted proof is rejected, not downgraded. The fence protects screenshot correspondence; it does not claim that unobservable property ABA after capture is tracked. Final value, identity, semantics, geometry and hit testing must still match at dispatch.
